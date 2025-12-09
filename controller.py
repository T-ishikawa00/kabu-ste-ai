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

# Lv7.3: 戦略分離用
from strategy_router import StrategyRouter, Context

# Lv7.3: オンライン学習（戦略別・pnl_ticks）
from train_policy_online import update_after_trade

# ===== パラメータ（初期値） =====
# SCALP は観測寄り。実トレードは MINI_SWING をメインとする。
SCALP = dict(
    tp_ticks=1,
    sl_ticks=1,
    max_hold_sec=25,
    max_spread_ticks=3,
    heat_entry=0.64,
    exit_drop=0.45,
)

# ★★★ 修正箇所1: ミニスイング → 本格スイング寄りパラメータへ変更 ★★★
SWING = dict(
    tp_ticks_cap=20,         # 上限TP（20tick程度まで許容）
    sl_ticks_cap=10,         # 上限SL（ノイズで狩られないよう10tickまで許容）
    max_hold_sec=120,        # 最大保有時間（2分）
    max_spread_ticks=3,      # スプレッド許容を少し拡大
    heat_exit_drop=0.40,     # 板優位性が0.40未満に落ちたら危険シグナル
    obi_flip=0.10,           # 売り優勢側に0.10以上振れたら危険シグナル
    vwap_distance_max=0.40,  # VWAPからの乖離上限（%）
)

# ルーター閾値（初期）
ROUTER_PREF = dict(
    heat=0.72,
    vwap_distance=0.25,   # %
    spread_ticks=2,
    vol_boost_ratio=1.5,
    market_temp=0.60,
)
ROUTER_FORBID = dict(
    spread_ticks=6,
    vol_rank=0.10,
    price_step=5,
)

LOOP_SLEEP_SEC  = 0.05
FAST_CHECK_SEC  = 0.3
GRACE_SEC       = 30.0        # ★★★ 修正: 30秒間はノイズEXITを無効化（猶予期間）
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
        self._backoff       = {str(s.code): 0.0 for s in self.syms}
        self._board_min_iv = max(
            0.2,
            float(getattr(getattr(self.cfg, "safety", object()), "board_min_interval_ms", 900)) / 1000.0,
        )
        self._qps_limit     = int(getattr(getattr(self.cfg, "safety", object()), "api_qps_limit", 6))
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

        # 秒統計（直近5分＝300サンプル）＋特徴量の素材＋1分足バッファ
        self.hist: Dict[str, Dict[str, Any]] = {}
        for s in self.syms:
            self.hist[str(s.code)] = {
                "spr_ticks": deque(maxlen=300),
                "vol_1s":   deque(maxlen=300),
                "obi":      deque(maxlen=300),
                "px_1s":    deque(maxlen=10),
                "last_cumvol": None,
                "last_ts": 0.0,
                "pv": 0.0,
                "vv": 0.0,      # VWAP用
                "vol_min": 1e18,
                "vol_max": 0.0,  # 日内ボリューム正規化
                "last_bid_qty": 0.0,  # 板厚変化検出用
                "last_ask_qty": 0.0,
                # 1分足OHLC
                "m1_cur": None,               # 進行中の1分足
                "m1_bars": deque(maxlen=600)  # 過去600分分の1分足
            }

        # クールダウン・連敗・日次
        self._cooldown_until: Dict[str, float] = {}
        self._cooldown_after_exit_sec = float(
            getattr(getattr(self.cfg, "safety", object()), "cooldown_after_exit_sec", 2.0)
        )
        self._streak: Dict[str, int] = {s.code: 0 for s in self.syms}
        self._streak_pause_sec = float(getattr(getattr(self.cfg, "safety", object()), "streak_pause_sec", 60.0))
        self._daily_pnl = 0.0
        self._daily_stop = float(getattr(getattr(self.cfg, "risk", object()), "max_daily_loss_jpy", 10000))

        # 7.3 ルーター
        self.router = StrategyRouter(
            {
                "router": {
                    "prefer_swing_if": ROUTER_PREF,
                    "forbid_entry_if": ROUTER_FORBID,
                }
            }
        )

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
                        b.get("CurrentPrice")
                        or b.get("CurrentPriceV2")
                        or b.get("LastTradedPrice")
                        or b.get("CurrentValue")
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

                    # 板厚の一つ前を保持
                    prev_bid_qty = float(h.get("last_bid_qty", 0.0))
                    prev_ask_qty = float(h.get("last_ask_qty", 0.0))
                    cur_bid_qty  = float(bid_qty or 0.0)
                    cur_ask_qty  = float(ask_qty or 0.0)
                    h["last_bid_qty"] = cur_bid_qty
                    h["last_ask_qty"] = cur_ask_qty

                    if nowt - h["last_ts"] >= 1.0:
                        if spread_ticks is not None:
                            h["spr_ticks"].append(float(spread_ticks))
                    if isinstance(bid_qty, (int, float)) and isinstance(ask_qty, (int, float)) and (bid_qty + ask_qty) > 0:
                        h["obi"].append(bid_qty / (bid_qty + ask_qty))

                    # 出来高・VWAP計算用
                    dv = 0.0
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

                    # --- 1分足OHLCの生成・更新 ---
                    dt_now = datetime.datetime.fromtimestamp(nowt)
                    minute_key = dt_now.replace(second=0, microsecond=0)
                    price_f = float(cur)

                    bar1 = h.get("m1_cur")
                    if bar1 is None or bar1["start"] != minute_key:
                        # 1分の切り替わり → 直前のバーを確定させて履歴に積む
                        if bar1 is not None:
                            h["m1_bars"].append(bar1)
                        # 新しい1分足を開始
                        h["m1_cur"] = {
                            "start": minute_key,
                            "open": price_f,
                            "high": price_f,
                            "low": price_f,
                            "close": price_f,
                            "vol": float(dv),
                        }
                    else:
                        # 進行中バーの更新
                        bar1["close"] = price_f
                        bar1["high"] = max(bar1["high"], price_f)
                        bar1["low"] = min(bar1["low"], price_f)
                        bar1["vol"] += float(dv)

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

                    # ====== 特徴量 ======
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

                    # ===== エントリー判定（MTFロジック） =====
                    buf.append(float(cur))

                    # --- 1分足/3分足/15分足ベースの判定用バッファ ---
                    m1_bars = h.get("m1_bars", deque())

                    # 15分足フィルター（環境認識）
                    #  - 15分足MA25 ≒ 1分足375本のMA25
                    #  - 現在価格 > MA25 かつ MA25が上向き
                    ma15_ok = False
                    
                    # ★★★ 修正箇所2: 20分溜まれば動くように緩和（テスト用） ★★★
                    if len(m1_bars) >= 20:
                        closes = [bar["close"] for bar in m1_bars]
                        # データが足りないうちは、ある分だけで平均を取る（コールドスタート対応）
                        lookback = min(375, len(closes))
                        ma_now = sum(closes[-lookback:]) / float(lookback)
                        
                        # 傾き判定
                        if lookback > 5:
                            ma_prev = sum(closes[-(lookback+1):-1]) / float(lookback)
                        else:
                            ma_prev = ma_now # データ不足時は傾きなしとみなす

                        if (float(cur) > ma_now) and (ma_now >= ma_prev):
                            ma15_ok = True

                    # 3分足ロジック（執行の主軸）
                    #  - 直近60分（≒3分足20本）から High_20 / Low_20 を計算
                    #  - パターンA: 押し目反発 Low_20 + 10tick 以上
                    #  - パターンB: ブレイク High_20 + 5tick 超え
                    pattern_ok = False
                    
                    # ★★★ 修正箇所3: 60分なくてもある分で判定（テスト用） ★★★
                    if len(m1_bars) >= 20: 
                        # 直近最大60分を参照
                        window_lookback = min(60, len(m1_bars))
                        window_bars = list(m1_bars)[-window_lookback:]
                        
                        low_20 = min(bar["low"] for bar in window_bars)
                        high_20 = max(bar["high"] for bar in window_bars)
                        price_f = float(cur)

                        patA = price_f >= low_20 + 10.0 * tick   # 押し目反発（+10tick）
                        patB = price_f >= high_20 + 5.0 * tick   # ブレイク（+5tick）

                        pattern_ok = (patA or patB)

                    # 1分足フィルター（勢い）
                    #  - 直近1分の出来高 >= 過去20分平均の1.2倍
                    vol_filter_ok = False
                    if len(m1_bars) >= 21:
                        last_21 = list(m1_bars)[-21:]    # [過去20分, 直近1分]
                        last_bar = last_21[-1]
                        prev20 = last_21[:-1]
                        avg20 = sum(b1["vol"] for b1 in prev20) / max(1, len(prev20))
                        if avg20 > 0 and last_bar["vol"] >= 1.2 * avg20:
                            vol_filter_ok = True

                    # 最終エントリー条件（AND）
                    strong_long = ma15_ok and pattern_ok and vol_filter_ok

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

                    # ロット決定: 当面は設定qty or 固定ロット
                    decided_qty = int(getattr(sym, "qty", 100) or 100)

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
                        market_temp=float(market_temp),
                    )
                    strategy = self.router.decide(cx)

                    # 5円刻みの扱い：SCALPは禁止、スプレッド広すぎも禁止
                    if float(tick) >= 5.0:
                        if strategy == "SCALP":
                            strategy = "OBSERVE"  # 5円刻みのSCALPは禁止
                        elif (spread_ticks is not None) and (spread_ticks > SWING["max_spread_ticks"]):
                            strategy = "OBSERVE"

                    # ミニスイング特化：SCALPは実行せず、SCALP→MINI_SWINGに寄せる
                    if strategy == "SCALP":
                        strategy = "MINI_SWING"

                    # 市場温度しきい値（レジーム別）
                    market_heat_thr = 0.5 if regime == "OPEN" else 0.4
                    market_hot_enough = (market_heat >= market_heat_thr)

                    # エントリー可否
                    if (
                        (not pos_open)
                        and strong_long
                        and (strategy != "OBSERVE")
                        and can_trade_window
                        and can_new_entry
                        and under_max_positions
                        and interval_ok
                        and allowed
                        and cool_ok
                        and (self._daily_pnl > -self._daily_stop)
                        and market_hot_enough
                    ):

                        if self.cfg.paper_trading:
                            logging.info(
                                "PAPER BUY %s@%s x%d cur=%s strategy=%s regime=%s heat=%.2f mt=%.2f",
                                sym.code,
                                sym.exchange,
                                decided_qty,
                                cur,
                                strategy,
                                regime,
                                heat_for_router,
                                market_heat,
                            )
                            self.brk.pos[sym.code] = {
                                "entry": float(cur),
                                "qty": int(decided_qty),
                                "ts": time.time(),
                                "fill_ts": time.time(),
                                "strategy": strategy,
                            }
                            self.brk.last_order_ts[sym.code] = time.time()
                        else:
                            logging.info(
                                "LIVE BUY  %s@%s x%d cur=%s strategy=%s regime=%s heat=%.2f mt=%.2f",
                                sym.code,
                                sym.exchange,
                                decided_qty,
                                cur,
                                strategy,
                                regime,
                                heat_for_router,
                                market_heat,
                            )
                            res = self.brk.buy(sym, b, cur)
                            if not res or res.get("Result") != 0:
                                logging.error("LIVE BUY 失敗: %s", res)
                            else:
                                self.brk.pos[sym.code] = {
                                    "entry": float(cur),
                                    "qty": int(decided_qty),
                                    "ts": time.time(),
                                    "fill_ts": None,
                                    "strategy": strategy,
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

                        # SCALPは現在MINI_SWINGロジックに統合する
                        if strategy == "SCALP":
                            strategy = "MINI_SWING"
                            pos["strategy"] = "MINI_SWING"

                        # --- MINI_SWING: 条件崩壊型（ノイズ耐性＋危険シグナル3個以上でEXIT） ---
                        if strategy == "MINI_SWING":
                            # 現在の含み損益（tick換算）
                            if isinstance(bid, (int, float)):
                                live_pnl_ticks = (bid - entry_px) / max(tick, 0.5)
                                exit_px_live = float(bid)
                            else:
                                live_pnl_ticks = (cur - entry_px) / max(tick, 0.5)
                                exit_px_live = float(cur)

                            # 固定TP/SL
                            hit_tp = isinstance(bid, (int, float)) and (
                                bid >= entry_px + SWING["tp_ticks_cap"] * tick
                            )
                            hit_sl = isinstance(bid, (int, float)) and (
                                bid <= entry_px - SWING["sl_ticks_cap"] * tick
                            )

                            heat_now = float(imbalance if imbalance is not None else 0.5)
                            vol_cool = (vol_boost < 0.8)
                            obi_flip = (heat_now < (0.5 - SWING["obi_flip"]))  # 売り優勢側へ反転
                            vwap_over = (vwap_dist_pct > SWING["vwap_distance_max"])
                            hit_time = held_sec >= SWING["max_hold_sec"]
                            hit_force = bool(force_close)

                            # 市場温度による EXIT 条件
                            exit_mkt_thr = 0.35
                            market_cold = (market_heat < exit_mkt_thr)

                            # 危険シグナルをカウント
                            danger_flags = []
                            if heat_now < SWING["heat_exit_drop"]:
                                danger_flags.append("HEAT")
                            if vol_cool:
                                danger_flags.append("VOL")
                            if obi_flip:
                                danger_flags.append("OBI")
                            if vwap_over:
                                danger_flags.append("VWAP")
                            if market_cold:
                                danger_flags.append("MKT")

                            do_exit = False
                            reason = "COND"

                            # 1. 強制系は即EXIT
                            if hit_tp or hit_sl or hit_time or hit_force:
                                do_exit = True
                                if hit_tp:
                                    reason = "TP"
                                elif hit_sl:
                                    reason = "SL"
                                elif hit_time:
                                    reason = "TIME"
                                elif hit_force:
                                    reason = "FORCE"
                            else:
                                # 2. 猶予期間中は条件ベースEXITを無効化（ノイズ耐性）
                                if held_sec < GRACE_SEC:
                                    do_exit = False
                                    reason = "HOLD_GRACE"
                                else:
                                    # 3. 危険シグナルが3個以上そろったときだけ条件崩壊としてEXIT
                                    if len(danger_flags) >= 3:
                                        do_exit = True
                                        reason = "COND"

                            if do_exit:
                                if self.cfg.paper_trading:
                                    exit_px = exit_px_live
                                    pnl = (exit_px - entry_px) * qty
                                    pnl_ticks = (exit_px - entry_px) / max(tick, 0.5)
                                    self._daily_pnl += pnl
                                    self._streak[sym.code] = self._streak.get(sym.code, 0) + (
                                        1 if pnl > 0 else -1
                                    )
                                    logging.info(
                                        f"PAPER EXIT {sym.code}@{sym.exchange} x{qty} "
                                        f"bid={exit_px:.1f} → {reason} strategy={strategy} pnl={pnl:.1f} "
                                        f"pnl_ticks={pnl_ticks:.2f} danger={danger_flags}"
                                    )

                                    # 学習
                                    try:
                                        update_after_trade(
                                            {"code": sym.code, "strategy": strategy, "pnl_ticks": float(pnl_ticks)}
                                        )
                                    except Exception as e:
                                        logging.warning(f"[OnlineTrain] failed {e}")

                                    # 取引ログ
                                    self._append_trade_log(
                                        {
                                            "ts": jst_iso(),
                                            "code": sym.code,
                                            "strategy": strategy,
                                            "reason": reason,
                                            "pnl_ticks": float(pnl_ticks),
                                            "pnl_jpy": float(pnl),
                                            "slippage_ticks": None,
                                        }
                                    )

                                    del self.brk.pos[sym.code]
                                else:
                                    logging.info(
                                        f"LIVE EXIT  {sym.code}@{sym.exchange} x{qty} "
                                        f"reason={reason} age={held_sec:.1f}s strategy={strategy} "
                                        f"pnl_ticks≈{live_pnl_ticks:.2f} danger={danger_flags}"
                                    )
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
                        self.tele.save_atomic(
                            {
                                "ts": jst_iso(),
                                "pnl": self._daily_pnl,
                                "streak": self._streak,
                            }
                        )
                        self._last_tele_write = now_sec
                    except Exception:
                        logging.warning("telemetry write failed", exc_info=True)

                time.sleep(LOOP_SLEEP_SEC)

            except Exception as e:
                logging.exception("controller main loop error: %s", e)
                time.sleep(0.2)
                continue
