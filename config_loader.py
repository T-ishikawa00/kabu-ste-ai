# -*- coding: utf-8 -*-
"""
config_loader.py v22.5 (Sniper Config Support)
===============================================
【v22.5 修正点】
1. デフォルト値を「30分短期決戦 (Sniper)」仕様に変更
   - TP: 50.0 -> 3.0 (確実な利益)
   - SL: 2.0 -> 1.2 (ノイズ許容限界)
   - MaxHold: 7200 -> 1800 (30分)
2. クールダウンのデフォルト値を 10分 (600秒) に安全化
"""
from __future__ import annotations
import os, yaml
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict

# ---------- Dataclasses ----------

@dataclass
class SymbolCfg:
    code: str
    exchange: int = 1
    qty: int = 100
    min_order_interval_sec: int = 1
    tp_pct_cap: Optional[float] = None
    sl_pct_cap: Optional[float] = None
    tp_ticks_cap: Optional[int] = None
    sl_ticks_cap: Optional[int] = None

@dataclass
class PortfolioCfg:
    max_open_positions: int = 3

@dataclass
class TradingCfg:
    long_only: bool = False
    day_trade_credit: bool = True

@dataclass
class RiskCfg:
    max_daily_loss_jpy: int = 30000
    no_new_entry_after: str = "14:50"
    force_close_time: str = "14:55"

@dataclass
class SafetyCfg:
    api_latency_ms_limit: int = 1500
    max_order_failures: int = 3
    order_fail_window_sec: int = 300
    spike_pct: float = 3.5
    spike_window_sec: int = 3
    pause_after_spike_sec: int = 60
    pause_on_latency: bool = True
    pause_on_order_failures: bool = True
    kill_switch_file: str = "runtime/kill.txt"
    board_min_interval_ms: int = 300
    api_qps_limit: int = 8
    backoff_429_ms: int = 800
    holdid_wait_ms: int = 3000
    poll_interval_ms: int = 50
    # ★重要: デフォルトで10分のクールダウンを強制
    cooldown_after_exit_sec: float = 600.0
    streak_pause_sec: float = 60.0
    min_order_interval_sec: float = 1.0
    order_confirm_timeout_sec: float = 15.0
    api_call_interval_sec: float = 0.2

@dataclass
class ShortCfg:
    enabled: bool = True
    allowed_symbols: List[str] = field(default_factory=list)
    no_entry_after: str = "14:30"
    force_close_time: str = "14:50"
    # ★スナイパー仕様に変更
    tp_pct: float = 3.0    # 欲張らない
    sl_pct: float = 1.2    # ノイズ回避
    max_hold_sec: int = 1800 # 30分一本勝負
    panic_on_special_quote: bool = True
    panic_distance_pct: float = 1.0

@dataclass
class CommonCfg:
    # ★スナイパー仕様に変更
    tp_pct: float = 3.0
    sl_pct: float = 1.2
    max_hold_sec: int = 1800

@dataclass
class ExitCfg:
    trail_back_pct: float = 0.5
    trail_start_pct: float = 1.0
    max_hold_sec: int = 1800

@dataclass
class GatekeeperCfg:
    enabled: bool = True
    whitelist: List[str] = field(default_factory=list)
    blacklist: List[str] = field(default_factory=list)

@dataclass
class NotificationCfg:
    discord_webhook_url: str = ""
    line_notify_token: str = ""
    notify_on_startup: bool = True
    notify_on_entry: bool = True
    notify_on_exit: bool = True
    notify_on_error: bool = True
    notify_hourly: bool = True

@dataclass
class EntryCfg:
    theta: float = 0.65
    ai_confidence_threshold: float = None

@dataclass
class LoggingCfg:
    save_data: bool = True
    save_interval_sec: int = 300
    log_dir: str = "logs"

@dataclass
class BuySellCfg:
    front_order_type: int = 10
    price: int = 0

@dataclass
class OrdersCfg:
    buy: BuySellCfg = field(default_factory=BuySellCfg)
    sell: BuySellCfg = field(default_factory=BuySellCfg)

@dataclass
class APISettings:
    base: str = "http://localhost:18080/kabusapi"
    api_password_env: Optional[str] = "KABUS_API_PASSWORD"
    trade_password_env: Optional[str] = "KABUS_TRADE_PASSWORD"
    api_password: Optional[str] = None
    trade_password: Optional[str] = None
    timeout_get_sec: int = 5
    timeout_post_sec: int = 7

@dataclass
class Config:
    paper_trading: bool = False
    log_level: str = "INFO"
    symbols: List[SymbolCfg] = field(default_factory=list)
    portfolio: PortfolioCfg = field(default_factory=PortfolioCfg)
    trading: TradingCfg = field(default_factory=TradingCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    safety: SafetyCfg = field(default_factory=SafetyCfg)
    
    short: ShortCfg = field(default_factory=ShortCfg)
    common: CommonCfg = field(default_factory=CommonCfg)
    
    exit: ExitCfg = field(default_factory=ExitCfg)
    
    gatekeeper: GatekeeperCfg = field(default_factory=GatekeeperCfg)
    notification: NotificationCfg = field(default_factory=NotificationCfg)
    
    logging: LoggingCfg = field(default_factory=LoggingCfg)
    entry: EntryCfg = field(default_factory=EntryCfg)
    
    orders: OrdersCfg = field(default_factory=OrdersCfg)
    api: APISettings = field(default_factory=APISettings)
    telemetry_path: str = "runtime/state.json"
    max_concurrent_symbols: int = 5
    mode: str = "live"

# ---------- Loader ----------

def _to_int(x: Any, default: int) -> int:
    try: return int(x)
    except: return default

def _to_float(x: Any, default: float) -> float:
    try: return float(x)
    except: return default

def _load_orders(raw: Dict[str, Any]) -> OrdersCfg:
    def _one(d: Dict[str, Any]) -> BuySellCfg:
        return BuySellCfg(
            front_order_type=_to_int(d.get("front_order_type", 10), 10),
            price=_to_int(d.get("price", 0), 0),
        )
    b = raw.get("orders", {})
    return OrdersCfg(
        buy=_one(b.get("buy", {})),
        sell=_one(b.get("sell", {})),
    )

def _load_symbols(lst: Any) -> List[SymbolCfg]:
    res: List[SymbolCfg] = []
    if not isinstance(lst, list): return res
    for s in lst:
        if not isinstance(s, dict) or "code" not in s: continue
        
        tp_pct_cap = _to_float(s.get("tp_pct_cap"), 0.0) if s.get("tp_pct_cap") is not None else None
        sl_pct_cap = _to_float(s.get("sl_pct_cap"), 0.0) if s.get("sl_pct_cap") is not None else None
        tp_ticks_cap = _to_int(s.get("tp_ticks_cap"), 0) if s.get("tp_ticks_cap") is not None else None
        sl_ticks_cap = _to_int(s.get("sl_ticks_cap"), 0) if s.get("sl_ticks_cap") is not None else None

        res.append(SymbolCfg(
            code=str(s["code"]),
            exchange=_to_int(s.get("exchange", 1), 1),
            qty=_to_int(s.get("qty", 100), 100),
            min_order_interval_sec=_to_int(s.get("min_order_interval_sec", 1), 1),
            tp_pct_cap=tp_pct_cap,
            sl_pct_cap=sl_pct_cap,
            tp_ticks_cap=tp_ticks_cap,
            sl_ticks_cap=sl_ticks_cap
        ))
    return res

def _load_api(d: Dict[str, Any]) -> APISettings:
    return APISettings(
        base=str(d.get("base", "http://localhost:18080/kabusapi")),
        api_password_env=d.get("api_password_env", "KABUS_API_PASSWORD"),
        trade_password_env=d.get("trade_password_env", "KABUS_TRADE_PASSWORD"),
        api_password=d.get("api_password"),
        trade_password=d.get("trade_password"),
        timeout_get_sec=_to_int(d.get("timeout_get_sec", 5), 5),
        timeout_post_sec=_to_int(d.get("timeout_post_sec", 7), 7),
    )

def _load_safety(d: Dict[str, Any]) -> SafetyCfg:
    return SafetyCfg(
        api_latency_ms_limit=_to_int(d.get("api_latency_ms_limit", 1500), 1500),
        max_order_failures=_to_int(d.get("max_order_failures", 3), 3),
        order_fail_window_sec=_to_int(d.get("order_fail_window_sec", 300), 300),
        spike_pct=_to_float(d.get("spike_pct", 3.5), 3.5),
        spike_window_sec=_to_int(d.get("spike_window_sec", 3), 3),
        pause_after_spike_sec=_to_int(d.get("pause_after_spike_sec", 60), 60),
        pause_on_latency=bool(d.get("pause_on_latency", True)),
        pause_on_order_failures=bool(d.get("pause_on_order_failures", True)),
        kill_switch_file=str(d.get("kill_switch_file", "runtime/kill.txt")),
        board_min_interval_ms=_to_int(d.get("board_min_interval_ms", 300), 300),
        api_qps_limit=_to_int(d.get("api_qps_limit", 8), 8),
        backoff_429_ms=_to_int(d.get("backoff_429_ms", 800), 800),
        holdid_wait_ms=_to_int(d.get("holdid_wait_ms", 3000), 3000),
        poll_interval_ms=_to_int(d.get("poll_interval_ms", 50), 50),
        # ★ここも安全値(600s)に変更
        cooldown_after_exit_sec=_to_float(d.get("cooldown_after_exit_sec", 600.0), 600.0),
        streak_pause_sec=_to_float(d.get("streak_pause_sec", 60.0), 60.0),
        min_order_interval_sec=_to_float(d.get("min_order_interval_sec", 1.0), 1.0),
        order_confirm_timeout_sec=_to_float(d.get("order_confirm_timeout_sec", 15.0), 15.0),
        api_call_interval_sec=_to_float(d.get("api_call_interval_sec", 0.2), 0.2),
    )

def _load_short(d: Dict[str, Any]) -> ShortCfg:
    return ShortCfg(
        enabled=bool(d.get("enabled", True)),
        allowed_symbols=d.get("allowed_symbols", []) or [],
        no_entry_after=str(d.get("no_entry_after", "14:30")),
        force_close_time=str(d.get("force_close_time", "14:50")),
        tp_pct=_to_float(d.get("tp_pct", 3.0), 3.0),  # ★スナイパー仕様
        sl_pct=_to_float(d.get("sl_pct", 1.2), 1.2),  # ★スナイパー仕様
        max_hold_sec=_to_int(d.get("max_hold_sec", 1800), 1800), # ★スナイパー仕様
        panic_on_special_quote=bool(d.get("panic_on_special_quote", True)),
        panic_distance_pct=_to_float(d.get("panic_distance_pct", 1.0), 1.0)
    )

def _load_common(d: Dict[str, Any]) -> CommonCfg:
    return CommonCfg(
        tp_pct=_to_float(d.get("tp_pct", 3.0), 3.0), # ★スナイパー仕様
        sl_pct=_to_float(d.get("sl_pct", 1.2), 1.2),   # ★スナイパー仕様
        max_hold_sec=_to_int(d.get("max_hold_sec", 1800), 1800) # ★スナイパー仕様
    )

def _load_exit(d: Dict[str, Any]) -> ExitCfg:
    return ExitCfg(
        trail_back_pct=_to_float(d.get("trail_back_pct", 0.5), 0.5), # ★タイトに
        trail_start_pct=_to_float(d.get("trail_start_pct", 1.0), 1.0),
        max_hold_sec=_to_int(d.get("max_hold_sec", 1800), 1800)
    )

def _load_logging(d: Dict[str, Any]) -> LoggingCfg:
    return LoggingCfg(
        save_data=bool(d.get("save_data", True)),
        save_interval_sec=_to_int(d.get("save_interval_sec", 300), 300),
        log_dir=str(d.get("log_dir", "logs"))
    )

def _load_entry(d: Dict[str, Any]) -> EntryCfg:
    theta = _to_float(d.get("theta", 0.65), 0.65) # ★0.70だと高すぎるので調整
    ai_conf = d.get("ai_confidence_threshold")
    if ai_conf is not None:
        ai_conf = _to_float(ai_conf, theta)
    return EntryCfg(
        theta=theta,
        ai_confidence_threshold=ai_conf
    )

def load_cfg(path: str = "config.yaml") -> Config:
    if not os.path.exists(path):
        print(f"⚠️ Config file not found: {path}. Using defaults.")
        return Config()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = Config(
        paper_trading=bool(raw.get("paper_trading", False)),
        log_level=str(raw.get("log_level", "INFO")),
        symbols=_load_symbols(raw.get("symbols", [])),
        portfolio=PortfolioCfg(**(raw.get("portfolio", {}) or {})),
        trading=TradingCfg(**(raw.get("trading", {}) or {})),
        risk=RiskCfg(**(raw.get("risk", {}) or {})),
        safety=_load_safety(raw.get("safety", {}) or {}),
        
        short=_load_short(raw.get("short", {}) or {}),
        common=_load_common(raw.get("common", {}) or {}),
        
        exit=_load_exit(raw.get("exit", {}) or {}),
        
        gatekeeper=GatekeeperCfg(**(raw.get("gatekeeper", {}) or {})),
        notification=NotificationCfg(**(raw.get("notification", {}) or {})),
        
        logging=_load_logging(raw.get("logging", {}) or {}),
        entry=_load_entry(raw.get("entry", {}) or {}),
        orders=_load_orders(raw),
        api=_load_api(raw.get("api", {})),
        telemetry_path=str(raw.get("telemetry_path", "runtime/state.json")),
        max_concurrent_symbols=_to_int(raw.get("max_concurrent_symbols", 5), 5),
        mode=str(raw.get("mode", "live"))
    )
    return cfg