# -*- coding: utf-8 -*-
import os, time, logging, traceback
import datetime
from collections import deque
from typing import Deque, Dict, Any, List

from utils_common import now, ensure_dir, jst_iso, tick_size
from telemetry import Telemetry
from broker import Broker
from kabus_api import KabusAPI
from model_registry import ModelRegistry

# 追加: 学習エンジンとメタ層
from entry_decider import EntryDecider
from exit_decider import ExitDecider
from risk_sizer import RiskSizer
from symbol_gate import SymbolGate

# ===== スキャ設定（RR>1）=====
SCALP = dict(
    tp_ticks=1,               # 利確 tick（必要なら 5 に）
    sl_ticks=1,               # 損切 tick
    max_hold_sec=25,          # 最大保有 秒
    max_spread_ticks=6        # 入口スプレッド制限
)
LOOP_SLEEP_SEC = 0.05

# ---- 常時この条件（テスト/本番 共通）----
FAST_CHECK_SEC   = 0.3   # 早めの健全性チェック秒
VOL_MED_FACTOR   = 0.4   # 直近60秒中央値×係数で流動性しきい値
MOMENTUM_DELTA   = 0.5   # |delta_ticks|の許容
OBI_BE_THRESH    = 0.48  # OBI悪化でBEへ

def _hhmm() -> str:
    return now().strftime("%H:%M")

def _time_ge(target_hhmm: str) -> bool:
    return _hhmm() >= str(target_hhmm)

class Controller:
    def __init__(self, cfg):
        self.cfg = cfg
        self.api = KabusAPI(cfg)
        self.brk = Broker(self.api, cfg)

        maxn = max(1, int(getattr(cfg, "max_concurrent_symbols", len(cfg.symbols) or 1)))
        self.syms = cfg.symbols[:maxn]

        # 直近価格バッファ（ティック方向判定用）
        self.buf: Dict[str, Deque[float]] = {s.code: deque(maxlen=5) for s in self.syms}

        self.tele = Telemetry(cfg.telemetry_path)
        self.models = ModelRegistry()
        self.models.load_all(self.syms)
        self._last_tele_write = 0.0

        # タイムアウトは config.exit.max_hold_sec を優先（未指定なら SCALP に近い25秒）
        self._max_hold_sec = int(getattr(getattr(self.cfg, "exit", object()), "max_hold_sec", SCALP["max_hold_sec"]))

        # --- board 叩き過ぎ対策（429対策 + ラウンドロビン） ---
        self._next_board_at = {str(s.code): 0.0 for s in self.syms}  # 次に板を叩ける時刻
        self._backoff = {str(s.code): 0.0 for s in self.syms}        # 429後の解除時刻
        self._board_min_iv = max(0.2, float(getattr(getattr(self.cfg, "safety", object()), "board_min_interval_ms", 900)) / 1000.0)
        self._qps_limit = int(getattr(getattr(self.cfg, "safety", object()), "api_qps_limit", 6))
        self._rr_i = 0  # round-robin index

        # --- Gatekeeper (allowed_symbols) ---
        gk = getattr(self.cfg, "gatekeeper", None)
        if gk and getattr(gk, "allowed_symbols", None):
            self.allowed_symbols = {str(x) for x in gk.allowed_symbols}
        else:
            self.allowed_symbols = {str(s.code) for s in self.syms}
        self.gk_enabled = bool(getattr(gk, "enabled", True))

        # --- Rotation（5分統計の保持） ---
        self.rot_cfg = getattr(self.cfg, "rotation", None)
        self.rotation_enabled = bool(getattr(self.rot_cfg, "enabled", False))
        self.rotation_last_ts = 0.0

        # per-symbol rolling stats (秒粒度, 直近5分=300サンプル)
        self.hist: Dict[str, Dict[str, Any]] = {}
        for s in self.syms:
            self.hist[str(s.code)] = {
                "spr_ticks": deque(maxlen=300),  # spread in ticks（秒）
                "vol_1s":   deque(maxlen=300),  # 1秒出来高（累計差分）
                "obi":      deque(maxlen=300),  # OrderBook Imbalance
                "px_1s":    deque(maxlen=10),   # 1秒ごとの価格（スパイク検知用）
                "last_cumvol": None,
                "last_ts": 0.0,
                "add_hits": 0,
                "drop_hits": 0,
            }

        # --- 連敗・クールダウン・日次損益 ---
        self._cooldown_until: Dict[str, float] = {}   # code -> epoch
        self._cooldown_after_exit_sec = float(getattr(getattr(self.cfg, "safety", object()), "cooldown_after_exit_sec", 2.0))
        self._streak: Dict[str, int] = {s.code: 0 for s in self.syms}
        self._streak_pause_sec = float(getattr(getattr(self.cfg, "safety", object()), "streak_pause_sec", 60.0))
        self._daily_pnl = 0.0
        # 修正: max_daily_loss_jpy を参照
        self._daily_stop = float(getattr(getattr(self.cfg, "risk", object()), "max_daily_loss_jpy", 10000))

        # === 学習層・メタ層（追加） ===
        self.entry_decider = EntryDecider(model_path="model_registry.json")
        self.exit_decider  = ExitDecider(model_path="model_registry.json")
        self.risk_sizer    = RiskSizer(model_path="model_registry.json", use_bandit=False)
        self.symbol_gate   = SymbolGate(model_path="model_registry.json")

    # -------------------------------------------------------
    # 小さなガード群
    # -------------------------------------------------------
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

    # -------------------------------------------------------
    # メインループ
    # -------------------------------------------------------
    def start(self):
        ensure_dir("runtime")
        self.api.auth()
        self.api.register_symbols(self.syms)
        logging.info(
            "起動 mode=%s / symbols=%s",
            "PAPER" if self.cfg.paper_trading else "LIVE",
            [s.code for s in self.syms],
        )

        first_dump = {s.code: True for s in self.syms}

        while True:
            try:
                # ---- kill switch ----
                if os.path.exists(self.cfg.safety.kill_switch_file):
                    logging.warning("Kill switch detected — stopping all trades.")
                    break

                force_close = self._force_close_due()

                # ---- ラウンドロビンで board のQPSを制御 ----
                slice_n = max(1, min(len(self.syms), max(1, self._qps_limit // 2)))
                start_idx = self._rr_i
                target_syms: List[Any] = []
                for k in range(slice_n):
                    target_syms.append(self.syms[(start_idx + k) % len(self.syms)])
                self._rr_i = (self._rr_i + slice_n) % len(self.syms)

                for sym in target_syms:
                    code_s = str(sym.code)
                    nowt = time.time()

                    # 429バックオフ / board最短間隔
                    if nowt < self._backoff.get(code_s, 0.0):
                        continue
                    if nowt < self._next_board_at.get(code_s, 0.0):
                        continue

                    # ---- board取得（429対策付き）----
                    b = {}
                    try:
                        b = self.api.board(sym.code, sym.exchange) if hasattr(self.api, "board") else {}
                        self._next_board_at[code_s] = nowt + self._board_min_iv
                    except Exception as e:
                        msg = str(e)
                        if "429" in msg or "API実行回数エラー" in msg:
                            wait = max(2.0, 2.0 * (1.0 + (hash(code_s) % 50) / 100.0))
                            self._backoff[code_s] = time.time() + wait
                            self._next_board_at[code_s] = time.time() + wait
                            logging.warning("429 backoff: code=%s wait=%.2fs", code_s, wait)
                            continue
                        self._next_board_at[code_s] = nowt + max(self._board_min_iv, 1.0)
                        logging.warning("[API] board error code=%s: %s", code_s, msg)
                        continue

                    if not isinstance(b, dict) or not b:
                        continue

                    # 現値・板
                    cur = (
                        b.get("CurrentPrice")
                        or b.get("CurrentPriceV2")
                        or b.get("LastTradedPrice")
                        or b.get("CurrentValue")
                    )
                    if cur is None:
                        cur = (b.get("BestAsk") or {}).get("Price") or (b.get("BestBid") or {}).get("Price")
                        if cur is None:
                            time.sleep(0.05)
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

                    # === 1秒ごとにローリング統計を更新 ===
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
                        h["last_cumvol"] = float(cumv)
                    h["px_1s"].append(float(cur))
                    h["last_ts"] = nowt

                    # === スパイク検知 ===
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

                    # スプレッドが広すぎると見送り
                    if (spread_ticks is not None) and (spread_ticks > SCALP["max_spread_ticks"]):
                        self.buf[sym.code].append(float(cur))
                        continue

                    # 直近変化（ティック換算）
                    buf = self.buf[sym.code]
                    prev = buf[-1] if len(buf) else None
                    delta_ticks = (float(cur) - float(prev)) / max(tick, 0.5) if prev is not None else 0.0

                    # OBI（板の偏り）
                    imbalance = None
                    if isinstance(bid_qty, (int, float)) and isinstance(ask_qty, (int, float)) and (bid_qty + ask_qty) > 0:
                        imbalance = bid_qty / (bid_qty + ask_qty)

                    # θ（モデルから上書き可）
                    theta_cfg = float(getattr(getattr(self.cfg, "entry", object()), "theta", 0.55))
                    theta = theta_cfg
                    try:
                        m = self.models.get(code_s) or {}
                        if isinstance(m, dict):
                            theta = float(m.get("theta", theta_cfg))
                    except Exception:
                        theta = theta_cfg

                    # ===== エントリー判定 =====
                    buf.append(float(cur))
                    recent = list(buf)[-3:] if len(buf) >= 3 else list(buf)
                    upticks = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])

                    # --- 流動性しきい値（常時速攻型） ---
                    vol5 = sum(list(h.get("vol_1s", []))[-5:]) if h.get("vol_1s") else 0.0
                    vol_hist = list(h.get("vol_1s", []))[-60:]
                    base_med = (sorted(vol_hist)[len(vol_hist)//2] if vol_hist else 0.0)
                    vol_thr = max(120.0, base_med * VOL_MED_FACTOR)

                    obi_ok = (imbalance is not None and imbalance >= theta)
                    spread_ok = (spread_ticks is not None and spread_ticks <= SCALP["max_spread_ticks"])
                    momentum_ok = (upticks >= 0) or (abs(delta_ticks) >= MOMENTUM_DELTA)
                    liquidity_ok = (vol5 >= vol_thr)
                    strong_long = (delta_ticks >= 0) and obi_ok and spread_ok and momentum_ok and liquidity_ok

                    # ENTRY前の共通判定
                    allowed = True
                    if self.gk_enabled and code_s not in self.allowed_symbols:
                        allowed = False
                        if sym.code not in self.brk.pos:
                            logging.info("門番: 新規禁止（%s は allowed_symbols 外）", sym.code)

                    cool_ok = (time.time() >= self._cooldown_until.get(code_s, 0.0))
                    can_trade_window = self.brk.can_trade_now(is_entry=True)
                    can_new_entry = self._can_new_entry_now()
                    under_max_positions = self._respect_max_open_positions()
                    interval_ok = self._respect_min_order_interval(sym)
                    pos_open = sym.code in self.brk.pos

                    # ========= メタ層（サイジング＆5円ティック可否） =========
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
                    size_dec = self.risk_sizer.decide(code_s, kpi)
                    decided_qty = int(size_dec["qty"])

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
                    if float(tick) >= 5.0 and not allow_5yen:
                        self.buf[sym.code].append(float(cur))
                        continue

                    # ========= Entryバンディット =========
                    ctx_entry = type("CTX", (), {
                        "code": code_s,
                        "obi_median_0_5s": float(imbalance if imbalance is not None else 0.5),
                        "spread_ticks": float(spread_ticks or 0.0),
                        "vol5": float(vol5),
                        "upticks": float(upticks),
                        "delta_ticks_2s": float(delta_ticks),
                        "board_rtt_ms": float(getattr(self.api, "last_latency_ms", 0)),
                        "cur_px": float(cur),
                        "recent_low_2s": float(min(list(self.buf[sym.code])[-5:], default=cur)),
                        "recent_high_2s": float(max(list(self.buf[sym.code])[-5:], default=cur)),
                        "tick_size": float(tick),
                        "now_ms": int(time.time() * 1000),
                    })()
                    act_entry = self.entry_decider.decide(ctx_entry)

                    if act_entry == "SKIP":
                        self.buf[sym.code].append(float(cur))
                        continue

                    # === ENTRY ===
                    if (not pos_open) and strong_long and can_trade_window and can_new_entry \
                        and under_max_positions and interval_ok and allowed \
                        and cool_ok and (self._daily_pnl > -self._daily_stop):

                        if self.cfg.paper_trading:
                            logging.info("PAPER BUY %s@%s x%d cur=%s", sym.code, sym.exchange, decided_qty, cur)
                            self.brk.pos[sym.code] = {"entry": float(cur), "qty": int(decided_qty),
                                                      "ts": time.time(), "fill_ts": time.time()}
                            self.brk.last_order_ts[sym.code] = time.time()
                        else:
                            logging.info("LIVE BUY  %s@%s x%d cur=%s", sym.code, sym.exchange, decided_qty, cur)
                            res = self.brk.buy(sym, b, cur)
                            if not res or res.get("Result") != 0:
                                logging.error("LIVE BUY 失敗: %s", res)
                            else:
                                # ★ LIVEでもEXIT判定が回るよう内部ポジを登録
                                self.brk.pos[sym.code] = {
                                    "entry": float(cur),
                                    "qty":   int(decided_qty),
                                    "ts":    time.time(),
                                    "fill_ts": None,  # Broker側約定検知で埋める
                                }
                            self.brk.last_order_ts[sym.code] = time.time()

                    # === EXIT 判定（force_close 時や SL/TP）===
                    pos = self.brk.pos.get(sym.code)
                    if pos:
                        entry_px = float(pos["entry"])
                        qty = int(pos["qty"])
                        # 約定時刻（fill_ts）を優先、無ければ発注時刻(ts)
                        base_ts = float(pos.get("fill_ts") or pos.get("ts") or time.time())
                        held_sec = time.time() - base_ts

                        # ロング前提：TP/SL は bid で判定（ショートなら ask に分岐すること）
                        tp_px = entry_px + SCALP["tp_ticks"] * tick
                        sl_px = entry_px - SCALP["sl_ticks"] * tick

                        # 即時見切り & BE/BE+
                        fast_force = False
                        if held_sec >= FAST_CHECK_SEC:
                            if (spread_ticks is not None) and (spread_ticks > SCALP["max_spread_ticks"]):
                                fast_force = True
                            if (imbalance is not None) and (imbalance < OBI_BE_THRESH):
                                sl_px = max(sl_px, entry_px + 0.0 * tick)

                        # 早BE/BE+：bid 基準
                        if isinstance(bid, (int, float)):
                            if bid >= entry_px + 0.5 * tick:
                                sl_px = max(sl_px, entry_px + 0.0 * tick)
                            if bid >= entry_px + 1.0 * tick:
                                sl_px = max(sl_px, entry_px + 0.5 * tick)

                        hit_tp = isinstance(bid, (int, float)) and (bid >= tp_px)
                        hit_sl = isinstance(bid, (int, float)) and (bid <= sl_px)
                        hit_time = held_sec >= self._max_hold_sec
                        hit_force = bool(force_close or fast_force)

                        if hit_tp or hit_sl or hit_time or hit_force:
                            reason = ("TP" if hit_tp else "SL" if hit_sl else "TIME" if hit_time else "FORCE")
                            if self.cfg.paper_trading:
                                exit_px = float(bid if isinstance(bid, (int, float)) else cur)
                                pnl = (exit_px - entry_px) * qty
                                self._daily_pnl += pnl
                                self._streak[sym.code] = self._streak.get(sym.code, 0) + (1 if pnl > 0 else -1)
                                logging.info(f"PAPER EXIT {sym.code}@{sym.exchange} x{qty} bid={exit_px:.1f} → {reason} pnl={pnl:.1f}")
                                del self.brk.pos[sym.code]
                            else:
                                logging.info(f"LIVE EXIT  {sym.code}@{sym.exchange} x{qty} reason={reason} age={held_sec:.1f}s")
                                ok, resp = self.brk.sell(sym, qty=qty)  # 決済（ExecutionID→FIFO）
                                if not ok:
                                    logging.error(f"[BROKER] EXIT_REJECT code={sym.code} resp={resp}")
                                else:
                                    logging.info(f"[BROKER] EXIT_OK code={sym.code} resp={resp}")
                                # 内部posは一旦削除（再同期は別途）
                                del self.brk.pos[sym.code]

                        # クールダウン
                        self._cooldown_until[code_s] = time.time() + self._cooldown_after_exit_sec
                        continue

                # ---- ループ末尾: テレメトリとスリープ ----
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


