# -*- coding: utf-8 -*-
"""
step6_inject_config.py v22.9 (Sniper + 10min Cool)
=======================================================
【修正内容】
1. クールダウン短縮
   - 30分(1800秒) -> 10分(600秒)
   - チャンスを逃したくない、でも往復ビンタは嫌だ、という折衷案。
"""

import os
import yaml
import pandas as pd
import re
from datetime import datetime

# ==========================================
# 設定
# ==========================================
TARGET_FILE = "target_list.csv"
CONFIG_FILE = "config.yaml"
BACKUP_DIR = "config_backup"

# 固定12銘柄
FIXED_TARGETS = [
    "7003", "6525", "7011", "6269", "6954", "5016", "8306", 
    "285A", "3110", "8031", "9984", "9107"
]

# データ収集のみ
DATA_ONLY_TARGETS = [
    "9107", "6857", "6526", "8306",       
    "1570",
]

# ★勝利の方程式（スナイパー設定）
DEFAULT_HOLD_SEC = 1800  # 30分
DEFAULT_TP_PCT = 3.0     # 利確目標
DEFAULT_SL_PCT = 1.2     # 損切りライン
DEFAULT_COOLDOWN = 600   # ★変更: 10分 (これなら次の中波に乗れる)

MAX_DAILY_SYMBOLS = 7

def load_yaml_data():
    if not os.path.exists(CONFIG_FILE): return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except:
        return rescue_passwords()

def rescue_passwords():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f: content = f.read()
        data = {'api': {}, 'notification': {}}
        m_api = re.search(r'api_password:\s*["\']?([^"\n\']+)', content)
        if m_api: data['api']['api_password'] = m_api.group(1)
        m_trade = re.search(r'trade_password:\s*["\']?([^"\n\']+)', content)
        if m_trade: data['api']['trade_password'] = m_trade.group(1)
        m_discord = re.search(r'discord_webhook_url:\s*["\']?([^"\n\']+)', content)
        if m_discord: data['notification']['discord_webhook_url'] = m_discord.group(1)
        return data
    except: return {}

def load_screening_results():
    if not os.path.exists(TARGET_FILE): return []
    try:
        df = pd.read_csv(TARGET_FILE)
        if df.empty: return []
        raw = df['code'].astype(str).tolist()
        clean = [c[:-1] if len(c)==5 and c.endswith('0') else c for c in raw]
        seen = set()
        return [x for x in clean if not (x in seen or seen.add(x))][:MAX_DAILY_SYMBOLS]
    except: return []

def backup_config():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as src:
                with open(os.path.join(BACKUP_DIR, f"config_backup_{ts}.yaml"), 'w', encoding='utf-8') as dst:
                    dst.write(src.read())
        except: pass

def generate_beautiful_config(current_data, new_daily_codes, fixed_code_list, data_only_list):
    api = current_data.get('api', {})
    risk = current_data.get('risk', {})
    short = current_data.get('short', {})
    
    fixed_symbols = [{'code': str(c), 'exchange': 1, 'qty': 100, 'fixed': True} for c in fixed_code_list]
    daily_symbols = []
    for c in new_daily_codes:
        if c not in fixed_code_list and c not in data_only_list:
            daily_symbols.append({'code': str(c), 'exchange': 1, 'qty': 100, 'fixed': False})
            
    data_symbols = [{'code': str(c), 'exchange': 1, 'qty': 0, 'fixed': False} for c in data_only_list]
    whitelist = [str(s['code']) for s in fixed_symbols] + [str(s['code']) for s in daily_symbols]

    text = f"""# ==========================================
#   Kabu-Ste-AI Config v22.9 (Sniper + 10min Cool)
#   ★スナイパー設定: SL={DEFAULT_SL_PCT}%, TP={DEFAULT_TP_PCT}%
#   ★攻撃的防御: CoolDown={DEFAULT_COOLDOWN}s (10分)
# ==========================================

# ==========================================
# API設定
# ==========================================
api:
  base: http://localhost:18080/kabusapi
  api_password: ""
  trade_password: ""
  timeout_get_sec: 10
  timeout_post_sec: 10

telemetry_path: runtime/telemetry.json

# ==========================================
# ログ・データ保存設定
# ==========================================
logging:
  save_data: true
  save_dir: "data_raw"
  log_level: "INFO"

# ==========================================
# 取引モード
# ==========================================
paper_trading: false

# ==========================================
# AIモデル設定
# ==========================================
model_path: "runtime/Master_AI_v2"

# ==========================================
# 注文設定
# ==========================================
orders:
  style: "market"
  ioc: false
  cap_ticks_entry: 10
  cap_ticks_exit: 10
  retry:
    enabled: true
    max_attempts: 2
    widen_ticks_each: 1
    wait_ms_between: 100

# ==========================================
# リスク管理
# ==========================================
risk:
  no_new_entry_after: "{risk.get('no_new_entry_after', '14:50')}"
  force_close_time: "{risk.get('force_close_time', '14:55')}"
  max_daily_loss_jpy: {risk.get('max_daily_loss_jpy', 30000)}

# ==========================================
# 空売り設定
# ==========================================
short:
  enabled: {str(short.get('enabled', True)).lower()}
  allowed_symbols:"""
    for c in [str(s['code']) for s in fixed_symbols]: text += f"\n    - \"{c}\""
    text += f"""
  min_distance_to_limit_up_pct: 2.0
  max_dsr: 4.0
  no_entry_after: "14:30"
  force_close_time: "14:50"
  tp_pct: {DEFAULT_TP_PCT}
  sl_pct: {DEFAULT_SL_PCT}
  max_hold_sec: {DEFAULT_HOLD_SEC}
  panic_on_special_quote: true
  panic_distance_pct: 1.0

# ==========================================
# 共通戦略設定
# ==========================================
common:
  tp_pct: {DEFAULT_TP_PCT}
  sl_pct: {DEFAULT_SL_PCT}
  max_hold_sec: {DEFAULT_HOLD_SEC}

# ==========================================
# スイングモード設定
# ==========================================
swing_mode:
  tp_pct: {DEFAULT_TP_PCT}
  sl_pct: {DEFAULT_SL_PCT}
  max_hold_sec: {DEFAULT_HOLD_SEC}
  time_limits:
    entry_start: "09:05"
    entry_stop: "14:30"
    exit_force: "14:55"

# ==========================================
# 銘柄設定
# ==========================================
symbols:"""
    
    # 固定枠
    for s in fixed_symbols:
        code = str(s['code'])
        text += f"""
  - code: "{code}"
    exchange: 1
    qty: 100
    fixed: true
    max_hold_sec: {DEFAULT_HOLD_SEC}"""

    # 日替わり枠
    if daily_symbols:
        for s in daily_symbols:
            code = str(s['code'])
            text += f"""
  - code: "{code}"
    exchange: 1
    qty: 100
    fixed: false
    max_hold_sec: {DEFAULT_HOLD_SEC}"""
            
    # データ収集専用枠
    if data_symbols:
        text += f"\n  # --- 観測専用 (No Trade) ---"
        for s in data_symbols:
            code = str(s['code'])
            text += f"""
  - code: "{code}"
    exchange: 1
    qty: 0
    fixed: false
    monitor_only: true"""

    text += f"""

# ==========================================
# ポートフォリオ設定
# ==========================================
max_concurrent_symbols: 60
portfolio:
  max_open_positions: 3

# ==========================================
# 安全設定 (★10分)
# ==========================================
safety:
  api_latency_ms_limit: 2500
  pause_on_latency: true
  api_qps_limit: 3
  board_min_interval_ms: 500
  min_order_interval_sec: 1
  order_confirm_timeout_sec: 8
  api_call_interval_sec: 0.2
  spike_pct: 4.0
  spike_window_sec: 3
  pause_after_spike_sec: 30
  kill_switch_file: runtime/kill.txt
  holdid_wait_ms: 2000
  poll_interval_ms: 20
  
  # ★ここを変更: 1800 -> 600 (10分)
  cooldown_after_exit_sec: {DEFAULT_COOLDOWN}
  
  streak_pause_sec: 30

# ==========================================
# エントリー・エグジット設定
# ==========================================
entry:
  theta: 0.65
exit:
  trail_back_pct: 0.5
  trail_start_pct: 1.0
  max_hold_sec: {DEFAULT_HOLD_SEC}

# ==========================================
# 自動学習
# ==========================================
learning:
  enable_auto_train: false

# ==========================================
# Gatekeeper
# ==========================================
gatekeeper:
  enabled: true
  whitelist:"""
    for c in whitelist: text += f"\n    - \"{c}\""
    
    text += """
  blacklist: []

# ==========================================
# 戦略設定
# ==========================================
strategies:
  MINI_SWING:
    pass_placeholder: true

router:
  prefer_swing_if:
    heat: 0.50
    vwap_distance: 0.70
    spread_ticks: 5
    vol_boost_ratio: 2.0
    market_temp: 0.40
  
  forbid_entry_if:
    spread_ticks: 10
    vol_rank: 0.05
    price_step: 100

  logic:
    time_limits:
      default: 30  # ★30分"""
    
    text += """

# ==========================================
# 通知設定
# ==========================================
notification:
  discord_webhook_url: ""
  line_notify_token: ""
  notify_on_startup: true
  notify_on_entry: true
  notify_on_exit: true
  notify_on_error: true
  notify_hourly: true
"""
    return text

def main():
    print("=" * 60)
    print("🔧 config.yaml 自動生成 v22.9 (Sniper + 10min Cool)")
    print(f"   ★設定変更: SL={DEFAULT_SL_PCT}%, TP={DEFAULT_TP_PCT}%")
    print(f"   ★クールダウン: {DEFAULT_COOLDOWN}秒 (10分)")
    print("=" * 60)
    
    current_data = load_yaml_data()
    new_daily_codes = load_screening_results()
    backup_config()
    new_config_text = generate_beautiful_config(current_data, new_daily_codes, FIXED_TARGETS, DATA_ONLY_TARGETS)
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(new_config_text)
    print("✅ config.yaml を更新しました")

if __name__ == "__main__":
    main()