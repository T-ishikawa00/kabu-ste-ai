# -*- coding: utf-8 -*-
"""
Controller v10.1 (Stable Short Selling Edition)
==================================================
修正点:
- AIモデルが存在しない場合でもエラー落ちせず、ルールベース（モメンタム）で動作するように修正
- 既存のロジック（MACD, RCI等）は全て維持
"""

import os, time, logging, json, glob
import datetime
import random 
from collections import deque
from typing import Deque, Dict, Any, List, Set
import threading

# ★AI・データ分析用ライブラリ
import numpy as np
import pandas as pd
import pandas_ta as ta
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

# ★追加: 通知機能
from notifier import Notifier

# ===== パラメータ（デフォルト値） =====
SWING = dict(
    tp_ticks_cap=20,
    sl_ticks_cap=10,
    max_hold_sec=300,
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

LOOP_SLEEP_SEC  = 0.2
GRACE_SEC       = 30.0

# ★監査対応: 資金管理上限 (Shadow Ledger Limit)
SHADOW_LIMIT_JPY = 2000000  # 200万円

# ==========================================
# ★Phase 2: ログ設定関数
# ==========================================
def setup_logger():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    ensure_dir("logs")
    file_handler = logging.FileHandler(f"logs/strategy_{datetime.date.today()}.log", encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO) 
    console_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    logging.info("📝 Logger initialized: Console=INFO, File=DEBUG")


# ==========================================
# RCI計算用関数
# ==========================================
def calc_rci_series(series: pd.Series, period=9) -> pd.Series:
    rci_values = []
    if len(series) < period:
        return pd.Series([np.nan] * len(series), index=series.index)

    time_rank = np.arange(1, period + 1)
    
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

def _safe_scalar(val, default=0.0) -> float:
    try:
        if hasattr(val, "item"):
            return float(val.item())
        if isinstance(val, (list, tuple, np.ndarray)):
            if len(val) > 0:
                return float(val[0])
            return default
        if pd.isna(val):
            return default
        return float(val)
    except Exception:
        return default


class Controller:
    def __init__(self, cfg):
        setup_logger()
        self.cfg = cfg
        self.api = KabusAPI(cfg)
        self.brk = Broker(self.api, cfg)

        maxn = max(1, int(getattr(cfg, "max_concurrent_symbols", len(cfg.symbols) or 1)))
        self.syms = cfg.symbols[:maxn]
        
        self.target_codes = [str(s.code) for s in self.syms]
        self.sym_map = {str(s.code): s for s in self.syms}

        self.buf: Dict[str, Deque[float]] = {s.code: deque(maxlen=5) for s in self.syms}
        self.tele = Telemetry(cfg.telemetry_path)
        self.models = ModelRegistry()
        self.models.load_all(self.syms)
        self._last_tele_write = 0.0

        self.launch_time = time.time()
        self.STARTUP_COOLDOWN = 60

        self.collector = DataCollector()
        logging.info("💾 DataCollector initialized")

        self.notifier = Notifier(cfg)
        self.notifier.send("🤖 システム起動しました (Version 10.1 - Stable Short Selling)", "startup")

        # ==========================================
        # AIモデルのロード
        # ==========================================
        self.ai_models = {}
        logging.info("🤖 AIモデルのロードを開始します...")
        
        for s in self.syms:
            code = str(s.code)
            loaded = False
            for v in range(30, 0, -1):
                model_path = f"model/model_{code}_v{v}"
                if os.path.exists(model_path + ".zip"):
                    try:
                        self.ai_models[code] = PPO.load(model_path)
                        logging.info(f"  ✅ [{code}] AI搭載完了 (v{v})")
                        loaded = True
                        break 
                    except Exception as e:
                        logging.warning(f" ⚠️ [{code}] v{v} ロード失敗: {e}")
            
            if not loaded:
                logging.info(f"  ⚪ [{code}] AIなし (ルールベースのみで稼働)")

        self._next_board_at = {str(s.code): 0.0 for s in self.syms}
        self._backoff        = {str(s.code): 0.0 for s in self.syms}
        self._board_min_iv = max(
            0.2,
            float(getattr(getattr(self.cfg, "safety", object()), "board_min_interval_ms", 900)) / 1000.0,
        )
        self._qps_limit      = int(getattr(getattr(self.cfg, "safety", object()), "api_qps_limit", 6))
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
                "vol_1s":    deque(maxlen=300),
                "obi":       deque(maxlen=300),
                "px_1s":     deque(maxlen=10),
                "last_cumvol": None,
                "last_ts": 0.0,
                "pv": 0.0,
                "vv": 0.0,
                "vol_min": 1e18,
                "vol_max": 0.0,
                "last_bid_qty": 0.0,
                "last_ask_qty": 0.0,
                "m1_cur": None,
                "m1_bars": deque(maxlen=600) 
            }
        
        self._preload_history()

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
        
        self.boards = {} 

        self._last_entry_check: Dict[str, float] = {}
        self._entry_check_interval = 1.0
        self._last_ai_signal: Dict[str, tuple] = {}
        
        self._processing_entry: Set[str] = set()
        self._processing_lock = threading.Lock()

        # ★v10.0: 空売り設定の読み込み
        short_cfg = getattr(self.cfg, "short", object())
        self._short_enabled = bool(getattr(short_cfg, "enabled", False))
        self._short_allowed = set(getattr(short_cfg, "allowed_symbols", []))
        
        logging.info(f"🔧 Controller v10.1 initialized")
        logging.info(f"  📉 Short Selling: {'ENABLED' if self._short_enabled else 'DISABLED'}")
        if self._short_enabled:
            logging.info(f"  📉 Short Allowed Symbols: {len(self._short_allowed)}")

    def _preload_history(self):
        logging.info("📚 過去データを読み込み中...")
        for s in self.syms:
            code = str(s.code)
            try:
                files = glob.glob(f"data_raw/*/{code}.csv")
                if not files:
                    if os.path.exists(f"data/merged_{code}.csv"):
                        files = [f"data/merged_{code}.csv"]
                if not files: continue

                latest_file = sorted(files)[-1]
                df = pd.read_csv(latest_file)
                if df.empty: continue
                
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                elif 'datetime' in df.columns:
                     df['datetime'] = pd.to_datetime(df['datetime'])
                     df['timestamp'] = df['datetime']
                
                df.set_index('timestamp', inplace=True)
                df_numeric = df.select_dtypes(include=[np.number])
                
                price_col = 'price' if 'price' in df_numeric.columns else 'close'
                if price_col not in df_numeric.columns:
                    continue
                    
                ohlc = df_numeric[price_col].resample('1T').ohlc()
                vol = df_numeric.get('volume', df_numeric.get('size', pd.Series(0, index=df.index))).resample('1T').sum()
                
                recent_bars = ohlc.tail(500)
                recent_vol = vol.tail(500)
                
                count = 0
                for ts, row in recent_bars.iterrows():
                    if pd.isna(row['open']): continue
                    bar = {
                        "start": ts,
                        "open": float(row['open']),
                        "high": float(row['high']),
                        "low": float(row['low']),
                        "close": float(row['close']),
                        "vol": float(recent_vol.get(ts, 0))
                    }
                    self.hist[code]["m1_bars"].append(bar)
                    count += 1
                
                if count > 0:
                    logging.info(f"  💾 [{code}] ウォームアップ完了: {count}本")
                    
            except Exception as e:
                logging.warning(f"  ⚠️ [{code}] データロード失敗: {e}")

    def _can_new_entry_now(self) -> bool:
        naa = getattr(getattr(self.cfg, "risk", object()), "no_new_entry_after", None)
        return not (naa and _time_ge(naa))

    def _can_short_entry_now(self) -> bool:
        """空売り専用の時間チェック"""
        short_cfg = getattr(self.cfg, "short", object())
        naa = getattr(short_cfg, "no_entry_after", "14:30")
        return not (naa and _time_ge(naa))

    def _force_close_due(self, side: str = "BUY") -> bool:
        """強制決済時刻チェック（空売りは早め）"""
        if side == "SHORT":
            short_cfg = getattr(self.cfg, "short", object())
            fct = getattr(short_cfg, "force_close_time", "14:50")
        else:
            fct = getattr(getattr(self.cfg, "risk", object()), "force_close_time", "14:55")
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

    def _recover_ghost_positions(self):
        logging.info("👻 証券口座の建玉を確認中...")
        try:
            positions = self.brk._open_positions()
            if positions is None:
                logging.warning("⚠️ 建玉取得失敗")
                return
                
            recovered_count = 0
            
            for p in positions:
                code = str(p.get("Symbol"))
                qty = int(p.get("LeavesQty") or p.get("HoldQty") or p.get("Qty") or 0)
                
                is_target = any(str(s.code) == code for s in self.syms)
                if is_target and qty > 0 and code not in self.brk.pos:
                    entry_price = float(p.get("Price") or p.get("EntryPrice") or 0.0)
                    # Side: "1"=売り（空売り）, "2"=買い
                    api_side = str(p.get("Side"))
                    if api_side == "1":
                        side = "SHORT"
                    else:
                        side = "BUY"
                    
                    logging.warning(f"🧟 ゴースト建玉を発見・回収: {code} {side} {qty}株 @ {entry_price}")
                    self.notifier.send(f"🧟 ゴースト建玉を回収: {code} {side} {qty}株", "error")

                    self.brk.pos[code] = {
                        "entry": entry_price,
                        "qty": qty,
                        "ts": time.time(),
                        "side": side,
                        "strategy": "RECOVERED",
                        "fill_ts": time.time()
                    }
                    recovered_count += 1
            
            if recovered_count == 0:
                logging.info("✨ 未管理の建玉はありません。クリーンです。")
                
        except Exception as e:
            logging.error(f"ゴースト回収中にエラー: {e}")

    def _can_attempt_entry(self, code_s: str) -> tuple:
        """エントリー判定を試みてよいかチェック"""
        now_t = time.time()
        
        if code_s in self.brk.pos:
            return False, "has_position"
        
        with self._processing_lock:
            if code_s in self._processing_entry:
                return False, "already_processing"
        
        last_check = self._last_entry_check.get(code_s, 0)
        if now_t - last_check < self._entry_check_interval:
            return False, "entry_rate_limit"
        
        if hasattr(self.brk, 'can_order'):
            can, reason = self.brk.can_order(code_s)
            if not can:
                return False, f"broker_{reason}"
        
        if not self._respect_max_open_positions():
            return False, "max_positions"
        
        if not self._can_new_entry_now():
            return False, "time_guard"
        
        cooldown_until = self._cooldown_until.get(code_s, 0)
        if now_t < cooldown_until:
            return False, "exit_cooldown"
        
        if now_t - self.launch_time < self.STARTUP_COOLDOWN:
            return False, "startup_cooldown"
        
        if self._daily_pnl <= -self._daily_stop:
            return False, "daily_loss_limit"
        
        return True, "ok"

    # ======================================================
    # ★v10.0: パニックエグジット（特別気配・ストップ高接近）
    # ======================================================
    def _check_panic_exit(self, code_s: str, sym, board: dict) -> bool:
        """
        空売りポジション保有中の緊急脱出チェック
        Returns: True if panic exit executed
        """
        pos = self.brk.pos.get(code_s)
        if not pos:
            return False
        
        side = pos.get("side", "BUY")
        if side != "SHORT":
            return False
        
        short_cfg = getattr(self.cfg, "short", object())
        
        # 1. 特別気配チェック（買い方向）
        if bool(getattr(short_cfg, "panic_on_special_quote", True)):
            sign = str(board.get("BidAskSign", ""))
            # 0102 = 特別気配（買い方向で価格が飛ぶ前兆）
            if sign == "0102":
                logging.warning(f"🚨 [{code_s}] 特別気配(0102)検出 - パニックエグジット!")
                self.notifier.send(f"🚨 緊急決済: {code_s} (特別気配検出)", "error")
                
                if hasattr(self.brk, 'panic_exit'):
                    self.brk.panic_exit(sym, "SPECIAL_QUOTE")
                else:
                    qty = pos.get("qty", 100)
                    self.brk.cover(sym, qty)
                
                if code_s in self.brk.pos:
                    del self.brk.pos[code_s]
                return True
        
        # 2. ストップ高接近チェック
        panic_distance = float(getattr(short_cfg, "panic_distance_pct", 1.0))
        upper_limit = board.get("UpperLimit")
        current = board.get("CurrentPrice")
        
        if upper_limit and current and upper_limit > 0 and current > 0:
            distance_pct = (upper_limit - current) / current * 100
            if distance_pct < panic_distance:
                logging.warning(f"🚨 [{code_s}] ストップ高接近({distance_pct:.1f}%) - パニックエグジット!")
                self.notifier.send(f"🚨 緊急決済: {code_s} (ストップ高接近)", "error")
                
                if hasattr(self.brk, 'panic_exit'):
                    self.brk.panic_exit(sym, "NEAR_LIMIT_UP")
                else:
                    qty = pos.get("qty", 100)
                    self.brk.cover(sym, qty)
                
                if code_s in self.brk.pos:
                    del self.brk.pos[code_s]
                return True
        
        return False

    # ======================================================
    # ★ Phase 2 対応: ロジック切り出し (統合版)
    # ======================================================
    def process_symbol(self, code_s: str, board: dict = None):
        """1銘柄ぶんのトレード判断ロジック"""
        sym = self.sym_map.get(code_s)
        if not sym: return

        has_position = code_s in self.brk.pos
        
        if not has_position:
            can_entry, reason = self._can_attempt_entry(code_s)
            if not can_entry:
                return

        # --- 1. データ取得とキャッシュ更新 ---
        if board is not None:
            if code_s not in self.boards:
                self.boards[code_s] = {}
            self.boards[code_s].update(board)
            current_board = self.boards[code_s]
        else:
            try:
                nowt = time.time()
                if nowt < self._backoff.get(code_s, 0.0): return
                if nowt < self._next_board_at.get(code_s, 0.0): return
                
                current_board = self.api.board(sym.code, sym.exchange)
                
                self._next_board_at[code_s] = nowt + self._board_min_iv
                self.boards[code_s] = current_board
                self.collector.log_board(code_s, current_board)
                
            except Exception as e:
                msg = str(e)
                if "429" in msg or "API実行回数エラー" in msg:
                    wait = max(2.0, 2.0 * (1.0 + (hash(code_s) % 50) / 100.0))
                    self._backoff[code_s] = time.time() + wait
                    self._next_board_at[code_s] = time.time() + wait
                return

        if not current_board: return

        # ★v10.0: パニックエグジットチェック（空売りポジション保有時）
        if has_position and self._check_panic_exit(code_s, sym, current_board):
            return  # パニックエグジット実行済み

        # --- 2. 指標計算 ---
        market_data = self._update_indicators(code_s, current_board)
        if not market_data: return

        # --- 3. AI判断 & トレード実行 ---
        self._execute_trade(sym, current_board, market_data)


    def _update_indicators(self, code_s, b):
        """ボード情報からテクニカル指標を計算・更新する"""
        nowt = time.time()
        
        sym = self.sym_map.get(code_s)
        if not sym: return None

        if code_s not in self.boards: self.boards[code_s] = {}
        self.boards[code_s].update(b)
        full_b = self.boards[code_s]

        cur = (full_b.get("CurrentPrice") or full_b.get("BidPrice") or full_b.get("AskPrice"))
        if cur is None: return None

        h = self.hist[code_s] 

        bid = full_b.get("BidPrice")
        ask = full_b.get("AskPrice")
        bid_qty = float(full_b.get("BidQty") or 0)
        ask_qty = float(full_b.get("AskQty") or 0)

        over_sell_qty = float(full_b.get("OverSellQty") or 0)
        under_buy_qty = float(full_b.get("UnderBuyQty") or 0)

        tick = tick_size(sym.code, cur, full_b)
        spread_ticks = None
        if isinstance(ask, (int, float)) and isinstance(bid, (int, float)):
            spr = ask - bid
            if tick > 0: spread_ticks = spr / max(tick, 0.5)

        h["last_bid_qty"], h["last_ask_qty"] = bid_qty, ask_qty

        if nowt - h["last_ts"] >= 1.0:
            if spread_ticks is not None: h["spr_ticks"].append(float(spread_ticks))
        
        if (bid_qty + ask_qty) > 0:
            h["obi"].append(bid_qty / (bid_qty + ask_qty))

        dv = 0.0
        cumv = full_b.get("TradingVolume") or full_b.get("CurrentVolume") or full_b.get("Volume")
        if isinstance(cumv, (int, float)):
            if h["last_cumvol"] is not None:
                dv = max(0, float(cumv) - float(h["last_cumvol"]))
                h["vol_1s"].append(dv)
                h["pv"] += float(cur) * float(dv)
                h["vv"] += float(dv)
            h["last_cumvol"] = float(cumv)
        h["px_1s"].append(float(cur))
        h["last_ts"] = nowt

        # 1分足生成
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

        # Features
        vwap = (h["pv"]/h["vv"]) if h["vv"] > 0 else float(cur)
        vwap_dist_pct = abs(float(cur) - vwap) / max(1.0, vwap) * 100.0
        vol1m = sum(list(h["vol_1s"])[-60:])
        vol5m = max(1.0, sum(list(h["vol_1s"])[-300:]) / 5.0 if len(h["vol_1s"]) >= 300 else sum(h["vol_1s"])/max(1, len(h["vol_1s"])))
        vol_boost = float(vol1m) / max(1.0, vol5m)
        last_cum = float(h["last_cumvol"] or 0.0)
        h["vol_min"], h["vol_max"] = min(h["vol_min"], last_cum), max(h["vol_max"], last_cum)
        vol_rank = (last_cum - h["vol_min"]) / (h["vol_max"] - h["vol_min"]) if h["vol_max"] > h["vol_min"] else 0.5
        depth_ratio = bid_qty / max(1.0, ask_qty)
        market_heat = max(0.0, min(1.0, 0.5 * min(2.0, vol_boost) / 2.0 + 0.5 * (depth_ratio/(1.0+depth_ratio))))
        regime = _regime_now()
        imbalance = bid_qty / (bid_qty + ask_qty) if (bid_qty + ask_qty) > 0 else None
        obi_val = imbalance if imbalance is not None else 0.5

        self.buf[sym.code].append(float(cur))
        
        ndbi = (under_buy_qty - over_sell_qty) / (under_buy_qty + over_sell_qty + 1e-9)
        hsr = under_buy_qty / (bid_qty + 1e-9)

        return {
            "cur": cur, "bid": bid, "ask": ask, "tick": tick, "spread_ticks": spread_ticks,
            "obi_val": obi_val, "vwap_dist_pct": vwap_dist_pct, "vol_boost": vol_boost,
            "vol_rank": vol_rank, "market_heat": market_heat, "regime": regime,
            "dt_now": dt_now, "m1_bars": h["m1_bars"],
            "ndbi": ndbi, "hsr": hsr,
            "over_sell_qty": over_sell_qty, "under_buy_qty": under_buy_qty
        }


    def _execute_trade(self, sym, b, mk):
        """AI判断と発注実行（v10.0: 空売り対応）"""
        code_s = str(sym.code)
        cur = mk['cur']
        m1_bars = mk['m1_bars']
        
        pos_open = code_s in self.brk.pos
        
        ai_model = self.ai_models.get(code_s)
        ai_buy = False
        ai_sell = False  # ★v10.0: 空売りシグナル
        ai_exit = False
        
        # ★★★ AI判定 ★★★
        should_run_ai = False
        if pos_open:
            should_run_ai = True
        else:
            now_t = time.time()
            last_check = self._last_entry_check.get(code_s, 0)
            if now_t - last_check >= self._entry_check_interval:
                should_run_ai = True
                self._last_entry_check[code_s] = now_t
        
        if should_run_ai:
            if ai_model and len(m1_bars) >= 5:
                try:
                    bars_list = list(m1_bars)
                    recent_bars = bars_list[-100:] 
                    df = pd.DataFrame(recent_bars)
                    
                    cols = ["open", "high", "low", "close", "vol"]
                    df = df[cols].apply(pd.to_numeric, errors='coerce')

                    cur_price = _safe_scalar(cur)
                    close_series = df["close"]

                    if len(df) >= 2:
                        prev_close = _safe_scalar(df['close'].iloc[-2]) 
                    else:
                        prev_close = cur_price
                    
                    pct_change = 0.0
                    if prev_close != 0:
                        pct_change = (cur_price - prev_close) / prev_close * 100.0

                    # MACD
                    macd_df = df.ta.macd(close='close', fast=12, slow=26, signal=9)
                    macd_hist = 0.0
                    if macd_df is not None and not macd_df.empty:
                        macd_hist = _safe_scalar(macd_df.iloc[-1].get("MACDh_12_26_9", 0.0))

                    # Bollinger Bands
                    bb_df = df.ta.bbands(close='close', length=20, std=2)
                    bb_p = 0.5
                    if bb_df is not None and not bb_df.empty:
                        bb_p = _safe_scalar(bb_df.iloc[-1].get("BBP_20_2.0", 0.5))

                    # Parabolic SAR
                    psar_df = df.ta.psar(high='high', low='low', close='close', af0=0.02, af=0.2)
                    sar_dist = 0.0
                    if psar_df is not None and not psar_df.empty:
                        last_row = psar_df.iloc[-1]
                        psar_l = _safe_scalar(last_row.get("PSARl_0.02_0.2", np.nan), default=np.nan)
                        psar_s = _safe_scalar(last_row.get("PSARs_0.02_0.2", np.nan), default=np.nan)
                        sar_val = psar_l if not pd.isna(psar_l) else (psar_s if not pd.isna(psar_s) else cur_price)
                        
                        if sar_val > 0:
                            sar_dist = (cur_price - sar_val) / sar_val * 100.0

                    # RCI (9, 26)
                    rci9_s = calc_rci_series(close_series, 9)
                    rci26_s = calc_rci_series(close_series, 26)
                    rci9 = _safe_scalar(rci9_s.iloc[-1]) if not rci9_s.empty else 0.0
                    rci26 = _safe_scalar(rci26_s.iloc[-1]) if not rci26_s.empty else 0.0
                    
                    # RSI (14)
                    rsi_df = df.ta.rsi(close='close', length=14)
                    rsi_val = 50.0
                    if rsi_df is not None and not rsi_df.empty:
                        rsi_val = _safe_scalar(rsi_df.iloc[-1], default=50.0)

                    # SMA25
                    sma25_df = df.ta.sma(close='close', length=25)
                    sma25 = cur_price
                    if sma25_df is not None and not sma25_df.empty:
                        sma25 = _safe_scalar(sma25_df.iloc[-1], default=cur_price)
                    dist_sma25 = (cur_price - sma25) / sma25 * 100.0 if sma25 > 0 else 0.0

                    
                    # ポジション情報 (Short対応)
                    is_pos = 0.0
                    pnl_pct = 0.0
                    current_hold_duration = 0.0 # 経過時間正規化用
                    
                    if pos_open:
                        pos_data = self.brk.pos[code_s]
                        entry_p = float(pos_data["entry"])
                        pos_side = pos_data.get("side", "BUY")
                        
                        # 学習環境(step2_env)に合わせて Shortは -1.0, Longは 1.0 にする
                        if pos_side == "SHORT":
                            is_pos = -1.0
                            pnl_pct = (entry_p - cur_price) / entry_p * 100.0
                        else:
                            is_pos = 1.0
                            pnl_pct = (cur_price - entry_p) / entry_p * 100.0
                            
                        # 保有時間の計算 (学習環境に合わせる)
                        base_ts = float(pos_data.get("fill_ts") or pos_data.get("ts") or time.time())
                        held_sec = time.time() - base_ts
                        # SWING["max_hold_sec"] (3600秒) で正規化
                        current_hold_duration = min(1.0, held_sec / 3600.0)

                    ma15_val = cur_price
                    if len(df) >= 15:
                        ma15_val = _safe_scalar(df["close"].rolling(15).mean().iloc[-1], default=cur_price)
                    
                    dist_ma15 = (cur_price - ma15_val) / ma15_val * 100.0 if ma15_val > 0 else 0.0
                    
                    volatility = 0.0
                    open_p = _safe_scalar(df.iloc[-1]["open"])
                    if open_p > 0:
                        high_p = _safe_scalar(df.iloc[-1]["high"])
                        low_p = _safe_scalar(df.iloc[-1]["low"])
                        volatility = (high_p - low_p) / open_p * 100.0

                    # AI入力 (17次元)
                    # step2_env.py と完全に一致させる
                    ai_obs = np.array([
                        pct_change, 
                        mk['obi_val'], 
                        macd_hist, 
                        bb_p, 
                        rci9 / 100.0, 
                        rci26 / 100.0,
                        0.0,  # step2_envに合わせてSARは0.0固定にする（不一致防止）
                        rsi_val / 100.0, 
                        mk['vol_boost'], 
                        dist_sma25, 
                        is_pos,        # ★修正済み (-1.0, 0.0, 1.0)
                        pnl_pct, 
                        dist_ma15, 
                        volatility, 
                        current_hold_duration, # ★修正済み (経過時間)
                        mk.get('ndbi', 0.0), 
                        mk.get('hsr', 0.0)
                    ], dtype=np.float32)
                    
                    
                    ai_obs = np.nan_to_num(ai_obs, nan=0.0, posinf=0.0, neginf=0.0)

                    req_dim = ai_model.observation_space.shape[0]
                    cur_dim = len(ai_obs)
                    
                    if req_dim == cur_dim:
                        action, _ = ai_model.predict(ai_obs, deterministic=True)
                        
                        # 現在のポジション方向を取得
                        current_side = None
                        if pos_open:
                            current_side = self.brk.pos[code_s].get("side", "BUY")

                        # Action 1: BULL (強気)
                        if action == 1:
                            if current_side == "SHORT":
                                # 空売りを持っていたら -> 決済 (Cover)
                                ai_exit = True
                                logging.debug(f"🤖 [{code_s}] AI指示: BULL (Cover Short) 🚀")
                            elif not pos_open:
                                # ノーポジなら -> 新規買い
                                ai_buy = True
                                logging.debug(f"🤖 [{code_s}] AI指示: BULL (Long Entry) 🔥")
                            # ※買いポジ保有中なら何もしない(Hold)

                        # Action 2: BEAR (弱気)
                        elif action == 2:
                            if current_side == "BUY":
                                # 買いを持っていたら -> 決済 (Sell)
                                ai_exit = True
                                logging.debug(f"🤖 [{code_s}] AI指示: BEAR (Sell Long) 📉")
                            elif not pos_open:
                                # ノーポジなら -> 新規空売り
                                ai_sell = True
                                logging.debug(f"🤖 [{code_s}] AI指示: BEAR (Short Entry) 💀")
                            # ※売りポジ保有中なら何もしない(Hold)

                except Exception as e:
                    pass
            
            else:
                # ★★★ AIモデルがない時のフォールバック (単純モメンタム) ★★★
                # これがないと「AIモデル待ち」で一生動かない
                px_hist = list(self.hist[code_s]['px_1s'])
                if len(px_hist) >= 5:
                    p_now = px_hist[-1]
                    p_prev = px_hist[-5]
                    tick_val = mk['tick']
                    
                    if not pos_open:
                        if p_now > p_prev + tick_val:
                            ai_buy = True
                        elif p_now < p_prev - tick_val and self._short_enabled:
                            ai_sell = True

        
        # --- ENTRY判定 ---
        strong_long = ai_buy
        strong_short = ai_sell and self._short_enabled  # 空売り有効時のみ

        allowed = (code_s in self.allowed_symbols) if self.gk_enabled else True

        can_trade_window = self.brk.can_trade_now(is_entry=True)
        can_new_entry = self._can_new_entry_now()
        can_short_entry = self._can_short_entry_now()  # ★空売り専用時間チェック
        under_max_positions = self._respect_max_open_positions()
        interval_ok = self._respect_min_order_interval(sym)
        cool_ok = time.time() >= self._cooldown_until.get(code_s, 0.0)
        
        decided_qty = int(getattr(sym, "qty", 100) or 100)

        # Shadow Ledger
        current_exposure = 0.0
        for p_code, p_val in self.brk.pos.items():
            current_exposure += float(p_val["entry"]) * int(p_val["qty"])
        new_cost = float(cur) * decided_qty
        if (strong_long or strong_short) and (current_exposure + new_cost > SHADOW_LIMIT_JPY):
            logging.debug(f"⛔ [{code_s}] Shadow Ledger Block")
            strong_long = False
            strong_short = False

        # Router
        cx = Context(
            code=code_s, 
            price_step=int(max(1, round(float(mk['tick'])))), 
            spread_ticks=float(mk['spread_ticks'] or 0.0), 
            heat=float(mk['obi_val']), 
            obi=float(mk['obi_val']), 
            vwap_distance_pct=float(mk['vwap_dist_pct']), 
            vol_boost_ratio=float(mk['vol_boost']), 
            vol_rank=float(mk['vol_rank']), 
            market_temp=float(mk['market_heat'])
        )
        strategy = self.router.decide(cx)

        if float(mk['tick']) >= 5.0 and strategy == "SCALP": strategy = "OBSERVE"
        if (mk['spread_ticks'] is not None) and (mk['spread_ticks'] > SWING["max_spread_ticks"]): strategy = "OBSERVE"
        if strategy == "SCALP": strategy = "MINI_SWING" 
        
        market_hot_enough = (mk['market_heat'] >= (0.3 if mk['regime'] == "OPEN" else 0.4))

        # ★★★ Entry Execution ★★★
        if (not pos_open) and (strategy != "OBSERVE") and can_trade_window and under_max_positions and interval_ok and allowed and cool_ok and (self._daily_pnl > -self._daily_stop) and market_hot_enough:
            
            # ★BUYエントリー
            if strong_long and can_new_entry:
                with self._processing_lock:
                    if code_s in self._processing_entry:
                        return
                    self._processing_entry.add(code_s)
                
                try:
                    logging.debug(f"🚀 [{code_s}] BUY Entry")
                    
                    if self.cfg.paper_trading:
                        logging.info(f"PAPER BUY {code_s} cur={cur}")
                        self.brk.pos[code_s] = {"entry": float(cur), "qty": int(decided_qty), "ts": time.time(), "side": "BUY", "strategy": strategy}
                    else:
                        res = self.brk.buy(sym, b, cur)
                        if res and res.get("Result") == 0:
                            logging.info(f"LIVE BUY {code_s} cur={cur}")
                            if code_s in self.brk.pos:
                                self.notifier.send(f"🚀 BUY: {code_s} @ {cur}円", "entry")
                        else:
                            logging.warning(f"⚠️ BUY注文失敗 {code_s}: {res}")
                    
                    self.brk.last_order_ts[code_s] = time.time()
                    
                finally:
                    with self._processing_lock:
                        self._processing_entry.discard(code_s)
            
            # ★SHORTエントリー（v10.0新機能）
            elif strong_short and can_short_entry:
                # 空売り安全チェック
                can_short, reason = self.brk.can_short(code_s, b)
                if not can_short:
                    logging.debug(f"⛔ [{code_s}] SHORT blocked: {reason}")
                else:
                    with self._processing_lock:
                        if code_s in self._processing_entry:
                            return
                        self._processing_entry.add(code_s)
                    
                    try:
                        logging.info(f"📉 [{code_s}] SHORT Entry")
                        
                        if self.cfg.paper_trading:
                            logging.info(f"PAPER SHORT {code_s} cur={cur}")
                            self.brk.pos[code_s] = {"entry": float(cur), "qty": int(decided_qty), "ts": time.time(), "side": "SHORT", "strategy": strategy}
                        else:
                            res = self.brk.short_entry(sym, b, cur)
                            if res and res.get("Result") == 0:
                                logging.info(f"LIVE SHORT {code_s} cur={cur}")
                                if code_s in self.brk.pos:
                                    self.notifier.send(f"📉 SHORT: {code_s} @ {cur}円", "entry")
                            else:
                                logging.warning(f"⚠️ SHORT注文失敗 {code_s}: {res}")
                        
                        self.brk.last_order_ts[code_s] = time.time()
                        
                    finally:
                        with self._processing_lock:
                            self._processing_entry.discard(code_s)

        # ===== EXIT 判定 =====
        pos = self.brk.pos.get(code_s)
        if pos:
            side = pos.get("side", "BUY")
            strategy = pos.get("strategy", "MINI_SWING")
            entry_px = float(pos["entry"])
            qty = int(pos["qty"])
            base_ts = float(pos.get("fill_ts") or pos.get("ts") or time.time())
            held_sec = time.time() - base_ts

            if isinstance(mk['bid'], (int, float)) and isinstance(mk['ask'], (int, float)):
                if side == "SHORT":
                    exit_target_px = float(mk['ask'])  # 空売りは買い戻すのでAsk
                else:
                    exit_target_px = float(mk['bid'])  # 買いは売るのでBid
            else:
                exit_target_px = float(cur)
            
            # ★v10.0: 空売りと買いでパラメータを分ける
            if side == "SHORT":
                short_cfg = getattr(self.cfg, "short", object())
                p_tp = float(getattr(short_cfg, "tp_ticks", 10))
                p_sl = float(getattr(short_cfg, "sl_ticks", 5))
                p_time = float(getattr(short_cfg, "max_hold_sec", 1200))
            else:
                p_tp = self._get_sym_param(sym, "tp_ticks_cap", SWING["tp_ticks_cap"])
                p_sl = self._get_sym_param(sym, "sl_ticks_cap", SWING["sl_ticks_cap"])
                p_time = self._get_sym_param(sym, "max_hold_sec", SWING["max_hold_sec"])
            
            # 高額株の調整
            current_price_val = float(cur)
            if current_price_val >= 10000:
                p_tp = max(5, int(p_tp * 0.5)) 
                p_sl = max(3, int(p_sl * 0.5)) 
            
            # 損益計算（空売りは逆）
            if side == "SHORT":
                pnl_ticks = (entry_px - exit_target_px) / max(mk['tick'], 0.5)
                hit_tp = (exit_target_px <= entry_px - p_tp * mk['tick'])  # 価格下落で利確
                hit_sl = (exit_target_px >= entry_px + p_sl * mk['tick'])  # 価格上昇で損切り
            else:
                pnl_ticks = (exit_target_px - entry_px) / max(mk['tick'], 0.5)
                hit_tp = (exit_target_px >= entry_px + p_tp * mk['tick'])
                hit_sl = (exit_target_px <= entry_px - p_sl * mk['tick'])

            hit_time = held_sec >= p_time
            hit_force = self._force_close_due(side)  # ★空売りは早めに強制決済

            do_exit, reason = False, "COND"
            
            # ★★★ v13.0: TP/SLを最優先（AIより先に判定）★★★
            # これにより、利確ラインに達したら必ず利確される
            if hit_tp:
                do_exit, reason = True, "TP"
                logging.info(f"🎯 [{code_s}] TP hit! pnl_ticks={pnl_ticks:.1f}")
            elif hit_sl:
                do_exit, reason = True, "SL"
                logging.info(f"🛑 [{code_s}] SL hit! pnl_ticks={pnl_ticks:.1f}")
            elif hit_force:
                do_exit, reason = True, "FORCE"
            elif hit_time:
                do_exit, reason = True, "TIME"
            elif ai_exit:
                # AIのEXIT判断は最後（TP/SLに達してない時のみ）
                do_exit, reason = True, "AI_EXIT"

            if do_exit:
                if self.cfg.paper_trading:
                    pnl = pnl_ticks * mk['tick'] * qty
                    self._daily_pnl += pnl
                    logging.info(f"PAPER EXIT {side} {code_s} pnl={pnl:.0f} reason={reason}")
                    del self.brk.pos[code_s]
                else:
                    logging.info(f"LIVE EXIT {side} {code_s} reason={reason}")
                    
                    # 損益計算
                    entry_price = float(pos["entry"])
                    current_price = float(cur)
                    if side == "SHORT":
                        approx_pnl = (entry_price - current_price) * qty
                    else:
                        approx_pnl = (current_price - entry_price) * qty
                    
                    self._daily_pnl += approx_pnl
                    logging.info(f"LIVE EXIT_EXEC {code_s} pnl={approx_pnl:.0f} (approx) reason={reason}")

                    # ★v10.0: 空売りと買いで決済関数を分ける
                    if side == "SHORT":
                        is_ok, res = self.brk.cover(sym, qty=qty)
                    else:
                        is_ok, res = self.brk.sell(sym, qty=qty)
                    
                    if is_ok:
                        del self.brk.pos[code_s]
                        self._cooldown_until[code_s] = time.time() + self._cooldown_after_exit_sec
                        
                        icon = "💸" if approx_pnl < 0 else "💰"
                        self.notifier.send(f"{icon} EXIT {side}: {code_s} 損益:¥{approx_pnl:+.0f} ({reason})", "exit")
                    else:
                        logging.error(f"❌ [{code_s}] 決済注文失敗! {res}")
                        self.notifier.send(f"❌ 決済失敗: {code_s} (手動で決済してください！)", "error")

    def start(self):
        """メインループ (REST API版)"""
        ensure_dir("runtime")
        self.api.auth()
        self.api.register_symbols(self.syms)
        logging.info(
            "起動 mode=%s / symbols=%s",
            "PAPER" if self.cfg.paper_trading else "LIVE",
            [s.code for s in self.syms],
        )

        if not self.cfg.paper_trading:
            self._recover_ghost_positions()

        while True:
            try:
                if os.path.exists(self.cfg.safety.kill_switch_file):
                    logging.warning("Kill switch detected — stopping all trades.")
                    break

                now_str = _hhmm()
                if "11:30" <= now_str < "12:25":
                    if int(time.time()) % 60 == 0:
                        logging.info(f"🍱 昼休み休憩中... (現在 {now_str}, 12:25再開)")
                    time.sleep(10)
                    continue

                slice_n = max(1, min(len(self.syms), max(1, self._qps_limit // 2)))
                start_idx = self._rr_i
                target_syms = []
                for k in range(slice_n):
                    target_syms.append(self.syms[(start_idx + k) % len(self.syms)])
                self._rr_i = (self._rr_i + slice_n) % len(self.syms)

                for sym in target_syms:
                    self.process_symbol(str(sym.code))
                
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
                self.notifier.send(f"💀 システムエラー発生: {e}", "error")
                time.sleep(0.2)