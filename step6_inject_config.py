# -*- coding: utf-8 -*-
"""
step6_inject_config.py v17.13 (Fix Data Save & Portfolio)
=====================================================
【修正内容】
1. 生成する config.yaml に `logging` (save_data: true) を常時追加。
   → これでデータ消失を恒久的に防ぎます。
2. ポートフォリオを正式に「任天堂削除・SCREEN追加」版に更新。
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

# 固定13銘柄 (任天堂7974を削除し、SCREEN 7735を追加済み)
FIXED_TARGETS = [
    "7003", "6525", "7011", "6269", "6954", "5016", "8306", 
    "278A", "285A", "3110", "8031", "9984", "9107"
]

# ★【ここを自由に変えてください】銘柄ごとの保有時間設定（秒）
# 書いていない銘柄はデフォルト（1800秒＝30分）になります
TIME_SETTINGS = {
    # --- 20分 (1200秒): 暴れ馬・短期決戦 ---
    "7003": 1200,  # 三井E&S
    "6525": 1200,  # KOKUSAI
    "6269": 1200,  # 三井海洋
    "9984": 1200,  # SBG

    # --- 30分 (1800秒): 標準・バランス型 ---
    "7011": 1800,  # 三菱重工
    "6954": 1800,  # ファナック
    "5016": 1800,  # 良品計画
    "278A": 1800,  # (IPO/新興)
    "285A": 1800,  # (IPO/新興)
    "3110": 1800,  # 日東紡
    "8031": 1200,  # 三井物産

    # --- 60分 (3600秒): 重戦車・じっくり ---
    "8306": 3600,  # 三菱UFJ
    "9107": 3600,  # 川崎汽船
}

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

def generate_beautiful_config(current_data, new_daily_codes, fixed_code_list):
    api = current_data.get('api', {})
    risk = current_data.get('risk', {})
    short = current_data.get('short', {})
    
    # リスト構築
    fixed_symbols = [{'code': str(c), 'exchange': 1, 'qty': 100, 'fixed': True} for c in fixed_code_list]
    daily_symbols = []
    for c in new_daily_codes:
        if c not in fixed_code_list:
            daily_symbols.append({'code': str(c), 'exchange': 1, 'qty': 100, 'fixed': False})

    whitelist = [str(s['code']) for s in fixed_symbols] + [str(s['code']) for s in daily_symbols]

    # ★ここに `logging` セクションを追加しました
    text = f"""# ==========================================
#   Kabu-Ste-AI Config v17.13 (Fix Data Save & Portfolio)
#   ★固定枠{len(fixed_symbols)}銘柄 + 日替わり枠{len(daily_symbols)}銘柄
# ==========================================

# ==========================================
# API設定
# ==========================================
api:
  base: http://localhost:18080/kabusapi
  api_password: "Htk050313"
  trade_password: "Htk_222305"
  timeout_get_sec: 10
  timeout_post_sec: 10

telemetry_path: runtime/telemetry.json

# ==========================================
# ログ・データ保存設定 (★最重要)
# ==========================================
logging:
  save_data: true        # ★ここをtrueにしないと学習データが残りません！
  save_dir: "data_raw"   # 保存先フォルダ
  log_level: "INFO"

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
    for c in [str(s['code']) for s in fixed_symbols]: text += f"\n    - \"{c}\""
    text += f"""
  min_distance_to_limit_up_pct: 2.0
  max_dsr: 4.0
  no_entry_after: "14:30"
  force_close_time: "14:50"
  tp_pct: 1.0         # 空売り利確 -1%
  sl_pct: 0.4         # 空売り損切 +0.4%
  max_hold_sec: 1800
  panic_on_special_quote: true
  panic_distance_pct: 1.0

# ==========================================
# 共通戦略設定（利確・損切）
# ==========================================
common:
  tp_pct: 1.0       # 利確 +1%
  sl_pct: 0.4       # 損切 -0.4%
  max_hold_sec: 1800

# ==========================================
# 銘柄設定
# ==========================================
symbols:"""
    
    # 固定枠の出力
    for s in fixed_symbols:
        code = str(s['code'])
        # ★ここで時間を設定
        hold_time = TIME_SETTINGS.get(code, 1800)
        
        text += f"""
  - code: "{code}"
    exchange: 1
    qty: 100
    fixed: true
    max_hold_sec: {hold_time}"""

    # 日替わり枠の出力
    if daily_symbols:
        for s in daily_symbols:
            code = str(s['code'])
            # ★日替わり枠も時間設定があれば反映
            hold_time = TIME_SETTINGS.get(code, 1800)
            
            text += f"""
  - code: "{code}"
    exchange: 1
    qty: 100
    fixed: false
    max_hold_sec: {hold_time}"""
            
    else: text += "\n  # (本日は対象なし)"

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
  theta: 0.55
exit:
  trail_back_pct: 0.005
  trail_start_pct: 0.003
  max_hold_sec: 1800

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
  discord_webhook_url: "https://discordapp.com/api/webhooks/1458028818446028851/1k7PEt4LfAn7-YSXs9_b8NTlN7Gbo74zBxofc-YbUX-n1h_uFgYhgWOzPFycQ9aLrpix"
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
    print("🔧 config.yaml 自動生成 (v17.13 Fix Data Save & Portfolio)")
    print("   全銘柄を %指定に統一し、データ保存設定(save_data: true)を強制付与")
    print("   SCREEN(7735)を正式に固定枠へ追加しました")
    print("=" * 60)
    
    current_data = load_yaml_data()
    new_daily_codes = load_screening_results()
    backup_config()
    new_config_text = generate_beautiful_config(current_data, new_daily_codes, FIXED_TARGETS)
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.write(new_config_text)
    print("✅ config.yaml を更新しました")

if __name__ == "__main__":
    main()