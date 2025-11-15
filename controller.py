# -*- coding: utf-8 -*-
import os, time, logging, json
import datetime
from collections import deque
from typing import Deque, Dict, Any, List

from utils_common import now, ensure_dir, jst_iso, tick_size
from telemetry import Telemetry
from broker import Broker
from kabus_api import KabusAPI
from model_registry import ModelRegistry

# 学習・メタ層
from entry_decider import EntryDecider
from exit_decider import ExitDecider
from risk_sizer import RiskSizer
from symbol_gate import SymbolGate

# Lv7.3: 戦略分離用
from strategy_router import StrategyRouter, Context

# Lv7.3: オンライン学習（戦略別・pnl_ticks）
from train_policy_online import update_after_trade

# ===== パラメータ（初期値） =====
SCALP = dict(
    tp_ticks=1,
    sl_ticks=1,
    max_hold_sec=25,
    max_spread_ticks=3,
    heat_entry=0.64,
    exit_drop=0.45
)

SWING = dict(
    tp_ticks_cap=4,        # 上限TP
    sl_ticks_cap=2,        # 上限SL
    max_hold_sec=300,      # 2〜5分
    max_spread_ticks=2,
    heat_exit_drop=0.40,
    obi_flip=0.10,
    vwap_distance_max=0.35  # %
)

# ルーター閾値（初期）
ROUTER_PREF = dict(
    heat=0.72,
    vwap_distance=0.25,   # %
    spread_ticks=2,
    vol_boost_ratio=1.5,
    market_temp=0.60
)
ROUTER_FORBID = dict(
    spread_ticks=6,
    vol_rank=0.10,
    price_step=5
)

LOOP_SLEEP_SEC  = 0.05
FAST_CHECK_SEC  = 0.3
VOL_MED_FACTOR  = 0.4
MOMENTUM_DELTA  = 0.5
OBI_BE_THRESH   = 0.48


def _hhmm() -> str:
    return now().strftime("%H:%M")


def _time_ge(target_hhmm: str) -> bool:
    return _hhmm() >= str(target_hhmm)


def _regime_now() -> str:
    """
    簡易レジーム判定:
      - 09:00〜09:10 くらいまで: OPEN
      - 〜12:30       : AM
      - それ以降      : PM
    """
    h = _hhmm()
    if h < "09:10":
        return "OPEN"
    if h < "12:30":
        return "AM"
    return "PM"


class Controller:
    def __init__(self, cfg):
        self.cfg = cfg
        self.api = KabusAPI(cfg)
        self.brk = Broker(self.api, cfg)

        maxn = max(1, int(getattr(cfg, "max_concurrent_symbols", len(cfg.symbols) or 1)))
        self.syms = cfg.symbols[:maxn]

        # ローリングバッファ（価格の短期差分用）
        self.buf: Dict[str, Deque[float]] = {s.code: deque(maxlen=5) for s in self.syms}

        # テレメトリ・モデル
        self.tele = Telemetry(cfg.telemetry_path)
        self.models = ModelRegistry()
        self.models.load_all(self.syms)
        self._last_tele_write = 0.0

        # 時間・QPS制御
        self._next_board_at = {str(s.code): 0.0 for s in self.syms}
        self._backoff      = {str(s.code): 0.0 for s in self.syms}
        self._board_min_iv = max(0.2, float(getattr(getattr(self.cfg, "safety", object()), "board_min_interval_ms", 900)) / 1000.0)
        self._qps_limit    = int(getattr(getattr(self.cfg, "safety", object()), "api_qps_limit", 6))
        self._rr_i = 0

        # Gatekeeper
        gk = getattr(self.cfg, "gatekeeper", None)
        if gk and getattr(gk, "allowed_symbols", None):
            self.allowed_symbols = {str(x) for x in gk.allowed_symbols}
        else:
            self.allowed_symbols = {str(s.code) for s in self.syms}
        self.gk_enabled = bool(getattr(gk, "enabled", True))

        # Rotation（5分統計を持つ）
        self.rot_cfg = getattr(self.cfg, "rotation", None)
        self.rotation_enabled = bool(getattr(self.rot_cfg, "enabled", False))
        self.rotation_last_ts = 0.0

        # 秒統計（直近5分＝300サンプル）＋特徴量の素材
        self.hist: Dict[str, Dict[str, Any]] = {}
        for s in self.syms:
            self.hist[str(s.code)] = {
                "spr_ticks": deque(maxlen=300),
                "vol_1s":   deque(maxlen=300),
                "obi":      deque(maxlen=300),
                "px_1s":    deque(maxlen=10),
                "last_cumvol": None,
                "last_ts": 0.0,
                "pv": 0.0, "vv": 0.0,      # VWAP用
                "vol_min": 1e18, "vol_max": 0.0,  # 日内ボリューム正規化
            }

        # クールダウン・連敗・日次
        self._cooldown_until: Dict[str, float] = {}
        self._cooldown_after_exit_sec = float(getattr(getattr(self.cfg, "safety", object()), "cooldown_after_exit_sec", 2.0))
        self._streak: Dict[str, int] = {s.code: 0 for s in self.syms}
        self._streak_pause_sec = float(getattr(getattr(self.cfg, "safety", object()), "streak_pause_sec", 60.0))
        self._daily_pnl = 0.0
        self._daily_stop = float(getattr(getattr(self.cfg, "risk", object()), "max_daily_loss_jpy", 10000))

        # 学習層・メタ層
        self.entry_decider = EntryDecider(model_path="model_registry.json")
        self.exit_decider  = ExitDecider(model_path="model_registry.json")
        self.risk_sizer    = RiskSizer(model_path="model_registry.json", use_bandit=False)
        self.symbol_gate   = SymbolGate(model_path="model_registry.json")

        # 7.3 ルーター
        self.router = StrategyRouter({
            "router": {
                "prefer_swing_if": ROUTER_PREF,
                "forbid_entry_if": ROUTER_FORBID
            }
        })

    # ---- trades_*.jsonl 出力（KPI集計用）----
    def _append_trade_log(self, rec: dict):
        # rec: {"ts","code","strategy","reason","pnl_ticks","pnl_jpy","slippage_ticks"}
        os.makedirs("logs", exist_ok=True)
        ymd = datetime.datetime.now().strftime("%Y%m%d")
        path = os.path.join("logs", f"trades_{ymd}.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logging.warning(f"[TRADES_LOG] write failed: {e}")

    # ------- ガード -------
    def _can_new_entry_now(self) -> bool:
        naa = getattr(getattr(self.cfg, "risk", object()), "no_new_entry_after", None)
        return not (naa and _time_ge(naa))

    def _force_close_due(self) -> bool:
        fct = getattr(getattr(self.cfg, "risk", object()), "force_close_time", None)
        return bool(fct and _time_ge(fct))

    def _respect_min_order_interval(self, sym) -> bool:
        min_iv = float(getattr(sym, "min_order_interval_sec", 1))
        last_ts = self.brk.last_order_ts.get(sym.code, 0.0)
        return (time.time() - last_ts) >= min_iv

    def _respect_max_open_positions(self) -> bool:
        max_pos = int(getattr(getattr(self.cfg, "portfolio", object()), "max_open_positions", 1))
        return len(self.brk.pos) < max_pos

    # ------- メイン -------
    def start(self):
        ensure_dir("runtime")
        self.api.auth()
        self.api.register_symbols(self.syms)
        logging.info(
            "起動 mode=%s / symbols=%s",
            "PAPER" if self.cfg.paper_trading else "LIVE",
            [s.code for s in self.syms],
        )

        while True:
            try:
                if os.path.exists(self.cfg.safety.kill_switch_file):
                    logging.warning("Kill switch detected — stopping all trades.")
                    break

                force_close = self._force_close_due()

                # ラウンドロビンでQPS制御
                slice_n = max(1, min(len(self.syms), max(1, self._qps_limit // 2)))
                start_idx = self._rr_i
                target_syms: List[Any] = []
                for k in range(slice_n):
                    target_syms.append(self.syms[(start_idx + k) % len(self.syms)])
                self._rr_i = (self._rr_i + slice_n) % len(self.syms)

                for sym in target_syms:
                    code_s = str(sym.code)
                    nowt = time.time()

                    # バックオフ/最小間隔
                    if nowt < self._backoff.get(code_s, 0.0):
                        continue
                    if nowt < self._next_board_at.get(code_s, 0.0):
                        continue

                    # board取得
                    try:
                        b = self.api.board(sym.code, sym.exchange)
                        self._next_board_at[code_s] = nowt + self._board_min_iv
                    except Exception as e:
                        msg = str(e)
                        if "429" in msg or "API実行回数エラー" in msg:
                            wait = max(2.0, 2.0 * (1.0 + (hash(code_s) % 50) / 100.0))
                            self._backoff[code_s] = time.time() + wait
                            self._next_board_at[code_s] = time.time() + wait
                            logging.warning("429 backoff: code=%s wait=%.2fs", code_s, wait)
                        else:
                            logging.warning("[API] board error code=%s: %s", code_s, msg)
                        continue

                    if not isinstance(b, dict) or not b:
                        continue

                    # 価格・板
                    cur = (
                        b.get("CurrentPrice") or b.get("CurrentPriceV2") or
                        b.get("LastTradedPrice") or b.get("CurrentValue")
                    )
                    if cur is None:
                        cur = (b.get("BestAsk") or {}).get("Price") or (b.get("BestBid") or {}).get("Price")
                        if cur is None:
                            continue

                    bid = (b.get("BestBid") or {}).get("Price") or b.get("BidPrice")
                    ask = (b.get("BestAsk") or {}).get("Price") or b.get("AskPrice")
                    bid_qty = (b.get("BestBid") or {}).get("Qty") or b.get("BidQty")
                    ask_qty = (b.get("BestAsk") or {}).get("Qty") or b.get("AskQty")

                    tick = tick_size(sym.code, cur, b)
                    spread_ticks = None
                    if isinstance(ask, (int, float)) and isinstance(bid, (int, float)):
                        spr = ask - bid
                        if tick > 0:
                            spread_ticks = spr / max(tick, 0.5)

                    # 1秒統計の更新
                    h = self.hist[code_s]
                    if nowt - h["last_ts"] >= 1.0:
                        if spread_ticks is not None:
                            h["spr_ticks"].append(float(spread_ticks))
                    if isinstance(bid_qty, (int, float)) and isinstance(ask_qty, (int, float)) and (bid_qty + ask_qty) > 0:
                        h["obi"].append(bid_qty / (bid_qty + ask_qty))
                    cumv = b.get("TradingVolume") or b.get("CurrentVolume") or b.get("Volume")
                    if isinstance(cumv, (int, float)):
                        if h["last_cumvol"] is not None:
                            dv = max(0, float(cumv) - float(h["last_cumvol"]))
                            h["vol_1s"].append(dv)
                            # VWAP 累積（出来高が取れた時だけ反映）
                            h["pv"] += float(cur) * float(dv)
                            h["vv"] += float(dv)
                        h["last_cumvol"] = float(cumv)
                    h["px_1s"].append(float(cur))
                    h["last_ts"] = nowt

                    # スパイク一時停止
                    spike_pct = float(getattr(getattr(self.cfg, "safety", object()), "spike_pct", 3.5))
                    spike_win = int(float(getattr(getattr(self.cfg, "safety", object()), "spike_window_sec", 3)))
                    pause_sec = float(getattr(getattr(self.cfg, "safety", object()), "pause_after_spike_sec", 60))
                    pxq = h["px_1s"]
                    if spike_win >= 1 and len(pxq) >= spike_win:
                        old_px = pxq[-spike_win]
                        if old_px > 0:
                            d_pct = abs(cur - old_px) / old_px * 100.0
                            if d_pct >= spike_pct:
                                self._cooldown_until[code_s] = time.time() + pause_sec
                                logging.warning("Spike pause: code=%s Δ=%.2f%% for %.0fs", code_s, d_pct, pause_sec)

                    # ====== 特徴量（7.3 最小＋8.0用拡張） ======
                    # VWAP距離(%)
                    vwap = (h["pv"]/h["vv"]) if h["vv"] > 0 else float(cur)
                    vwap_dist_pct = abs(float(cur) - vwap) / max(1.0, vwap) * 100.0
                    # 出来高加速(1min/5minMA)
                    vol1m = sum(list(h["vol_1s"])[-60:])
                    if len(h["vol_1s"]) >= 300:
                        vol5m = sum(list(h["vol_1s"])[-300:]) / 5.0  # 1分あたり
                    else:
                        vol5m = max(1.0, sum(h["vol_1s"]) / max(1, len(h["vol_1s"])))
                    vol_boost = float(vol1m) / max(1.0, vol5m)
                    # 日内ボリューム順位(0-1)
                    last_cum = float(h["last_cumvol"] or 0.0)
                    h["vol_min"] = min(h["vol_min"], last_cum)
                    h["vol_max"] = max(h["vol_max"], last_cum)
                    if h["vol_max"] > h["vol_min"]:
                        vol_rank = (last_cum - h["vol_min"]) / (h["vol_max"] - h["vol_min"])
                    else:
                        vol_rank = 0.5
                    # 市場温度（簡易：出来高加速×板厚バランス）
                    depth_ratio = float((bid_qty or 1)) / max(1.0, float(ask_qty or 1))
                    depth_factor = depth_ratio / (1.0 + depth_ratio)   # 0〜1
                    market_temp = max(0.0, min(1.0, 0.5 * min(2.0, vol_boost) / 2.0 + 0.5 * depth_factor))
                    market_heat = market_temp  # Lv8.0 用の名前付け

                    # 時間帯レジーム
                    regime = _regime_now()

                    # スプレッド広すぎ → 観測
                    if (spread_ticks is not None) and (spread_ticks > ROUTER_FORBID["spread_ticks"]):
                        self.buf[sym.code].append(float(cur))
                    # 直近変化（ティック換算）
                    buf = self.buf[sym.code]
                    prev = buf[-1] if len(buf) else None
                    delta_ticks = (float(cur) - float(prev)) / max(tick, 0.5) if prev is not None else 0.0

                    # OBI
                    imbalance = None
                    if isinstance(bid_qty, (int, float)) and isinstance(ask_qty, (int, float)) and (bid_qty + ask_qty) > 0:
                        imbalance = bid_qty / (bid_qty + ask_qty)

                    # ===== エントリー判定 =====
                    buf.append(float(cur))
                    recent = list(buf)[-3:] if len(buf) >= 3 else list(buf)
                    upticks = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])

                    # 流動性しきい値
                    vol5 = sum(list(h.get("vol_1s", []))[-5:]) if h.get("vol_1s") else 0.0
                    vol_hist = list(h.get("vol_1s", []))[-60:]
                    base_med = (sorted(vol_hist)[len(vol_hist)//2] if vol_hist else 0.0)
                    vol_thr = max(120.0, base_med * VOL_MED_FACTOR)

                    # 共通フラグ
                    obi_ok      = (imbalance is not None and imbalance >= SCALP["heat_entry"])
                    spread_ok   = (spread_ticks is not None and spread_ticks <= SCALP["max_spread_ticks"])
                    momentum_ok = (upticks >= 0) or (abs(delta_ticks) >= MOMENTUM_DELTA)
                    liquidity_ok= (vol5 >= vol_thr)
                    strong_long = (delta_ticks >= 0) and obi_ok and spread_ok and momentum_ok and liquidity_ok

                    # 共通ガード
                    allowed = True
                    if self.gk_enabled and code_s not in self.allowed_symbols:
                        allowed = False
                        if sym.code not in self.brk.pos:
                            logging.info("門番: 新規禁止（%s は allowed_symbols 外）", sym.code)

                    cool_ok            = (time.time() >= self._cooldown_until.get(code_s, 0.0))
                    can_trade_window   = self.brk.can_trade_now(is_entry=True)
                    can_new_entry      = self._can_new_entry_now()
                    under_max_positions= self._respect_max_open_positions()
                    interval_ok        = self._respect_min_order_interval(sym)
                    pos_open           = sym.code in self.brk.pos

                    # サイジング＆5円刻み可否
                    kpi = {
                        "paid_spread": float(spread_ticks or 0.0),
                        "exit_slip": 0.3,
                        "ev_tick": float(delta_ticks),
                        "rtt": float(getattr(self.api, "last_latency_ms", 0)),
                        "streak": -int(self._streak.get(sym.code, 0)),
                        "daily_pl": float(self._daily_pnl),
                        "tick_value": float(tick),
                        "time_bucket": int((datetime.datetime.now().minute // 10)),
                    }
                    size_dec   = self.risk_sizer.decide(code_s, kpi)
                    decided_qty= int(size_dec["qty"])

                    gate_ctx = type("C", (), {
                        "code": code_s,
                        "spread_ticks": float(spread_ticks or 0.0),
                        "vol5": float(vol5),
                        "best_bid_qty": float(bid_qty or 0.0),
                        "board_rtt_ms": float(getattr(self.api, "last_latency_ms", 0)),
                        "tick_value": float(tick),
                    })()
                    gate = self.symbol_gate.decide(gate_ctx, decided_qty)
                    allow_5yen = bool(gate["allow_5yen"])

                    # ---- 戦略ルーティング ----
                    heat_for_router = float(imbalance if imbalance is not None else 0.5)
                    price_step = int(max(1, round(float(tick))))
                    cx = Context(
                        code=code_s,
                        price_step=price_step,
                        spread_ticks=float(spread_ticks or 0.0),
                        heat=heat_for_router,
                        obi=heat_for_router,
                        vwap_distance_pct=float(vwap_dist_pct),
                        vol_boost_ratio=float(vol_boost),
                        vol_rank=float(vol_rank),
                        market_temp=float(market_temp)
                    )
                    strategy = self.router.decide(cx)

                    # 5円刻みの扱い：門番NGなら観測、OKでもSWINGのみ許可（spread<=2tick）
                    if float(tick) >= 5.0:
                        if not allow_5yen:
                            strategy = "OBSERVE"
                        elif strategy == "SCALP":
                            strategy = "OBSERVE"  # 5円刻みのSCALPは禁止
                        elif (spread_ticks is not None) and (spread_ticks > SWING["max_spread_ticks"]):
                            strategy = "OBSERVE"

                    # 市場温度しきい値（レジーム別）
                    market_heat_thr = 0.5 if regime == "OPEN" else 0.4
                    market_hot_enough = (market_heat >= market_heat_thr)

                    # エントリー可否
                    if (not pos_open) and strong_long and (strategy != "OBSERVE") \
                        and can_trade_window and can_new_entry and under_max_positions and interval_ok \
                        and allowed and cool_ok and (self._daily_pnl > -self._daily_stop) \
                        and market_hot_enough:

                        # 戦略別のサイズ調整：SWINGは0.5倍
                        if strategy == "MINI_SWING":
                            decided_qty = max(100, int(decided_qty * 0.5))

                        if self.cfg.paper_trading:
                            logging.info(
                                "PAPER BUY %s@%s x%d cur=%s strategy=%s regime=%s heat=%.2f mt=%.2f",
                                sym.code, sym.exchange, decided_qty, cur, strategy, regime, heat_for_router, market_heat
                            )
                            self.brk.pos[sym.code] = {
                                "entry": float(cur), "qty": int(decided_qty),
                                "ts": time.time(), "fill_ts": time.time(),
                                "strategy": strategy
                            }
                            self.brk.last_order_ts[sym.code] = time.time()
                        else:
                            logging.info(
                                "LIVE BUY  %s@%s x%d cur=%s strategy=%s regime=%s heat=%.2f mt=%.2f",
                                sym.code, sym.exchange, decided_qty, cur, strategy, regime, heat_for_router, market_heat
                            )
                            res = self.brk.buy(sym, b, cur)
                            if not res or res.get("Result") != 0:
                                logging.error("LIVE BUY 失敗: %s", res)
                            else:
                                self.brk.pos[sym.code] = {
                                    "entry": float(cur), "qty": int(decided_qty),
                                    "ts": time.time(), "fill_ts": None,
                                    "strategy": strategy
                                }
                            self.brk.last_order_ts[sym.code] = time.time()

                    # ===== EXIT 判定 =====
                    pos = self.brk.pos.get(sym.code)
                    if pos:
                        strategy = pos.get("strategy", "SCALP")
                        entry_px = float(pos["entry"])
                        qty = int(pos["qty"])
                        base_ts = float(pos.get("fill_ts") or pos.get("ts") or time.time())
                        held_sec = time.time() - base_ts

                        # --- SCALP: 既存ロジック（TP/SL/時間/強制/早BE） ---
                        if strategy == "SCALP":
                            tp_px = entry_px + SCALP["tp_ticks"] * tick
                            sl_px = entry_px - SCALP["sl_ticks"] * tick

                            fast_force = False
                            if held_sec >= FAST_CHECK_SEC:
                                if (spread_ticks is not None) and (spread_ticks > SCALP["max_spread_ticks"]):
                                    fast_force = True
                                if (bid_qty and ask_qty) and (bid_qty + ask_qty > 0):
                                    obi = bid_qty / (bid_qty + ask_qty)
                                    if obi < OBI_BE_THRESH:
                                        sl_px = max(sl_px, entry_px)  # 早BE

                            # bid基準のBE+（+1tickでSLを半分まで切り上げ）
                            if isinstance(bid, (int, float)):
                                if bid >= entry_px + 0.5 * tick:
                                    sl_px = max(sl_px, entry_px + 0.0 * tick)
                                if bid >= entry_px + 1.0 * tick:
                                    sl_px = max(sl_px, entry_px + 0.5 * tick)

                            hit_tp   = isinstance(bid, (int, float)) and (bid >= tp_px)
                            hit_sl   = isinstance(bid, (int, float)) and (bid <= sl_px)
                            hit_time = held_sec >= SCALP["max_hold_sec"]
                            hit_force= bool(force_close or fast_force)

                            if hit_tp or hit_sl or hit_time or hit_force:
                                reason = ("TP" if hit_tp else "SL" if hit_sl else "TIME" if hit_time else "FORCE")
                                if self.cfg.paper_trading:
                                    exit_px = float(bid if isinstance(bid, (int, float)) else cur)
                                    pnl = (exit_px - entry_px) * qty
                                    pnl_ticks = (exit_px - entry_px) / max(tick, 0.5)
                                    self._daily_pnl += pnl
                                    self._streak[sym.code] = self._streak.get(sym.code, 0) + (1 if pnl > 0 else -1)
                                    logging.info(f"PAPER EXIT {sym.code}@{sym.exchange} x{qty} bid={exit_px:.1f} → {reason} strategy={strategy} pnl={pnl:.1f}")

                                    # 学習
                                    try:
                                        update_after_trade({"code": sym.code, "strategy": strategy, "pnl_ticks": float(pnl_ticks)})
                                    except Exception as e:
                                        logging.warning(f"[OnlineTrain] failed {e}")

                                    # 取引ログ
                                    self._append_trade_log({
                                        "ts": jst_iso(),
                                        "code": sym.code,
                                        "strategy": strategy,
                                        "reason": reason,
                                        "pnl_ticks": float(pnl_ticks),
                                        "pnl_jpy": float(pnl),
                                        "slippage_ticks": None
                                    })

                                    del self.brk.pos[sym.code]
                                else:
                                    logging.info(f"LIVE EXIT  {sym.code}@{sym.exchange} x{qty} reason={reason} age={held_sec:.1f}s strategy={strategy}")
                                    ok, resp = self.brk.sell(sym, qty=qty)  # FIFO/ExecutionIDはBroker側
                                    if not ok:
                                        logging.error(f"[BROKER] EXIT_REJECT code={sym.code} resp={resp}")
                                    else:
                                        logging.info(f"[BROKER] EXIT_OK code={sym.code} resp={resp}")
                                    del self.brk.pos[sym.code]

                        # --- MINI_SWING: 条件崩壊型 ---  (Lv8.0: 市場温度も見る)
                        elif strategy == "MINI_SWING":
                            # 条件崩壊：heat低下・OBI反転・出来高失速・VWAP逸脱・時間切れ・市場温度低下
                            hit_tp = isinstance(bid, (int, float)) and (bid >= entry_px + SWING["tp_ticks_cap"] * tick)
                            hit_sl = isinstance(bid, (int, float)) and (bid <= entry_px - SWING["sl_ticks_cap"] * tick)
                            heat_now = float(imbalance if imbalance is not None else 0.5)
                            vol_cool = (vol_boost < 0.8)
                            obi_flip = (heat_now < (0.5 - SWING["obi_flip"]))  # 売り優勢側へ反転
                            vwap_over = (vwap_dist_pct > SWING["vwap_distance_max"])
                            hit_time = held_sec >= SWING["max_hold_sec"]
                            hit_force = bool(force_close)

                            # 市場温度による EXIT 条件（レジーム別）
                            exit_mkt_thr = 0.35 if regime == "OPEN" else 0.30
                            market_cold = (market_heat < exit_mkt_thr)

                            if hit_tp or hit_sl or hit_time or hit_force \
                               or (heat_now < SWING["heat_exit_drop"]) \
                               or vol_cool or obi_flip or vwap_over or market_cold:
                                if hit_tp: reason = "TP"
                                elif hit_sl: reason = "SL"
                                elif hit_time: reason = "TIME"
                                elif hit_force: reason = "FORCE"
                                elif market_cold: reason = "MKT"
                                else: reason = "COND"

                                if self.cfg.paper_trading:
                                    exit_px = float(bid if isinstance(bid, (int, float)) else cur)
                                    pnl = (exit_px - entry_px) * qty
                                    pnl_ticks = (exit_px - entry_px) / max(tick, 0.5)
                                    self._daily_pnl += pnl
                                    self._streak[sym.code] = self._streak.get(sym.code, 0) + (1 if pnl > 0 else -1)
                                    logging.info(f"PAPER EXIT {sym.code}@{sym.exchange} x{qty} bid={exit_px:.1f} → {reason} strategy={strategy} pnl={pnl:.1f}")

                                    # 学習
                                    try:
                                        update_after_trade({"code": sym.code, "strategy": strategy, "pnl_ticks": float(pnl_ticks)})
                                    except Exception as e:
                                        logging.warning(f"[OnlineTrain] failed {e}")

                                    # 取引ログ
                                    self._append_trade_log({
                                        "ts": jst_iso(),
                                        "code": sym.code,
                                        "strategy": strategy,
                                        "reason": reason,
                                        "pnl_ticks": float(pnl_ticks),
                                        "pnl_jpy": float(pnl),
                                        "slippage_ticks": None
                                    })

                                    del self.brk.pos[sym.code]
                                else:
                                    logging.info(f"LIVE EXIT  {sym.code}@{sym.exchange} x{qty} reason={reason} age={held_sec:.1f}s strategy={strategy}")
                                    ok, resp = self.brk.sell(sym, qty=qty)
                                    if not ok:
                                        logging.error(f"[BROKER] EXIT_REJECT code={sym.code} resp={resp}")
                                    else:
                                        logging.info(f"[BROKER] EXIT_OK code={sym.code} resp={resp}")
                                    del self.brk.pos[sym.code]

                        # EXITの有無を問わずクールダウン
                        self._cooldown_until[code_s] = time.time() + self._cooldown_after_exit_sec
                        continue

                # 1秒に一度テレメトリ
                now_sec = time.time()
                if now_sec - self._last_tele_write >= 1.0:
                    try:
                        self.tele.save_atomic({
                            "ts": jst_iso(),
                            "pnl": self._daily_pnl,
                            "streak": self._streak,
                        })
                        self._last_tele_write = now_sec
                    except Exception:
                        logging.warning("telemetry write failed", exc_info=True)

                time.sleep(LOOP_SLEEP_SEC)

            except Exception as e:
                logging.exception("controller main loop error: %s", e)
                time.sleep(0.2)
                continue


