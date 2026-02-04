# -*- coding: utf-8 -*-
"""
Controller v22.7 (AI Logging + Volatility Fix)
==============================================
【v22.7 修正点】
1. volatility計算を追加（学習環境と一致）
2. AI判断のログ出力を追加
3. Gatekeeper whitelist対応
4. qty=0チェック追加（注文エラー防止）
"""

import os, time, logging, json, glob
import datetime
import random 
import pickle
from collections import deque
from typing import Deque, Dict, Any, List, Set
import threading

import numpy as np
import pandas as pd
import pandas_ta as ta
from stable_baselines3 import PPO

from utils_common import now, ensure_dir, jst_iso, tick_size
from telemetry import Telemetry
from broker import Broker
from kabus_api import KabusAPI
from model_registry import ModelRegistry
from data_collector import DataCollector
from strategy_router import StrategyRouter, Context
from notifier import Notifier

# ===== デフォルトパラメータ =====
DEFAULT_SWING = dict(
    tp_pct=3.0,
    sl_pct=1.2,
    max_hold_sec=1800,
    max_spread_ticks=10,
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
    spread_ticks=10,
    vol_rank=0.10,
    price_step=5,
)

LOOP_SLEEP_SEC    = 0.2
GRACE_SEC         = 30.0
SHADOW_LIMIT_JPY = 2000000

DEFAULT_TIME_LIMITS = {
    "default": 30
}

def setup_logger():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    ensure_dir("logs")
    file_handler = logging.FileHandler(
        f"logs/strategy_{datetime.date.today()}.log", 
        encoding='utf-8',
        mode='a' 
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    logging.warning("📝 Logger: Console=WARNING+, File=DEBUG+")


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
        
        # ★v22.7: qty=0の銘柄を除外（monitor_onlyや重複登録対策）
        valid_symbols = []
        for s in cfg.symbols[:maxn]:
            qty = int(getattr(s, "qty", 0) or 0)
            monitor_only = getattr(s, "monitor_only", False)
            if qty > 0 and not monitor_only:
                valid_symbols.append(s)
            else:
                logging.info(f"⏭️ [{s.code}] スキップ (qty={qty}, monitor_only={monitor_only})")
        
        self.syms = valid_symbols
        
        common_cfg = getattr(cfg, "common", {})
        
        self.global_tp_pct = float(getattr(common_cfg, "tp_pct", DEFAULT_SWING["tp_pct"]))
        self.global_sl_pct = float(getattr(common_cfg, "sl_pct", DEFAULT_SWING["sl_pct"]))
        self.global_hold = int(getattr(common_cfg, "max_hold_sec", DEFAULT_SWING["max_hold_sec"]))
        
        logging.warning(f"⚙️ Config: MaxHold={self.global_hold}s, TP={self.global_tp_pct}%, SL={self.global_sl_pct}%")
        
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
        self.notifier = Notifier(cfg)
        self.notifier.send("🤖 システム起動 (v22.7 AI Log)", "startup")

        self._trade_log_path = f"logs/trades_{datetime.date.today()}.csv"
        self._init_trade_log()

        entry_cfg = getattr(cfg, "entry", None)
        if entry_cfg:
            threshold = getattr(entry_cfg, "ai_confidence_threshold", None)
            if threshold is None:
                threshold = getattr(entry_cfg, "theta", 0.65)
            self.ai_confidence_threshold = float(threshold)
        else:
            self.ai_confidence_threshold = 0.65
        
        logging.warning(f"⚙️ AI確信度閾値: {self.ai_confidence_threshold:.0%}")
        
        self.ai_models = {}
        self.universal_model = None
        self.obs_rms = None
        self.clip_obs = 10.0
        
        logging.warning("🤖 AIモデルロード開始 (v22.7 18次元)...")
        
        universal_path = "runtime/Master_AI_v2"
        env_path = "runtime/Master_AI_v2_env.pkl"
        
        if os.path.exists(universal_path + ".zip"):
            try:
                self.universal_model = PPO.load(universal_path)
                logging.warning("   🌐 汎用モデル(v22) ロード成功")
                
                if os.path.exists(env_path):
                    try:
                        with open(env_path, 'rb') as f:
                            vec_normalize = pickle.load(f)
                        
                        if hasattr(vec_normalize, 'obs_rms') and vec_normalize.obs_rms is not None:
                            self.obs_rms = vec_normalize.obs_rms
                            self.clip_obs = getattr(vec_normalize, 'clip_obs', 10.0)
                            logging.warning(f"   📊 正規化統計ロード成功 (mean shape: {self.obs_rms.mean.shape})")
                        else:
                            logging.warning("   ⚠️ obs_rmsが見つかりません")
                    except Exception as e:
                        logging.error(f"   ❌ 正規化統計ロード失敗: {e}")
                else:
                    logging.warning(f"   ⚠️ 正規化統計ファイルがありません: {env_path}")
                    
            except Exception as e:
                logging.error(f"   ❌ 汎用モデルロード失敗: {e}")
                self.universal_model = None
        else:
            logging.error(f"   ❌ モデルが見つかりません: {universal_path}.zip")
        
        indiv_count = 0
        univ_count = 0

        for s in self.syms:
            code = str(s.code)
            indiv_path = f"runtime/model_individual_{code}"
            
            if os.path.exists(indiv_path + ".zip"):
                try:
                    self.ai_models[code] = PPO.load(indiv_path)
                    logging.info(f"   👤 [{code}] 個別モデル(Specialist)をロードしました")
                    indiv_count += 1
                    continue
                except Exception as e:
                    logging.error(f"   ⚠️ [{code}] 個別モデル破損 -> 汎用モデルへフォールバック: {e}")

            if self.universal_model:
                self.ai_models[code] = self.universal_model
                univ_count += 1
            else:
                logging.warning(f"   ❌ [{code}] AIモデルなし (ルールベース稼働)")

        logging.warning(f"🤖 モデル適用完了: 個別特化={indiv_count} / 汎用={univ_count}")

        self._next_board_at = {str(s.code): 0.0 for s in self.syms}
        self._backoff = {str(s.code): 0.0 for s in self.syms}
        self._board_min_iv = max(
            0.2,
            float(getattr(getattr(self.cfg, "safety", object()), "board_min_interval_ms", 900)) / 1000.0,
        )
        self._qps_limit = int(getattr(getattr(self.cfg, "safety", object()), "api_qps_limit", 6))
        self._rr_i = 0

        # ★v22.7: Gatekeeper whitelist/allowed_symbols 両対応
        gk = getattr(self.cfg, "gatekeeper", None)
        if gk:
            allowed = getattr(gk, "allowed_symbols", None) or getattr(gk, "whitelist", None)
            if allowed:
                self.allowed_symbols = {str(x) for x in allowed}
            else:
                self.allowed_symbols = {str(s.code) for s in self.syms}
        else:
            self.allowed_symbols = {str(s.code) for s in self.syms}
        self.gk_enabled = bool(getattr(gk, "enabled", True)) if gk else False

        self.hist: Dict[str, Dict[str, Any]] = {}
        for s in self.syms:
            self.hist[str(s.code)] = {
                "spr_ticks": deque(maxlen=300),
                "vol_1s": deque(maxlen=300),
                "obi": deque(maxlen=300),
                "px_1s": deque(maxlen=10),
                "last_cumvol": None,
                "last_ts": 0.0,
                "pv": 0.0,
                "vv": 0.0,
                "vol_min": 1e18,
                "vol_max": 0.0,
                "last_bid_qty": 0.0,
                "last_ask_qty": 0.0,
                "m1_cur": None,
                "m1_bars": deque(maxlen=600),
                "obi_history": deque(maxlen=10),
                "over_sell_history": deque(maxlen=10),
                "under_buy_history": deque(maxlen=10),
            }
        
        self._preload_history()

        self._cooldown_until: Dict[str, float] = {}
        self._cooldown_after_exit_sec = float(
            getattr(getattr(self.cfg, "safety", object()), "cooldown_after_exit_sec", 600.0)
        )
        logging.warning(f"🧊 クールダウン設定: {self._cooldown_after_exit_sec:.0f}秒")

        self._streak: Dict[str, int] = {s.code: 0 for s in self.syms}
        self._daily_pnl = 0.0
        self._daily_stop = float(getattr(getattr(self.cfg, "risk", object()), "max_daily_loss_jpy", 30000))
        self._high_water_mark: Dict[str, float] = {}
        
        self._daily_stats = {
            "trades_win": 0,
            "trades_loss": 0,
            "ai_stay": 0,
            "ai_buy": 0,
            "ai_short": 0,
            "ai_exit": 0,
            "confidence_sum": 0.0,
            "confidence_count": 0,
            "pnl_total": 0.0,
        }
        self._daily_report_done = False

        self.router = StrategyRouter(
            {
                "router": {
                    "prefer_swing_if": ROUTER_PREF,
                    "forbid_entry_if": ROUTER_FORBID,
                }
            },
            self.brk,
            self.universal_model
        )
        
        self.boards = {} 

        self._last_ai_check: Dict[str, float] = {}
        self._ai_check_interval = 1.0
        
        self._last_entry_check: Dict[str, float] = {}
        self._entry_check_interval = 1.0
        self._last_ai_signal: Dict[str, tuple] = {}
        
        self._processing_entry: Set[str] = set()
        self._processing_lock = threading.Lock()
        
        self._global_order_lock = threading.Lock()
        self._last_order_time = 0.0
        self._min_order_gap = 2.0
        
        self._entry_failed_until: Dict[str, float] = {}

        short_cfg = getattr(self.cfg, "short", object())
        self._short_enabled = bool(getattr(short_cfg, "enabled", False))
        self._short_allowed = set(getattr(short_cfg, "allowed_symbols", []))
        
        # ★v22.7: AI判断ログ用の最終判断キャッシュ
        self._last_ai_log: Dict[str, float] = {}
        self._ai_log_interval = 5.0  # 5秒に1回ログ出力
        
        logging.warning(f"🔧 Controller v22.7 initialized ({len(self.syms)} tradable symbols)")

        found_limits = self._find_key_recursive(self.cfg, "time_limits")
        
        if found_limits:
            try:
                self.time_limits = dict(found_limits)
                logging.info(f"⚙️ Configから時間制限を発見・適用しました: {len(self.time_limits)}銘柄")
            except:
                self.time_limits = DEFAULT_TIME_LIMITS
                logging.warning("⚠️ Configの時間制限形式が不正です。バックアップ値を使用します。")
        else:
            self.time_limits = DEFAULT_TIME_LIMITS
            logging.warning("⚠️ Config内に 'time_limits' が見つかりませんでした。バックアップ値を使用します。")

    def _find_key_recursive(self, obj, target_key):
        if isinstance(obj, dict):
            if target_key in obj:
                return obj[target_key]
            for v in obj.values():
                res = self._find_key_recursive(v, target_key)
                if res is not None: return res
                
        elif hasattr(obj, "__dict__") or hasattr(obj, "__slots__"):
            if hasattr(obj, target_key):
                return getattr(obj, target_key)
            
            try:
                items = vars(obj) if hasattr(obj, "__dict__") else {}
                for v in items.values():
                    res = self._find_key_recursive(v, target_key)
                    if res is not None: return res
            except:
                pass
                
        return None

    def _preload_history(self):
        logging.debug("📚 過去データ読み込み中...")
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
                    
                ohlc = df_numeric[price_col].resample('1min').ohlc()
                vol = df_numeric.get('volume', df_numeric.get('size', pd.Series(0, index=df.index))).resample('1min').sum()
                
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
                    logging.debug(f"  💾 [{code}] ウォームアップ: {count}本")
                    
            except Exception as e:
                logging.debug(f"  ⚠️ [{code}] データロード失敗: {e}")

    def _predict_with_confidence(self, model, obs):
        import torch
        
        try:
            if self.obs_rms is not None:
                obs = (obs - self.obs_rms.mean) / np.sqrt(self.obs_rms.var + 1e-8)
                obs = np.clip(obs, -self.clip_obs, self.clip_obs)
            
            obs_tensor = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
            device = next(model.policy.parameters()).device
            obs_tensor = obs_tensor.to(device)
            
            with torch.no_grad():
                features = model.policy.extract_features(obs_tensor)
                
                if hasattr(model.policy, 'mlp_extractor'):
                    latent_pi, _ = model.policy.mlp_extractor(features)
                else:
                    latent_pi = features
                
                action_logits = model.policy.action_net(latent_pi)
                probs = torch.softmax(action_logits, dim=-1)
                action = torch.argmax(probs, dim=-1).item()
                confidence = probs[0, action].item()
                
            return int(action), float(confidence)
            
        except Exception as e:
            logging.debug(f"Confidence calc failed: {e}")
            action, _ = model.predict(obs, deterministic=True)
            return int(action), 0.5

    def _can_new_entry_now(self) -> bool:
        hm = _hhmm()
        if "11:25" <= hm < "12:30":
            return False
        naa = getattr(getattr(self.cfg, "risk", object()), "no_new_entry_after", None)
        return not (naa and _time_ge(naa))

    def _can_short_entry_now(self) -> bool:
        hm = _hhmm()
        if "11:25" <= hm < "12:30":
            return False
        short_cfg = getattr(self.cfg, "short", object())
        naa = getattr(short_cfg, "no_entry_after", "14:30")
        return not (naa and _time_ge(naa))

    def _force_close_due(self, side: str = "BUY") -> bool:
        hm = _hhmm()
        if "11:28" <= hm < "11:30":
            return True
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
        logging.warning("👻 証券口座の建玉確認中...")
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
                    api_side = str(p.get("Side"))
                    side = "SHORT" if api_side == "1" else "BUY"
                    
                    logging.warning(f"🧟 ゴースト建玉回収: {code} {side} {qty}株 @ {entry_price}")
                    self.notifier.send(f"🧟 ゴースト建玉回収: {code} {side} {qty}株", "error")

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
                logging.warning("✨ 未管理の建玉なし")
                
        except Exception as e:
            logging.error(f"ゴースト回収エラー: {e}")

    def _can_attempt_entry(self, code_s: str) -> tuple:
        now_t = time.time()
        
        if code_s in self.brk.pos:
            return False, "has_position"
        
        with self._processing_lock:
            if code_s in self._processing_entry:
                return False, "already_processing"
        
        if now_t - self._last_order_time < self._min_order_gap:
            return False, "order_gap"
        
        failed_until = self._entry_failed_until.get(code_s, 0)
        if now_t < failed_until:
            return False, "entry_failed_cooldown"
        
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

    def _check_panic_exit(self, code_s: str, sym, board: dict) -> bool:
        pos = self.brk.pos.get(code_s)
        if not pos:
            return False
        
        side = pos.get("side", "BUY")
        if side != "SHORT":
            return False
        
        short_cfg = getattr(self.cfg, "short", object())
        
        if bool(getattr(short_cfg, "panic_on_special_quote", True)):
            sign = str(board.get("BidAskSign", ""))
            if sign == "0102":
                logging.warning(f"🚨 [{code_s}] 特別気配検出 - パニックエグジット!")
                self.notifier.send(f"🚨 緊急決済: {code_s} (特別気配)", "error")
                
                qty = pos.get("qty", 100)
                self.brk.cover(sym, qty)
                
                if code_s in self.brk.pos:
                    del self.brk.pos[code_s]
                return True
        
        panic_distance = float(getattr(short_cfg, "panic_distance_pct", 1.0))
        upper_limit = board.get("UpperLimit")
        current = board.get("CurrentPrice")
        
        if upper_limit and current and upper_limit > 0 and current > 0:
            distance_pct = (upper_limit - current) / current * 100
            if distance_pct < panic_distance:
                logging.warning(f"🚨 [{code_s}] ストップ高接近({distance_pct:.1f}%) - パニックエグジット!")
                self.notifier.send(f"🚨 緊急決済: {code_s} (ストップ高接近)", "error")
                
                qty = pos.get("qty", 100)
                self.brk.cover(sym, qty)
                
                if code_s in self.brk.pos:
                    del self.brk.pos[code_s]
                return True
        
        return False

    def process_symbol(self, code_s: str, board: dict = None):
        sym = self.sym_map.get(code_s)
        if not sym: 
            return

        # ★v22.7: qty=0チェック
        sym_qty = int(getattr(sym, "qty", 0) or 0)
        if sym_qty <= 0:
            return

        has_position = code_s in self.brk.pos
        
        if not has_position:
            can_entry, reason = self._can_attempt_entry(code_s)
            if not can_entry:
                return

        if board is not None:
            if code_s not in self.boards:
                self.boards[code_s] = {}
            self.boards[code_s].update(board)
            current_board = self.boards[code_s]

            logging_cfg = getattr(self.cfg, "logging", None)
            should_save = False
            if logging_cfg:
                if isinstance(logging_cfg, dict):
                    should_save = logging_cfg.get("save_data", False)
                else:
                    should_save = getattr(logging_cfg, "save_data", False)

            if should_save:
                if "timestamp" not in current_board:
                    current_board["timestamp"] = datetime.datetime.now().isoformat()
                
                if hasattr(self, 'collector'):
                    self.collector.log(code_s, current_board)

        else:
            current_board = self.boards.get(code_s)
            if not current_board:
                return

        if not current_board:
            return

        if has_position and self._check_panic_exit(code_s, sym, current_board):
            return

        market_data = self._update_indicators(code_s, current_board)
        if not market_data:
            return

        if has_position:
            exit_done = self._check_tpsl_fast(code_s, sym, current_board, market_data)
            if exit_done:
                return

        self._execute_trade(sym, current_board, market_data)

    def _update_indicators(self, code_s, b):
        nowt = time.time()
        sym = self.sym_map.get(code_s)
        if not sym:
            return None

        if code_s not in self.boards:
            self.boards[code_s] = {}
        self.boards[code_s].update(b)
        full_b = self.boards[code_s]

        cur = (full_b.get("CurrentPrice") or full_b.get("BidPrice") or full_b.get("AskPrice"))
        if cur is None:
            return None

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
            if tick > 0:
                spread_ticks = spr / max(tick, 0.5)

        h["last_bid_qty"], h["last_ask_qty"] = bid_qty, ask_qty

        if nowt - h["last_ts"] >= 1.0:
            if spread_ticks is not None:
                h["spr_ticks"].append(float(spread_ticks))
        
        obi_val = 0.5
        if (bid_qty + ask_qty) > 0:
            obi_val = bid_qty / (bid_qty + ask_qty)
            h["obi"].append(obi_val)
            h["obi_history"].append(obi_val)
        
        h["over_sell_history"].append(over_sell_qty)
        h["under_buy_history"].append(under_buy_qty)

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

        dt_now = datetime.datetime.fromtimestamp(nowt)
        minute_key = dt_now.replace(second=0, microsecond=0)
        price_f = float(cur)
        bar1 = h.get("m1_cur")
        
        if bar1 is None or bar1["start"] != minute_key:
            if bar1 is not None:
                h["m1_bars"].append(bar1)
            h["m1_cur"] = {
                "start": minute_key, 
                "open": price_f, 
                "high": price_f, 
                "low": price_f, 
                "close": price_f, 
                "vol": float(dv)
            }
        else:
            bar1["close"] = price_f
            bar1["high"] = max(bar1["high"], price_f)
            bar1["low"] = min(bar1["low"], price_f)
            bar1["vol"] += float(dv)

        spike_pct = float(getattr(getattr(self.cfg, "safety", object()), "spike_pct", 3.5))
        spike_win = int(float(getattr(getattr(self.cfg, "safety", object()), "spike_window_sec", 3)))
        pxq = h["px_1s"]
        if spike_win >= 1 and len(pxq) >= spike_win:
            old_px = pxq[-spike_win]
            if old_px > 0 and (abs(cur - old_px) / old_px * 100.0) >= spike_pct:
                self._cooldown_until[code_s] = time.time() + float(
                    getattr(getattr(self.cfg, "safety", object()), "pause_after_spike_sec", 60)
                )

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

        self.buf[sym.code].append(float(cur))
        
        ndbi = (under_buy_qty - over_sell_qty) / (under_buy_qty + over_sell_qty + 1e-9)
        hsr = under_buy_qty / (bid_qty + 1e-9)
        
        obi_change = 0.0
        obi_hist = list(h["obi_history"])
        if len(obi_hist) >= 6:
            obi_change = obi_val - obi_hist[-6]
        
        sell_pressure = over_sell_qty / (ask_qty + 1) if ask_qty > 0 else 0
        buy_pressure = under_buy_qty / (bid_qty + 1) if bid_qty > 0 else 0

        # ★v22.7: volatility計算（学習環境と一致）
        volatility = 0.0
        m1_bars_list = list(h["m1_bars"])
        if len(m1_bars_list) >= 1:
            last_bar = m1_bars_list[-1]
            bar_open = float(last_bar.get("open", cur))
            bar_high = float(last_bar.get("high", cur))
            bar_low = float(last_bar.get("low", cur))
            if bar_open > 0:
                volatility = (bar_high - bar_low) / bar_open * 100.0

        return {
            "cur": cur, 
            "bid": bid, 
            "ask": ask, 
            "tick": tick, 
            "spread_ticks": spread_ticks,
            "obi_val": obi_val,
            "vwap": vwap, 
            "vwap_dist_pct": vwap_dist_pct, 
            "vol_boost": vol_boost,
            "vol_rank": vol_rank, 
            "market_heat": market_heat, 
            "regime": regime,
            "dt_now": dt_now, 
            "m1_bars": h["m1_bars"],
            "ndbi": ndbi, 
            "hsr": hsr,
            "over_sell_qty": over_sell_qty, 
            "under_buy_qty": under_buy_qty,
            "obi_change": obi_change,
            "sell_pressure": sell_pressure,
            "buy_pressure": buy_pressure,
            "volatility": volatility,  # ★v22.7追加
        }

    def _check_tpsl_fast(self, code_s: str, sym, b: dict, mk: dict) -> bool:
        pos = self.brk.pos.get(code_s)
        if not pos:
            return False

        side = pos.get("side", "BUY")
        entry_px = float(pos["entry"])
        base_ts = float(pos.get("fill_ts") or pos.get("ts") or time.time())
        held_sec = time.time() - base_ts
        
        if held_sec < 5.0:
            return False
        
        cur = mk['cur']
        
        if isinstance(mk['bid'], (int, float)) and isinstance(mk['ask'], (int, float)):
            if side == "SHORT":
                exit_target_px = float(mk['ask'])
            else:
                exit_target_px = float(mk['bid'])
        else:
            exit_target_px = float(cur)

        if side == "SHORT":
            short_cfg = getattr(self.cfg, "short", object())
            p_tp_pct = float(getattr(short_cfg, "tp_pct", self.global_tp_pct))
            p_sl_pct = float(getattr(short_cfg, "sl_pct", self.global_sl_pct))
            p_time = float(getattr(short_cfg, "max_hold_sec", 1200))
        else:
            p_tp_pct = self._get_sym_param(sym, "tp_pct_cap", self.global_tp_pct)
            p_sl_pct = self._get_sym_param(sym, "sl_pct_cap", self.global_sl_pct)
            p_time = self._get_sym_param(sym, "max_hold_sec", self.global_hold)

        if side == "SHORT":
            pnl_pct = (entry_px - exit_target_px) / entry_px * 100.0
        else:
            pnl_pct = (exit_target_px - entry_px) / entry_px * 100.0

        current_max_profit = self._high_water_mark.get(code_s, pnl_pct)
        
        if pnl_pct > current_max_profit:
            current_max_profit = pnl_pct
            self._high_water_mark[code_s] = current_max_profit

        do_exit, reason = False, ""
        
        hit_tp = (pnl_pct >= p_tp_pct)
        hit_sl = (pnl_pct <= -p_sl_pct)
        
        hit_trail = False
        trail_mode = ""

        if current_max_profit >= 1.0:
            trigger = current_max_profit - 0.3
            if pnl_pct < trigger:
                hit_trail = True
                trail_mode = "Solid"
            
        elif current_max_profit >= 0.3:
            trigger = current_max_profit * 0.5
            if pnl_pct < trigger:
                hit_trail = True
                trail_mode = "Half"

        hit_breakeven = (current_max_profit >= 0.25) and (pnl_pct < 0.02)

        hit_time = held_sec >= p_time
        hit_force = self._force_close_due(side)

        if hit_sl:
            do_exit, reason = True, f"SL({pnl_pct:+.2f}%)"
        elif hit_tp:
            do_exit, reason = True, f"TP({pnl_pct:+.2f}%)"
        elif hit_trail:
            do_exit, reason = True, f"TRAIL_{trail_mode}(Max:{current_max_profit:.2f}%->End:{pnl_pct:.2f}%)"
        elif hit_breakeven:
            do_exit, reason = True, "BREAKEVEN"
        elif hit_force:
            do_exit, reason = True, "FORCE"
        elif hit_time:
            do_exit, reason = True, "TIME"

        if do_exit:
            self._execute_exit(code_s, sym, pos, cur, reason)
            return True

        return False

    def _execute_exit(self, code_s: str, sym, pos: dict, cur: float, reason: str):
        side = pos.get("side", "BUY")
        entry_price = float(pos["entry"])
        qty = int(pos["qty"])
        
        if side == "SHORT":
            approx_pnl = (entry_price - cur) * qty
        else:
            approx_pnl = (cur - entry_price) * qty
        
        self._log_trade(code_s, "EXIT", side, cur, pnl=approx_pnl, reason=reason)

        if self.cfg.paper_trading:
            self._daily_pnl += approx_pnl
            logging.warning(f"📤 PAPER EXIT {side} {code_s} @ {cur:.1f} ¥{approx_pnl:+.0f} ({reason})")
            del self.brk.pos[code_s]

            self._daily_stats["pnl_total"] += approx_pnl
            if approx_pnl > 0:
                self._daily_stats["trades_win"] += 1
            else:
                self._daily_stats["trades_loss"] += 1

            if code_s in self._high_water_mark:
                del self._high_water_mark[code_s]

            icon = "💸" if approx_pnl < 0 else "💰"
            self.notifier.send(f"{icon} EXIT {side}: {code_s} @ {cur:.1f} ¥{approx_pnl:+.0f} ({reason})", "exit")
            
            self._cooldown_until[code_s] = time.time() + self._cooldown_after_exit_sec
            
        else:
            logging.warning(f"📤 LIVE EXIT {side} {code_s} @ {cur:.1f} ¥{approx_pnl:+.0f} ({reason})")
            self._daily_pnl += approx_pnl

            if side == "SHORT":
                is_ok, res = self.brk.cover(sym, qty=qty)
            else:
                is_ok, res = self.brk.sell(sym, qty=qty)
            
            if is_ok:
                del self.brk.pos[code_s]
                if code_s in self._high_water_mark:
                    del self._high_water_mark[code_s]

                self._daily_stats["pnl_total"] += approx_pnl
                if approx_pnl > 0:
                    self._daily_stats["trades_win"] += 1
                else:
                    self._daily_stats["trades_loss"] += 1

                self._cooldown_until[code_s] = time.time() + self._cooldown_after_exit_sec
                
                icon = "💸" if approx_pnl < 0 else "💰"
                self.notifier.send(f"{icon} EXIT {side}: {code_s} @ {cur:.1f} ¥{approx_pnl:+.0f} ({reason})", "exit")
            else:
                logging.error(f"❌ [{code_s}] 決済注文失敗! {res}")
                self.notifier.send(f"❌ 決済失敗: {code_s} (手動決済必要!)", "error")

    def _init_trade_log(self):
        ensure_dir("logs")
        if not os.path.exists(self._trade_log_path):
            with open(self._trade_log_path, 'w', encoding='utf-8') as f:
                f.write("timestamp,code,action,side,price,pnl,reason,confidence,rsi,obi,trend\n")

    def _log_trade(self, code: str, action: str, side: str, price: float, 
                   pnl: float = 0.0, reason: str = "", extra: dict = None):
        try:
            extra = extra or {}
            with open(self._trade_log_path, 'a', encoding='utf-8') as f:
                ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                conf = extra.get('conf', '')
                rsi = extra.get('rsi', '')
                obi = extra.get('obi', '')
                trend = extra.get('trend', '')
                line = f"{ts},{code},{action},{side},{price:.1f},{pnl:.0f},{reason},{conf},{rsi},{obi},{trend}\n"
                f.write(line)
        except Exception as e:
            logging.error(f"トレードログ書き込み失敗: {e}")

    def _execute_trade(self, sym, b, mk):
        code_s = str(sym.code)
        cur = mk['cur']
        m1_bars = mk['m1_bars']
        
        # ★v22.7: qty=0チェック（二重確認）
        sym_qty = int(getattr(sym, "qty", 0) or 0)
        if sym_qty <= 0:
            return
        
        pos_open = code_s in self.brk.pos
        
        ai_model = self.ai_models.get(code_s) or self.universal_model
        ai_buy = False
        ai_sell = False 
        ai_exit = False
        confidence = 0.0
        rsi_val = 50.0
        trend_score = 0.0
        
        now_t = time.time()
        last_ai_check = self._last_ai_check.get(code_s, 0)
        should_run_ai = (now_t - last_ai_check >= self._ai_check_interval)
        
        if should_run_ai:
            self._last_ai_check[code_s] = now_t
            self._last_entry_check[code_s] = now_t
            
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

                    rsi_df = df.ta.rsi(close='close', length=14)
                    rsi_val = 50.0
                    if rsi_df is not None and not rsi_df.empty:
                        rsi_val = _safe_scalar(rsi_df.iloc[-1], default=50.0)

                    rci9_s = calc_rci_series(close_series, 9)
                    rci9 = _safe_scalar(rci9_s.iloc[-1]) if not rci9_s.empty else 0.0

                    is_pos = 0.0
                    pnl_pct = 0.0
                    current_hold_duration = 0.0 
                    
                    if pos_open:
                        pos_data = self.brk.pos[code_s]
                        entry_p = float(pos_data["entry"])
                        pos_side = pos_data.get("side", "BUY")
                        
                        if pos_side == "SHORT":
                            is_pos = -1.0
                            pnl_pct = (entry_p - cur_price) / entry_p * 100.0
                        else:
                            is_pos = 1.0
                            pnl_pct = (cur_price - entry_p) / entry_p * 100.0
                            
                        base_ts = float(pos_data.get("fill_ts") or pos_data.get("ts") or time.time())
                        held_sec = time.time() - base_ts
                        current_hold_duration = min(1.0, held_sec / 3600.0)

                    ma15_val = cur_price
                    if len(df) >= 15:
                        ma15_val = _safe_scalar(df["close"].rolling(15).mean().iloc[-1], default=cur_price)
                    
                    dist_ma15 = (cur_price - ma15_val) / ma15_val * 100.0 if ma15_val > 0 else 0.0
                    
                    ma75_val = cur_price
                    if len(df) >= 75:
                        ma75_val = _safe_scalar(df["close"].rolling(75).mean().iloc[-1], default=cur_price)
                    ma75_dist = (cur_price - ma75_val) / (ma75_val + 1e-9) * 100.0
                    
                    vwap_val = float(mk.get('vwap', cur_price))
                    if vwap_val == 0: vwap_val = cur_price
                    vwap_dist = (cur_price - vwap_val) / (vwap_val + 1e-9) * 100.0

                    trend_score = 0.0
                    if cur_price > ma75_val and cur_price > vwap_val:
                        trend_score = 1.0
                    elif cur_price < ma75_val and cur_price < vwap_val:
                        trend_score = -1.0

                    obi = mk.get('obi_val', 0.5)
                    obi_change = mk.get('obi_change', 0.0)
                    sell_pressure = mk.get('sell_pressure', 0.0) / 100.0
                    buy_pressure = mk.get('buy_pressure', 0.0) / 100.0
                    ndbi = mk.get('ndbi', 0.0)
                    
                    sell_pressure = np.clip(sell_pressure, 0, 10)
                    buy_pressure = np.clip(buy_pressure, 0, 10)

                    # ★v22.7: 18次元観測ベクトル（volatility追加済み）
                    ai_obs = np.array([
                        # 1. 価格アクション
                        np.clip(pct_change, -10, 10),
                        np.clip(vwap_dist, -5, 5),
                        np.clip(ma75_dist, -5, 5),
                        np.clip(dist_ma15, -10, 10),

                        # 2. 板情報
                        np.clip(obi, 0, 1),
                        np.clip(obi_change, -0.5, 0.5),
                        np.clip(sell_pressure, 0, 10),
                        np.clip(buy_pressure, 0, 10),
                        np.clip(ndbi, -1, 1),

                        # 3. テクニカル
                        np.clip(rsi_val / 100.0, 0, 1),
                        np.clip(rci9 / 100.0, -1, 1),
                        np.clip(mk.get('vol_boost', 1.0), 0, 5),
                        np.clip(mk.get('volatility', 0.0), 0, 10),  # ★v22.7修正
                        trend_score,

                        # 4. ポジション状態
                        is_pos,
                        np.clip(pnl_pct, -5, 5),
                        current_hold_duration,
                        self._high_water_mark.get(code_s, 0.0),
                        
                    ], dtype=np.float32)
                    
                    ai_obs = np.nan_to_num(ai_obs, nan=0.0, posinf=0.0, neginf=0.0)

                    req_dim = ai_model.observation_space.shape[0]
                    cur_dim = len(ai_obs)
                    
                    if cur_dim < req_dim:
                        ai_obs = np.concatenate([ai_obs, np.zeros(req_dim - cur_dim, dtype=np.float32)])
                    elif cur_dim > req_dim:
                        ai_obs = ai_obs[:req_dim]
                    
                    action, confidence = self._predict_with_confidence(ai_model, ai_obs)

                    act_str = {0: "STAY", 1: "BUY", 2: "SHORT", 3: "EXIT"}.get(int(action), "UNK")
                    
                    # ★v22.7: AI判断ログ出力（5秒に1回）
                    last_log_time = self._last_ai_log.get(code_s, 0)
                    if now_t - last_log_time >= self._ai_log_interval:
                        self._last_ai_log[code_s] = now_t
                        trend_str = "↑" if trend_score > 0 else ("↓" if trend_score < 0 else "→")
                        pos_str = f"POS={is_pos:+.0f}" if pos_open else "NO_POS"
                        logging.info(f"🧠 [{code_s}] AI={act_str}({confidence:.0%}) | RSI={rsi_val:.0f} OBI={obi:.2f} {trend_str} | {pos_str}")
                    
                    # 確信度が閾値未満ならSTAYに強制
                    if confidence < self.ai_confidence_threshold:
                        action = 0
                    
                    if action == 1:
                        if not pos_open:
                            ai_buy = True
                    elif action == 2:
                        if not pos_open:
                            ai_sell = True
                    elif action == 3:
                        if pos_open:
                            ai_exit = True
                    
                    if action == 0:
                        self._daily_stats["ai_stay"] += 1
                    elif action == 1:
                        self._daily_stats["ai_buy"] += 1
                    elif action == 2:
                        self._daily_stats["ai_short"] += 1
                    elif action == 3:
                        self._daily_stats["ai_exit"] += 1
                    
                    self._daily_stats["confidence_sum"] += confidence
                    self._daily_stats["confidence_count"] += 1

                except Exception as e:
                    logging.debug(f"AI error [{code_s}]: {e}")
            
            else:
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

        if pos_open and ai_exit:
            pos = self.brk.pos.get(code_s)
            if pos:
                base_ts = float(pos.get("fill_ts") or pos.get("ts") or time.time())
                if time.time() - base_ts < 5.0:
                    logging.debug(f"[{code_s}] AI EXIT suppressed (< 5s)")
                else:
                    self._execute_exit(code_s, sym, pos, cur, "AI_EXIT")
                    return

        strong_long = ai_buy
        strong_short = ai_sell and self._short_enabled 

        allowed = (code_s in self.allowed_symbols) if self.gk_enabled else True

        can_trade_window = self.brk.can_trade_now(is_entry=True)
        can_new_entry = self._can_new_entry_now()
        can_short_entry = self._can_short_entry_now() 
        under_max_positions = self._respect_max_open_positions()
        interval_ok = self._respect_min_order_interval(sym)
        
        cool_ok = time.time() >= self._cooldown_until.get(code_s, 0.0)
        
        decided_qty = int(getattr(sym, "qty", 100) or 100)
        
        # ★v22.7: qty=0なら注文しない
        if decided_qty <= 0:
            return

        current_exposure = 0.0
        for p_code, p_val in self.brk.pos.items():
            current_exposure += float(p_val["entry"]) * int(p_val["qty"])
        new_cost = float(cur) * decided_qty
        if (strong_long or strong_short) and (current_exposure + new_cost > SHADOW_LIMIT_JPY):
            strong_long = False
            strong_short = False

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

        if float(mk['tick']) >= 5.0 and strategy == "SCALP":
            strategy = "OBSERVE"
        if (mk['spread_ticks'] is not None) and (mk['spread_ticks'] > DEFAULT_SWING["max_spread_ticks"]):
            strategy = "OBSERVE"
        if strategy == "SCALP":
            strategy = "MINI_SWING" 
        
        market_hot_enough = (mk['market_heat'] >= (0.3 if mk['regime'] == "OPEN" else 0.4))

        if (not pos_open) and (strategy != "OBSERVE") and can_trade_window and under_max_positions and interval_ok and allowed and cool_ok and (self._daily_pnl > -self._daily_stop) and market_hot_enough:
            
            if strong_long and can_new_entry:
                acquired = self._global_order_lock.acquire(blocking=False)
                if not acquired:
                    return 
                
                try:
                    if code_s in self.brk.pos: return
                    if not self._respect_max_open_positions(): return
                    
                    with self._processing_lock:
                        if code_s in self._processing_entry: return
                        self._processing_entry.add(code_s)
                    
                    try:
                        if self.cfg.paper_trading:
                            logging.warning(f"📥 PAPER BUY {code_s} @ {cur}")
                            self.brk.pos[code_s] = {
                                "entry": float(cur), 
                                "qty": int(decided_qty), 
                                "ts": time.time(), 
                                "side": "BUY", 
                                "strategy": strategy
                            }
                            self.notifier.send(f"🚀 BUY: {code_s} @ {cur}円", "entry")
                            self._log_trade(code_s, "ENTRY", "BUY", cur, reason=f"AI:{confidence:.2f}", extra={"conf":f"{confidence:.2f}", "rsi":f"{rsi_val:.0f}", "obi":f"{mk.get('obi_val', 0.5):.2f}", "trend":f"{trend_score:.0f}"})
                        else:
                            res = self.brk.buy(sym, b, cur)
                            if res and res.get("Result") == 0:
                                logging.warning(f"📥 LIVE BUY {code_s} @ {cur}")
                                self.notifier.send(f"🚀 BUY: {code_s} @ {cur}円", "entry")
                                self._log_trade(code_s, "ENTRY", "BUY", cur, reason=f"AI:{confidence:.2f}", extra={"conf":f"{confidence:.2f}", "rsi":f"{rsi_val:.0f}", "obi":f"{mk.get('obi_val', 0.5):.2f}", "trend":f"{trend_score:.0f}"})
                                self._last_order_time = time.time()
                            else:
                                logging.warning(f"⚠️ BUY注文失敗 {code_s}: {res}")
                                self._entry_failed_until[code_s] = time.time() + 60.0
                        
                        self.brk.last_order_ts[code_s] = time.time()
                        
                    finally:
                        with self._processing_lock:
                            self._processing_entry.discard(code_s)
                finally:
                    self._global_order_lock.release()
            
            elif strong_short and can_short_entry:
                can_short, reason = self.brk.can_short(code_s, b)
                if can_short:
                    acquired = self._global_order_lock.acquire(blocking=False)
                    if not acquired: return 
                    
                    try:
                        if code_s in self.brk.pos: return
                        if not self._respect_max_open_positions(): return
                        
                        with self._processing_lock:
                            if code_s in self._processing_entry: return
                            self._processing_entry.add(code_s)
                        
                        try:
                            if self.cfg.paper_trading:
                                logging.warning(f"📥 PAPER SHORT {code_s} @ {cur}")
                                self.brk.pos[code_s] = {
                                    "entry": float(cur), 
                                    "qty": int(decided_qty), 
                                    "ts": time.time(), 
                                    "side": "SHORT", 
                                    "strategy": strategy
                                }
                                self.notifier.send(f"📉 SHORT: {code_s} @ {cur}円", "entry")
                                self._log_trade(code_s, "ENTRY", "SHORT", cur, reason=f"AI:{confidence:.2f}", extra={"conf":f"{confidence:.2f}", "rsi":f"{rsi_val:.0f}", "obi":f"{mk.get('obi_val', 0.5):.2f}", "trend":f"{trend_score:.0f}"})
                            else:
                                res = self.brk.short_entry(sym, b, cur)
                                if res and res.get("Result") == 0:
                                    logging.warning(f"📥 LIVE SHORT {code_s} @ {cur}")
                                    self.notifier.send(f"📉 SHORT: {code_s} @ {cur}円", "entry")
                                    self._log_trade(code_s, "ENTRY", "SHORT", cur, reason=f"AI:{confidence:.2f}", extra={"conf":f"{confidence:.2f}", "rsi":f"{rsi_val:.0f}", "obi":f"{mk.get('obi_val', 0.5):.2f}", "trend":f"{trend_score:.0f}"})
                                    self._last_order_time = time.time()
                                else:
                                    logging.warning(f"⚠️ SHORT注文失敗 {code_s}: {res}")
                                    self._entry_failed_until[code_s] = time.time() + 60.0
                            
                            self.brk.last_order_ts[code_s] = time.time()
                            
                        finally:
                            with self._processing_lock:
                                self._processing_entry.discard(code_s)
                    finally:
                        self._global_order_lock.release()

    def start(self):
        ensure_dir("runtime")
        self.api.auth()
        self.api.register_symbols(self.syms)
        logging.warning(
            "起動 mode=%s / symbols=%s",
            "PAPER" if self.cfg.paper_trading else "LIVE",
            [s.code for s in self.syms],
        )

        if not self.cfg.paper_trading:
            self._recover_ghost_positions()

        while True:
            try:
                if os.path.exists(self.cfg.safety.kill_switch_file):
                    logging.warning("Kill switch detected — stopping.")
                    break

                now_str = _hhmm()
                if "11:30" <= now_str < "12:25":
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
                        pass

                time.sleep(LOOP_SLEEP_SEC)
            except Exception as e:
                logging.exception("controller main loop error: %s", e)
                self.notifier.send(f"💀 システムエラー: {e}", "error")
                time.sleep(0.2)

    def stop(self):
        self.is_running = False
        if hasattr(self, 'ws') and self.ws:
            self.ws.close()
        if hasattr(self, '_executor'):
            self._executor.shutdown(wait=False)
        
        if hasattr(self, 'collector'):
            logging.warning("💾 終了処理: 蓄積データをCSVに保存中...")
            try:
                self.collector.save_all()
                logging.warning("✅ データ保存完了")
            except Exception as e:
                logging.error(f"❌ データ保存失敗: {e}")
        
        logging.warning("🛑 Controller stopped")

    def generate_daily_report(self):
        if self._daily_report_done:
            return
        
        self._daily_report_done = True
        stats = self._daily_stats
        
        total_trades = stats["trades_win"] + stats["trades_loss"]
        win_rate = (stats["trades_win"] / total_trades * 100) if total_trades > 0 else 0
        avg_confidence = (stats["confidence_sum"] / stats["confidence_count"] * 100) if stats["confidence_count"] > 0 else 0
        
        total_ai = stats["ai_stay"] + stats["ai_buy"] + stats["ai_short"] + stats["ai_exit"]
        stay_pct = (stats["ai_stay"] / total_ai * 100) if total_ai > 0 else 0
        buy_pct = (stats["ai_buy"] / total_ai * 100) if total_ai > 0 else 0
        short_pct = (stats["ai_short"] / total_ai * 100) if total_ai > 0 else 0
        exit_pct = (stats["ai_exit"] / total_ai * 100) if total_ai > 0 else 0
        
        report_lines = [
            "",
            "=" * 50,
            f"📊 本日の成績 ({datetime.datetime.now().strftime('%Y-%m-%d')})",
            "=" * 50,
            f"トレード: {total_trades}回 (勝ち{stats['trades_win']} / 負け{stats['trades_loss']})",
            f"勝率: {win_rate:.1f}%",
            f"損益: {stats['pnl_total']:+,.0f}円",
            "",
            "AI判断の内訳 (v22.7 18dim):",
            f"  STAY:  {stats['ai_stay']:,}回 ({stay_pct:.1f}%)",
            f"  BUY:   {stats['ai_buy']:,}回 ({buy_pct:.1f}%)",
            f"  SHORT: {stats['ai_short']:,}回 ({short_pct:.1f}%)",
            f"  EXIT:  {stats['ai_exit']:,}回 ({exit_pct:.1f}%)",
            "",
            f"平均確信度: {avg_confidence:.1f}%",
            "=" * 50,
            "",
        ]
        
        for line in report_lines:
            logging.warning(line)
        
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            csv_path = "logs/daily_reports.csv"
            
            write_header = not os.path.exists(csv_path)
            
            with open(csv_path, "a", encoding="utf-8") as f:
                if write_header:
                    f.write("date,trades,wins,losses,win_rate,pnl,ai_stay,ai_buy,ai_short,ai_exit,stay_pct,avg_confidence\n")
                
                today = datetime.datetime.now().strftime('%Y-%m-%d')
                f.write(f"{today},{total_trades},{stats['trades_win']},{stats['trades_loss']},"
                        f"{win_rate:.1f},{stats['pnl_total']:.0f},"
                        f"{stats['ai_stay']},{stats['ai_buy']},{stats['ai_short']},{stats['ai_exit']},"
                        f"{stay_pct:.1f},{avg_confidence:.1f}\n")
            
            logging.warning(f"📝 日次レポートを保存: {csv_path}")
            
        except Exception as e:
            logging.error(f"❌ レポート保存失敗: {e}")
        
        if hasattr(self, 'notifier'):
            pnl_icon = "💰" if stats['pnl_total'] >= 0 else "💸"
            msg = (f"📊 **本日の成績 (v22.7)**\n"
                   f"トレード: {total_trades}回 (勝率{win_rate:.1f}%)\n"
                   f"{pnl_icon} 損益: {stats['pnl_total']:+,.0f}円\n"
                   f"STAY:{stay_pct:.1f}% EXIT:{exit_pct:.1f}%")
            self.notifier.send(msg, "report")