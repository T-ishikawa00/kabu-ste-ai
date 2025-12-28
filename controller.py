# -*- coding: utf-8 -*-
import os, time, logging, json
import datetime
import random 
from collections import deque
from typing import Deque, Dict, Any, List

# ★AI・データ分析用ライブラリ
import numpy as np
import pandas as pd
import pandas_ta as ta  # テクニカル指標計算用
from stable_baselines3 import PPO

from utils_common import now, ensure_dir, jst_iso, tick_size
from telemetry import Telemetry
from broker import Broker
from kabus_api import KabusAPI
from model_registry import ModelRegistry

# データ収集機
from data_collector import DataCollector

# 戦略分離用
from strategy_router import StrategyRouter, Context

# ===== パラメータ（デフォルト値） =====
SWING = dict(
    tp_ticks_cap=60,
    sl_ticks_cap=25,
    max_hold_sec=3600,
    max_spread_ticks=5,
    heat_exit_drop=0.20,
    obi_flip=0.10,
    vwap_distance_max=0.40,
)

ROUTER_PREF = dict(
    heat=0.70,
    vwap_distance=0.30,
    spread_ticks=2,
    vol_boost_ratio=1.2,
    market_temp=0.50,
)
ROUTER_FORBID = dict(
    spread_ticks=6,
    vol_rank=0.10,
    price_step=5,
)

LOOP_SLEEP_SEC  = 0.05
GRACE_SEC       = 30.0

# ==========================================
# ★追加: RCI計算用関数 (step3_trainと同じロジック)
# ==========================================
def calc_rci_series(series: pd.Series, period=9) -> pd.Series:
    """
    リアルタイム運用用のRCI計算関数
    """
    rci_values = []
    # 処理速度優先のため、直近のデータのみ計算してもよいが、
    # ここではpandasのapply等を使わず愚直に回す（データ数が少ないため高速）
    # リアルタイム用には「最後の行だけ」計算すれば良いが、念のためSeriesで返す
    
    # 計算に必要な長さがない場合はNaN埋め
    if len(series) < period:
        return pd.Series([np.nan] * len(series), index=series.index)

    # 全体を計算すると遅いので、直近1点だけ計算する簡易版ではなく、
    # windowを使って計算する
    # ※ controller内ではデータ量が少ない(max 600)ので全計算しても一瞬です
    
    time_rank = np.arange(1, period + 1)
    
    # ループ計算
    for i in range(len(series)):
        if i < period - 1:
            rci_values.append(np.nan)
            continue
        
        s = series.iloc[i - period + 1 : i + 1]
        price_rank = s.rank(method='max').values
        
        d_sq = np.sum((time_rank - price_rank) ** 2)
        val = (1 - (6 * d_sq) / (period**3 - period)) * 100
        rci_values.append(val)
        
    return pd.Series(rci_values, index=series.index)


def _hhmm() -> str:
    return now().strftime("%H:%M")

def _time_ge(target_hhmm: str) -> bool:
    return _hhmm() >= str(target_hhmm)

def _regime_now() -> str:
    h = _hhmm()
    if h < "09:10": return "OPEN"
    if h < "12:30": return "AM"
    return "PM"

class Controller:
    def __init__(self, cfg):
        self.cfg = cfg
        self.api = KabusAPI(cfg)
        self.brk = Broker(self.api, cfg)

        maxn = max(1, int(getattr(cfg, "max_concurrent_symbols", len(cfg.symbols) or 1)))
        self.syms = cfg.symbols[:maxn]

        self.buf: Dict[str, Deque[float]] = {s.code: deque(maxlen=5) for s in self.syms}
        self.tele = Telemetry(cfg.telemetry_path)
        self.models = ModelRegistry()
        self.models.load_all(self.syms)
        self._last_tele_write = 0.0

        # データ収集機の起動
        self.collector = DataCollector()
        logging.info("💾 DataCollector initialized")

        # ==========================================
        # AIモデルのロード (v30から探索)
        # ==========================================
        self.ai_models = {}
        logging.info("🤖 AIモデルのロードを開始します...")
        
        for s in self.syms:
            code = str(s.code)
            loaded = False
            # v30 から v1 まで順番に探す（最新優先）
            for v in range(30, 0, -1):
                model_path = f"model_{code}_v{v}"
                if os.path.exists(model_path + ".zip"):
                    try:
                        self.ai_models[code] = PPO.load(model_path)
                        logging.info(f"  ✅ [{code}] AI搭載完了 (v{v})")
                        loaded = True
                        break 
                    except Exception as e:
                        logging.warning(f"  ⚠️ [{code}] v{v} ロード失敗: {e}")
            
            if not loaded:
                logging.info(f"  ⚪ [{code}] AIなし (ルールベースのみで稼働)")

        self._next_board_at = {str(s.code): 0.0 for s in self.syms}
        self._backoff       = {str(s.code): 0.0 for s in self.syms}
        self._board_min_iv = max(
            0.2,
            float(getattr(getattr(self.cfg, "safety", object()), "board_min_interval_ms", 900)) / 1000.0,
        )
        self._qps_limit     = int(getattr(getattr(self.cfg, "safety", object()), "api_qps_limit", 6))
        self._rr_i = 0

        gk = getattr(self.cfg, "gatekeeper", None)
        if gk and getattr(gk, "allowed_symbols", None):
            self.allowed_symbols = {str(x) for x in gk.allowed_symbols}
        else:
            self.allowed_symbols = {str(s.code) for s in self.syms}
        self.gk_enabled = bool(getattr(gk, "enabled", True))

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
                "vv": 0.0,
                "vol_min": 1e18,
                "vol_max": 0.0,
                "last_bid_qty": 0.0,
                "last_ask_qty": 0.0,
                "m1_cur": None,
                "m1_bars": deque(maxlen=600) # RCI計算用に少し長めに保持
            }

        self._cooldown_until: Dict[str, float] = {}
        self._cooldown_after_exit_sec = float(
            getattr(getattr(self.cfg, "safety", object()), "cooldown_after_exit_sec", 2.0)
        )
        self._streak: Dict[str, int] = {s.code: 0 for s in self.syms}
        self._daily_pnl = 0.0
        self._daily_stop = float(getattr(getattr(self.cfg, "risk", object()), "max_daily_loss_jpy", 10000))

        self.router = StrategyRouter({
            "router": {
                "prefer_swing_if": ROUTER_PREF,
                "forbid_entry_if": ROUTER_FORBID,
            }
        })

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

    def _get_sym_param(self, sym, key, default_val):
        val = getattr(sym, key, None)
        if val is not None: return val
        if isinstance(sym, dict):
            val = sym.get(key)
            if val is not None: return val
        try:
            raw_symbols = getattr(self.cfg, "symbols", [])
            target_code = str(getattr(sym, "code", "") or sym.get("code", ""))
            for s_conf in raw_symbols:
                code_in_conf = str(getattr(s_conf, "code", "") or s_conf.get("code", ""))
                if code_in_conf == target_code:
                    raw_val = getattr(s_conf, key, None) if hasattr(s_conf, key) else s_conf.get(key) if isinstance(s_conf, dict) else None
                    if raw_val is not None:
                        return type(default_val)(raw_val)
        except Exception:
            pass
        return default_val

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
                slice_n = max(1, min(len(self.syms), max(1, self._qps_limit // 2)))
                start_idx = self._rr_i
                target_syms: List[Any] = []
                for k in range(slice_n):
                    target_syms.append(self.syms[(start_idx + k) % len(self.syms)])
                self._rr_i = (self._rr_i + slice_n) % len(self.syms)

                for sym in target_syms:
                    code_s = str(sym.code)
                    nowt = time.time()

                    if nowt < self._backoff.get(code_s, 0.0): continue
                    if nowt < self._next_board_at.get(code_s, 0.0): continue

                    try:
                        b = self.api.board(sym.code, sym.exchange)
                        self._next_board_at[code_s] = nowt + self._board_min_iv
                        self.collector.log_board(code_s, b)
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

                    if not isinstance(b, dict) or not b: continue

                    cur = (b.get("CurrentPrice") or b.get("CurrentPriceV2") or b.get("LastTradedPrice") or b.get("CurrentValue"))
                    if cur is None:
                        cur = (b.get("BestAsk") or {}).get("Price") or (b.get("BestBid") or {}).get("Price")
                        if cur is None: continue

                    bid = (b.get("BestBid") or {}).get("Price") or b.get("BidPrice")
                    ask = (b.get("BestAsk") or {}).get("Price") or b.get("AskPrice")
                    bid_qty = (b.get("BestBid") or {}).get("Qty") or b.get("BidQty")
                    ask_qty = (b.get("BestAsk") or {}).get("Qty") or b.get("AskQty")

                    tick = tick_size(sym.code, cur, b)
                    spread_ticks = None
                    if isinstance(ask, (int, float)) and isinstance(bid, (int, float)):
                        spr = ask - bid
                        if tick > 0: spread_ticks = spr / max(tick, 0.5)

                    h = self.hist[code_s]
                    cur_bid_qty, cur_ask_qty = float(bid_qty or 0.0), float(ask_qty or 0.0)
                    h["last_bid_qty"], h["last_ask_qty"] = cur_bid_qty, cur_ask_qty

                    if nowt - h["last_ts"] >= 1.0:
                        if spread_ticks is not None: h["spr_ticks"].append(float(spread_ticks))
                    if (cur_bid_qty + cur_ask_qty) > 0:
                        h["obi"].append(cur_bid_qty / (cur_bid_qty + cur_ask_qty))

                    dv = 0.0
                    cumv = b.get("TradingVolume") or b.get("CurrentVolume") or b.get("Volume")
                    if isinstance(cumv, (int, float)):
                        if h["last_cumvol"] is not None:
                            dv = max(0, float(cumv) - float(h["last_cumvol"]))
                            h["vol_1s"].append(dv)
                            h["pv"] += float(cur) * float(dv)
                            h["vv"] += float(dv)
                        h["last_cumvol"] = float(cumv)
                    h["px_1s"].append(float(cur))
                    h["last_ts"] = nowt

                    dt_now = datetime.datetime.fromtimestamp(nowt)
                    minute_key = dt_now.replace(second=0, microsecond=0)
                    price_f = float(cur)
                    bar1 = h.get("m1_cur")
                    if bar1 is None or bar1["start"] != minute_key:
                        if bar1 is not None: h["m1_bars"].append(bar1)
                        h["m1_cur"] = {
                            "start": minute_key, "open": price_f, "high": price_f, "low": price_f, "close": price_f, "vol": float(dv)
                        }
                    else:
                        bar1["close"] = price_f
                        bar1["high"] = max(bar1["high"], price_f)
                        bar1["low"] = min(bar1["low"], price_f)
                        bar1["vol"] += float(dv)

                    # Spike Pause
                    spike_pct = float(getattr(getattr(self.cfg, "safety", object()), "spike_pct", 3.5))
                    spike_win = int(float(getattr(getattr(self.cfg, "safety", object()), "spike_window_sec", 3)))
                    pxq = h["px_1s"]
                    if spike_win >= 1 and len(pxq) >= spike_win:
                        old_px = pxq[-spike_win]
                        if old_px > 0 and (abs(cur - old_px) / old_px * 100.0) >= spike_pct:
                            self._cooldown_until[code_s] = time.time() + float(getattr(getattr(self.cfg, "safety", object()), "pause_after_spike_sec", 60))

                    # Features (Simple)
                    vwap = (h["pv"]/h["vv"]) if h["vv"] > 0 else float(cur)
                    vwap_dist_pct = abs(float(cur) - vwap) / max(1.0, vwap) * 100.0
                    vol1m = sum(list(h["vol_1s"])[-60:])
                    vol5m = max(1.0, sum(list(h["vol_1s"])[-300:]) / 5.0 if len(h["vol_1s"]) >= 300 else sum(h["vol_1s"])/max(1, len(h["vol_1s"])))
                    vol_boost = float(vol1m) / max(1.0, vol5m)
                    last_cum = float(h["last_cumvol"] or 0.0)
                    h["vol_min"], h["vol_max"] = min(h["vol_min"], last_cum), max(h["vol_max"], last_cum)
                    vol_rank = (last_cum - h["vol_min"]) / (h["vol_max"] - h["vol_min"]) if h["vol_max"] > h["vol_min"] else 0.5
                    depth_ratio = cur_bid_qty / max(1.0, cur_ask_qty)
                    market_heat = max(0.0, min(1.0, 0.5 * min(2.0, vol_boost) / 2.0 + 0.5 * (depth_ratio/(1.0+depth_ratio))))
                    regime = _regime_now()
                    imbalance = cur_bid_qty / (cur_bid_qty + cur_ask_qty) if (cur_bid_qty + cur_ask_qty) > 0 else None
                    obi_val = imbalance if imbalance is not None else 0.5

                    self.buf[sym.code].append(float(cur))
                    m1_bars = h.get("m1_bars", deque())

                    # ==========================================
                    # ★★★ AI処理: 師匠対応版 (RCI, PSAR等計算) ★★★
                    # ==========================================
                    ai_model = self.ai_models.get(code_s)
                    ai_buy = False
                    ai_exit = False
                    
                    # 最低でもテクニカル指標計算用に十分な足数が必要 (RCI52などあるため60本くらい欲しい)
                    # 起動直後はデータがないのでAIは動かないが、DataCollectorが溜めるので時間とともに動き出す
                    if ai_model and len(m1_bars) >= 10: 
                        try:
                            # 1. リアルタイム・テクニカル計算
                            # dequeをDataFrameに変換 (直近100本あれば十分)
                            bars_list = list(m1_bars)
                            recent_bars = bars_list[-100:] 
                            df = pd.DataFrame(recent_bars)
                            
                            # 最新の未確定足(Current)も含めて計算してOK（リアルタイム判断なので）
                            
                            # --- A. 基本データ ---
                            close_series = df["close"]
                            cur_price = float(cur)
                            prev_close = float(df.iloc[-2]["close"]) if len(df) >= 2 else cur_price
                            
                            pct_change = 0.0
                            if prev_close > 0:
                                pct_change = (cur_price - prev_close) / prev_close * 100.0

                            # --- B. 師匠指標の計算 (pandas_ta利用) ---
                            
                            # MACD (12, 26, 9)
                            macd_df = df.ta.macd(close='close', fast=12, slow=26, signal=9)
                            # 最新のヒストグラムを取得 (列名: MACDh_12_26_9)
                            if macd_df is not None and not macd_df.empty:
                                macd_hist = macd_df.iloc[-1].get("MACDh_12_26_9", 0.0)
                            else:
                                macd_hist = 0.0

                            # Bollinger Bands (20, 2)
                            bb_df = df.ta.bbands(close='close', length=20, std=2)
                            if bb_df is not None and not bb_df.empty:
                                bb_p = bb_df.iloc[-1].get("BBP_20_2.0", 0.5) # %B
                            else:
                                bb_p = 0.5

                            # Parabolic SAR (0.02, 0.2)
                            psar_df = df.ta.psar(high='high', low='low', close='close', af0=0.02, af=0.2)
                            # PSARは Long(PSARl) と Short(PSARs) に分かれるので合体
                            sar_dist = 0.0
                            if psar_df is not None and not psar_df.empty:
                                last_row = psar_df.iloc[-1]
                                psar_l = last_row.get("PSARl_0.02_0.2", np.nan)
                                psar_s = last_row.get("PSARs_0.02_0.2", np.nan)
                                sar_val = psar_l if not pd.isna(psar_l) else (psar_s if not pd.isna(psar_s) else cur_price)
                                if sar_val > 0:
                                    sar_dist = (cur_price - sar_val) / sar_val * 100.0

                            # RCI (9, 26) - 自作関数
                            # 処理速度のため、直近データに対して実行
                            rci9_s = calc_rci_series(close_series, 9)
                            rci26_s = calc_rci_series(close_series, 26)
                            rci9 = rci9_s.iloc[-1] if not rci9_s.empty else 0.0
                            rci26 = rci26_s.iloc[-1] if not rci26_s.empty else 0.0
                            
                            # RSI (14)
                            rsi_df = df.ta.rsi(close='close', length=14)
                            rsi_val = rsi_df.iloc[-1] if rsi_df is not None and not rsi_df.empty else 50.0

                            # SMA25 乖離
                            sma25_df = df.ta.sma(close='close', length=25)
                            sma25 = sma25_df.iloc[-1] if sma25_df is not None and not sma25_df.empty else cur_price
                            dist_sma25 = (cur_price - sma25) / sma25 * 100.0 if sma25 > 0 else 0.0

                            # --- C. ポジション情報 ---
                            is_pos = 1.0 if sym.code in self.brk.pos else 0.0
                            pnl_pct = 0.0
                            if is_pos:
                                entry_p = float(self.brk.pos[sym.code]["entry"])
                                pnl_pct = (cur_price - entry_p) / entry_p * 100.0

                            # 旧指標 (互換性のため維持)
                            ma15_val = df["close"].rolling(15).mean().iloc[-1] if len(df) >= 15 else cur_price
                            dist_ma15 = (cur_price - ma15_val) / ma15_val * 100.0 if ma15_val > 0 else 0.0
                            volatility = (df.iloc[-1]["high"] - df.iloc[-1]["low"]) / df.iloc[-1]["open"] * 100.0 if df.iloc[-1]["open"] > 0 else 0.0

                            # --- D. AI入力データの作成 (15次元) ---
                            # step2_env.py の observation_space と完全に一致させる
                            ai_obs = np.array([
                                pct_change,          # 1. 変化率
                                obi_val,             # 2. OBI
                                macd_hist,           # 3. MACDヒスト
                                bb_p,                # 4. BB %B
                                rci9 / 100.0,        # 5. RCI短期 (正規化)
                                rci26 / 100.0,       # 6. RCI中期 (正規化)
                                sar_dist,            # 7. PSAR乖離
                                rsi_val / 100.0,     # 8. RSI (正規化)
                                vol_boost,           # 9. 出来高倍率 (vol_ratio)
                                dist_sma25,          # 10. SMA25乖離
                                is_pos,              # 11. ポジション有無
                                pnl_pct,             # 12. 含み損益率
                                dist_ma15,           # 13. 旧MA乖離
                                volatility,          # 14. ボラティリティ
                                0.0                  # 15. 予備
                            ], dtype=np.float32)
                            
                            # NaN対策
                            ai_obs = np.nan_to_num(ai_obs, nan=0.0, posinf=0.0, neginf=0.0)

                            # 推論実行
                            action, _ = ai_model.predict(ai_obs, deterministic=True)
                            
                            if action == 1:
                                ai_buy = True
                                logging.info(f"🤖 [{code_s}] AI指示: BUY 🔥 (RCI9:{rci9:.0f} MACD:{macd_hist:.1f})")
                            elif action == 2:
                                ai_exit = True
                                logging.info(f"🤖 [{code_s}] AI指示: EXIT 💨 (PnL:{pnl_pct:.2f}%)")

                        except Exception as e:
                            # logging.warning(f"AI predict error: {e}") # うるさいのでコメントアウト可
                            pass
                    # ★★★ AI処理終了 ★★★


                    # ==========================================
                    # エントリー判断ロジック
                    # ==========================================
                    
                    # 1. AIの判断 (AIが自信満々なら従う)
                    if ai_buy:
                        strong_long = True
                        logging.info(f"🚀 [{code_s}] Entry by AI Decision (v6 Hybrid)")
                    
                    else:
                        # 2. ルールベース判定 (AI未稼働時のバックアップ)
                        # 千鳥足廃止。簡単なトレンドフォローのみ。
                        
                        # 条件A: 1分足レベルの上昇トレンド
                        ma15_long_ok = False
                        if len(m1_bars) >= 15:
                            ma_now = sum([b["close"] for b in list(m1_bars)[-15:]]) / 15.0
                            if float(cur) > ma_now: ma15_long_ok = True
                        else:
                            ma15_long_ok = True # データ不足時は許可

                        # 条件B: 買い板圧力
                        board_ok = (obi_val >= 0.55)
                        
                        # 条件C: ボラティリティ
                        vol_ok = (vol_boost > 1.0)

                        if ma15_long_ok and board_ok and vol_ok:
                            strong_long = True
                            logging.debug(f"✨ [{code_s}] Entry by Logic (Trend+Board)")
                        else:
                            strong_long = False
                    
                    strong_short = False 

                    # 3. 日足トレンドフィルター (200日線など)
                    # ここは手動リスト(config)で管理するか、朝イチで取得したデータを使う想定
                    # 今回はコード上では実装せず、運用ルール(人間が選別)に委ねる

                    # Guards
                    allowed = (not self.gk_enabled) or (code_s in self.allowed_symbols)
                    cool_ok = (time.time() >= self._cooldown_until.get(code_s, 0.0))
                    can_trade_window = self.brk.can_trade_now(is_entry=True)
                    can_new_entry = self._can_new_entry_now()
                    under_max_positions = self._respect_max_open_positions()
                    interval_ok = self._respect_min_order_interval(sym)
                    pos_open = sym.code in self.brk.pos
                    
                    decided_qty = int(getattr(sym, "qty", 100) or 100)

                    # Router (戦略決定)
                    cx = Context(code=code_s, price_step=int(max(1, round(float(tick)))), spread_ticks=float(spread_ticks or 0.0), heat=float(obi_val), obi=float(obi_val), vwap_distance_pct=float(vwap_dist_pct), vol_boost_ratio=float(vol_boost), vol_rank=float(vol_rank), market_temp=float(market_heat))
                    strategy = self.router.decide(cx)

                    if float(tick) >= 5.0 and strategy == "SCALP": strategy = "OBSERVE"
                    if (spread_ticks is not None) and (spread_ticks > SWING["max_spread_ticks"]): strategy = "OBSERVE"
                    if strategy == "SCALP": strategy = "MINI_SWING" # スイング寄り設定
                    
                    market_hot_enough = (market_heat >= (0.3 if regime == "OPEN" else 0.4))
                    if ai_buy: market_hot_enough = True

                    # Entry Execution
                    if (not pos_open) and (strong_long or strong_short) and (strategy != "OBSERVE") and can_trade_window and can_new_entry and under_max_positions and interval_ok and allowed and cool_ok and (self._daily_pnl > -self._daily_stop) and market_hot_enough:
                        side = "BUY" if strong_long else "SELL"
                        
                        if self.cfg.paper_trading:
                            logging.info(f"PAPER {side} {sym.code} cur={cur}")
                            self.brk.pos[sym.code] = {"entry": float(cur), "qty": int(decided_qty), "ts": time.time(), "side": side, "strategy": strategy}
                        else:
                            if side == "BUY":
                                 logging.info(f"LIVE BUY {sym.code} cur={cur}")
                                 res = self.brk.buy(sym, b, cur)
                                 if res and res.get("Result") == 0:
                                     self.brk.pos[sym.code] = {"entry": float(cur), "qty": int(decided_qty), "ts": time.time(), "side": "BUY", "strategy": strategy}
                            else:
                                 logging.info(f"LIVE SHORT {sym.code} @ {cur}")
                                 res = self.api.order(sym.code, sym.exchange, "2", int(decided_qty), 0, 20, 3)
                                 if res and res.get("Result") == 0:
                                     self.brk.pos[sym.code] = {"entry": float(cur), "qty": int(decided_qty), "ts": time.time(), "side": "SELL", "strategy": strategy}
                        
                        self.brk.last_order_ts[sym.code] = time.time()

                    # ===== EXIT 判定 =====
                    pos = self.brk.pos.get(sym.code)
                    if pos:
                        side = pos.get("side", "BUY")
                        strategy = pos.get("strategy", "MINI_SWING")
                        entry_px = float(pos["entry"])
                        qty = int(pos["qty"])
                        base_ts = float(pos.get("fill_ts") or pos.get("ts") or time.time())
                        held_sec = time.time() - base_ts

                        if isinstance(bid, (int, float)) and isinstance(ask, (int, float)):
                             exit_target_px = float(bid) if side == "BUY" else float(ask)
                        else:
                             exit_target_px = float(cur)

                        p_tp   = self._get_sym_param(sym, "tp_ticks_cap", SWING["tp_ticks_cap"])
                        p_sl   = self._get_sym_param(sym, "sl_ticks_cap", SWING["sl_ticks_cap"])
                        p_time = self._get_sym_param(sym, "max_hold_sec", SWING["max_hold_sec"])
                        
                        if side == "BUY":
                            pnl_ticks = (exit_target_px - entry_px) / max(tick, 0.5)
                            hit_tp = (exit_target_px >= entry_px + p_tp * tick)
                            hit_sl = (exit_target_px <= entry_px - p_sl * tick)
                        else:
                            pnl_ticks = (entry_px - exit_target_px) / max(tick, 0.5)
                            hit_tp = (exit_target_px <= entry_px - p_tp * tick)
                            hit_sl = (exit_target_px >= entry_px + p_sl * tick)

                        hit_time = held_sec >= p_time
                        hit_force = bool(force_close)

                        do_exit, reason = False, "COND"
                        
                        # AI Exit or Hard Rules
                        if ai_exit:
                            do_exit, reason = True, "AI_EXIT"
                        elif hit_tp or hit_sl or hit_time or hit_force:
                            do_exit, reason = True, "COND"
                            if hit_tp: reason = "TP"
                            elif hit_sl: reason = "SL"
                            elif hit_time: reason = "TIME"
                            elif hit_force: reason = "FORCE"

                        if do_exit:
                             if self.cfg.paper_trading:
                                 pnl = pnl_ticks * tick * qty
                                 self._daily_pnl += pnl
                                 logging.info(f"PAPER EXIT {side} {sym.code} pnl={pnl:.0f} reason={reason}")
                                 del self.brk.pos[sym.code]
                             else:
                                 logging.info(f"LIVE EXIT {side} {sym.code} reason={reason}")
                                 if side == "BUY": self.brk.sell(sym, qty=qty)
                                 else: self.api.order(sym.code, sym.exchange, "1", qty, 0, 20, 3)
                                 del self.brk.pos[sym.code]
                             self._cooldown_until[code_s] = time.time() + self._cooldown_after_exit_sec
                        
                        if held_sec % 10 < 0.1:
                            logging.debug(f"WATCH {sym.code}: TP={p_tp} SL={p_sl} TIME={p_time}s")

                    now_sec = time.time()
                    if now_sec - self._last_tele_write >= 1.0:
                        try:
                            self.tele.save_atomic({"ts": jst_iso(), "pnl": self._daily_pnl, "streak": self._streak})
                            self._last_tele_write = now_sec
                        except Exception:
                            logging.warning("telemetry write failed")

                time.sleep(LOOP_SLEEP_SEC)
            except Exception as e:
                logging.exception("controller main loop error: %s", e)
                time.sleep(0.2)