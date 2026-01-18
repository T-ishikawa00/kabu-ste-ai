# -*- coding: utf-8 -*-
"""
step6_inject_config.py v16.3 (Strict Fix)
=========================================
config.yaml の自動更新ツール

修正点:
1. MAX_DAILY_SYMBOLS を 10 に変更
2. 指定された固定40銘柄を強制適用
3. それ以外のロジック（target_list.csvの使用など）は一切変更なし
"""

import os
import yaml
import pandas as pd
from datetime import datetime
from copy import deepcopy

# ==========================================
# 設定
# ==========================================
TARGET_FILE = "target_list.csv"      # スクリーニング結果
CONFIG_FILE = "config.yaml"          # メイン設定ファイル
BACKUP_DIR = "config_backup"         # バックアップ保存先

# ★ここを10に変更（日替わり枠）
MAX_DAILY_SYMBOLS = 10

# ★固定銘柄リスト (40銘柄)
FIXED_TARGETS = [
    "9501", "8411", "5803", "9984", "6954", "6269", "285A", "5801", "485A", "4570",
    "3110", "7974", "6525", "6330", "1662", "3350", "7746", "5016", "8306", "4506",
    "1893", "278A", "6702", "464A", "5101", "6501", "6758", "7267", "3382", "4446",
    "4082", "7003", "7735", "7711", "8135", "186A", "5802", "9107", "7453", "9227"
]

def load_yaml_data():
    """現在のデータを読み込む"""
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def load_screening_results():
    """スクリーニング結果を読み込み、コードを補正する"""
    if not os.path.exists(TARGET_FILE):
        return []
    df = pd.read_csv(TARGET_FILE)
    if df.empty: return []
    
    raw_codes = df['code'].astype(str).tolist()
    clean_codes = []
    
    # 5桁(xxxxx0)なら4桁(xxxxx)に直す
    for c in raw_codes:
        if len(c) == 5 and c.endswith('0'):
            clean_codes.append(c[:-1])
        else:
            clean_codes.append(c)
            
    return clean_codes[:MAX_DAILY_SYMBOLS]

def backup_config():
    """バックアップ作成"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(BACKUP_DIR, f"config_backup_{timestamp}.yaml")
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as src:
            with open(backup_path, 'w', encoding='utf-8') as dst:
                dst.write(src.read())
    return backup_path

def generate_beautiful_config(current_data, new_daily_codes, fixed_code_list):
    """
    設定ファイルを生成する
    """
    api = current_data.get('api', {})
    risk = current_data.get('risk', {})
    short = current_data.get('short', {})
    common = current_data.get('common', {})
    notif = current_data.get('notification', {})
    
    # ★固定枠の作成 (リストから強制生成)
    fixed_symbols = []
    for code in fixed_code_list:
        fixed_symbols.append({
            'code': str(code),
            'exchange': 1,
            'qty': 100,
            'fixed': True
        })
            
    # 日替わり枠の作成 (スイング設定)
    daily_symbols = []
    for code in new_daily_codes:
        daily_symbols.append({
            'code': str(code),
            'exchange': 1,
            'qty': 100,
            'fixed': False,
            'tp_ticks_cap': 60,     # 利確: 60tick
            'sl_ticks_cap': 30,     # 損切: 30tick
            'max_hold_sec': 3600    # 時間: 60分
        })

    # リスト作成
    gatekeeper_fixed = [str(s['code']) for s in fixed_symbols]
    gatekeeper_daily = [str(c) for c in new_daily_codes]

    # テキスト生成
    text = f"""# ==========================================
#   Kabu-Ste-AI Config v16.3 (Strict Fix)
#   ★固定枠{len(fixed_symbols)}銘柄 + 日替わり枠{len(daily_symbols)}銘柄
# ==========================================


# ==========================================
# API設定
# ==========================================
api:
  base: {api.get('base', 'http://localhost:18080/kabusapi')}
  api_password: {api.get('api_password', '')}
  trade_password: {api.get('trade_password', '')}
  timeout_get_sec: {api.get('timeout_get_sec', 10)}
  timeout_post_sec: {api.get('timeout_post_sec', 10)}

telemetry_path: runtime/telemetry.json


# ==========================================
# 取引モード
# ==========================================
paper_trading: {str(current_data.get('paper_trading', False)).lower()}


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
# 空売り設定 (固定枠のみ許可)
# ==========================================
short:
  enabled: {str(short.get('enabled', True)).lower()}
  allowed_symbols:"""
  
    # 固定枠のみを許可リストに追加
    if gatekeeper_fixed:
        for c in gatekeeper_fixed:
            text += f"""
    - "{c}" """
    else:
        text += " []"

    text += f"""
  min_distance_to_limit_up_pct: {short.get('min_distance_to_limit_up_pct', 2.0)}
  max_dsr: {short.get('max_dsr', 4.0)}
  no_entry_after: "{short.get('no_entry_after', '14:30')}"
  force_close_time: "{short.get('force_close_time', '14:50')}"
  sl_ticks: {short.get('sl_ticks', 20)}
  tp_ticks: {short.get('tp_ticks', 30)}
  max_hold_sec: {short.get('max_hold_sec', 900)}
  panic_on_special_quote: {str(short.get('panic_on_special_quote', True)).lower()}
  panic_distance_pct: {short.get('panic_distance_pct', 1.0)}


# ==========================================
# 共通戦略設定（利確・損切）
# ==========================================
common:
  tp_ticks: {common.get('tp_ticks', 30)}      # 利確
  sl_ticks: {common.get('sl_ticks', 20)}      # 損切
  max_hold_sec: {common.get('max_hold_sec', 900)} # 最大保有


# ==========================================
# 銘柄設定
# ==========================================
symbols:

  # ─────────────────────────────────────────
  # 【固定枠】毎日監視する主力銘柄 (スキャルピング寄り)
  # ─────────────────────────────────────────"""

    for s in fixed_symbols:
        text += f"""
  - code: "{s['code']}"
    exchange: {s.get('exchange', 1)}
    qty: {s.get('qty', 100)}
    fixed: true"""
    
    text += """

  # ─────────────────────────────────────────
  # 【日替わり枠】汎用AIによるデイスイング (ゆったり設定)
  # ─────────────────────────────────────────"""

    for s in daily_symbols:
        text += f"""
  - code: "{s['code']}"
    exchange: 1
    qty: 100
    fixed: false
    tp_ticks_cap: {s['tp_ticks_cap']}
    sl_ticks_cap: {s['sl_ticks_cap']}
    max_hold_sec: {s['max_hold_sec']} # 60分"""

    text += f"""


# ==========================================
# ポートフォリオ設定
# ==========================================
max_concurrent_symbols: 60

portfolio:
  max_open_positions: 3


# ==========================================
# 安全設定
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
  cooldown_after_exit_sec: 2.0
  streak_pause_sec: 30


# ==========================================
# エントリー・エグジット設定
# ==========================================
entry:
  theta: 0.60

exit:
  trail_back_pct: 0.005
  trail_start_pct: 0.003
  max_hold_sec: 900


# ==========================================
# 自動学習（無効）
# ==========================================
learning:
  enable_auto_train: false


# ==========================================
# Gatekeeper（取引許可リスト）
# ==========================================
gatekeeper:
  enabled: true
  whitelist:
    # 固定枠"""
    
    for c in gatekeeper_fixed:
        text += f"""
    - "{c}" """

    text += """
    # 日替わり枠"""
    
    for c in gatekeeper_daily:
        text += f"""
    - "{c}" """
    
    blacklist = current_data.get('gatekeeper', {}).get('blacklist', [])
    if blacklist:
        text += """
  blacklist:"""
        for c in blacklist:
             text += f"""
    - "{c}" """

    text += f"""


# ==========================================
# 戦略設定
# ==========================================
strategies:
  MINI_SWING:
    pass_placeholder: true

router:
  prefer_swing_if:
    heat: 0.50
    vwap_distance: 0.40
    spread_ticks: 3
    vol_boost_ratio: 1.0
    market_temp: 0.40
  forbid_entry_if:
    spread_ticks: 5
    vol_rank: 0.05
    price_step: 100


# ==========================================
# 通知設定（Discord）
# ==========================================
notification:
  discord_webhook_url: "REDACTED'discord_webhook_url', '')}"
  line_notify_token: "REDACTED'line_notify_token', '')}"
  notify_on_startup: true
  notify_on_entry: true
  notify_on_exit: true
  notify_on_error: true
  notify_hourly: true
"""
    return text

def main():
    print("=" * 60)
    print("🔧 config.yaml 最終調整ツール v16.3")
    print("   ・target_list.csv から日替わり10銘柄")
    print("   ・FIXED_TARGETS から固定40銘柄")
    print("=" * 60)
    
    current_data = load_yaml_data()
    if not current_data: return

    # target_list.csv から読み込むロジック（そのまま）
    new_daily_codes = load_screening_results()
    
    if not new_daily_codes:
        print("⚠️ target_list.csv に銘柄がありません")
    
    # バックアップ
    backup_config()
    
    # 生成 (固定リストを渡す)
    new_config_text = generate_beautiful_config(current_data, new_daily_codes, FIXED_TARGETS)

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(new_config_text)

    print("✅ config.yaml を更新しました")
    print(f"   固定: {len(FIXED_TARGETS)} + 日替わり: {len(new_daily_codes)} = 合計 {len(FIXED_TARGETS) + len(new_daily_codes)} 銘柄")
    print("=" * 60)

if __name__ == "__main__":
    main()