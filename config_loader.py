# -*- coding: utf-8 -*-
"""
config_loader.py — 安全＆後方互換対応の設定ローダ（Py3.13対応）
- default_factory を用いて mutable default を排除
- APIパスワードは環境変数優先（api_password_env / trade_password_env / 旧キーfallback）
- orders（buy/sell）を読み込み、Broker/KabusAPI と連携
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

@dataclass
class PortfolioCfg:
    max_open_positions: int = 1

@dataclass
class TradingCfg:
    long_only: bool = True
    day_trade_credit: bool = True

@dataclass
class RiskCfg:
    max_daily_loss_jpy: int = 10000
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
    # --- 追加: 約定→建玉反映ラグの吸収パラメータ ---
    holdid_wait_ms: int = 3000        # HoldID を待つ最大ミリ秒
    poll_interval_ms: int = 50        # positions/orders のポーリング間隔

    # --- ここから追加（Controller が参照する安全系パラメータ） ---
    cooldown_after_exit_sec: float = 2.0   # 返済直後のクールダウン
    streak_pause_sec: float = 60.0         # 連敗時の一時停止秒数

@dataclass
class BuySellCfg:
    front_order_type: int = 10  # 10=成行, 20=指値
    price: int = 0              # 成行なら0で無視

@dataclass
class OrdersCfg:
    buy: BuySellCfg = field(default_factory=BuySellCfg)
    sell: BuySellCfg = field(default_factory=BuySellCfg)

@dataclass
class APISettings:
    base: str = "http://localhost:18080/kabusapi"
    api_password_env: Optional[str] = "KABUS_API_PASSWORD"
    trade_password_env: Optional[str] = "KABUS_TRADE_PASSWORD"
    api_password: Optional[str] = None       # 後方互換
    trade_password: Optional[str] = None     # 後方互換
    timeout_get_sec: int = 5
    timeout_post_sec: int = 7

@dataclass
class Config:
    paper_trading: bool = True
    log_level: str = "INFO"
    symbols: List[SymbolCfg] = field(default_factory=list)
    portfolio: PortfolioCfg = field(default_factory=PortfolioCfg)
    trading: TradingCfg = field(default_factory=TradingCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    safety: SafetyCfg = field(default_factory=SafetyCfg)
    orders: OrdersCfg = field(default_factory=OrdersCfg)
    api: APISettings = field(default_factory=APISettings)
    telemetry_path: str = "runtime/state.json"
    max_concurrent_symbols: int = 5
    mode: str = "live"  # "live" / "paper" など

# ---------- Loader ----------

def _to_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return default

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
    if not isinstance(lst, list):
        return res
    for s in lst:
        if not isinstance(s, dict) or "code" not in s:
            continue
        res.append(SymbolCfg(
            code=str(s["code"]),
            exchange=_to_int(s.get("exchange", 1), 1),
            qty=_to_int(s.get("qty", 100), 100),
            min_order_interval_sec=_to_int(s.get("min_order_interval_sec", 1), 1),
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

def load_cfg(path: str = "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = Config(
        paper_trading=bool(raw.get("paper_trading", True)),
        log_level=str(raw.get("log_level", "INFO")),
        symbols=_load_symbols(raw.get("symbols", [])),
        portfolio=PortfolioCfg(**(raw.get("portfolio", {}) or {})),
        trading=TradingCfg(**(raw.get("trading", {}) or {})),
        risk=RiskCfg(**(raw.get("risk", {}) or {})),
        safety=SafetyCfg(**(raw.get("safety", {}) or {})),
        orders=_load_orders(raw),
        api=_load_api(raw.get("api", {})),
        telemetry_path=str(raw.get("telemetry_path", "runtime/state.json")),
        max_concurrent_symbols=_to_int(raw.get("max_concurrent_symbols", 5), 5),
        mode=str(raw.get("mode", "live")),
    )
    return cfg

