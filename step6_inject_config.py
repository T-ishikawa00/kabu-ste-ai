# -*- coding: utf-8 -*-
"""
step6_inject_config.py v1.0
===========================
スクリーニング結果（target_list.csv）を config.yaml の日替わり枠に注入する

特徴:
- fixed: true の銘柄は触らない
- fixed: false の銘柄だけを入れ替える
- gatekeeper.whitelist も自動更新
- バックアップを自動作成
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
MAX_DAILY_SYMBOLS = 5                # 日替わり枠の最大数

# 日替わり枠のデフォルト設定
DEFAULT_DAILY_SYMBOL = {
    "exchange": 1,
    "qty": 100,
    "fixed": False,
    "tp_ticks_cap": 20,
    "sl_ticks_cap": 10,
    "max_hold_sec": 300
}


def load_config():
    """config.yaml を読み込む"""
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ {CONFIG_FILE} が見つかりません")
        return None
    
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def save_config(config):
    """config.yaml を保存"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def backup_config():
    """現在の config.yaml をバックアップ"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f"config_backup_{timestamp}.yaml"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    
    with open(CONFIG_FILE, 'r', encoding='utf-8') as src:
        with open(backup_path, 'w', encoding='utf-8') as dst:
            dst.write(src.read())
    
    return backup_path


def load_screening_results():
    """スクリーニング結果を読み込む"""
    if not os.path.exists(TARGET_FILE):
        print(f"❌ {TARGET_FILE} が見つかりません")
        print("   先に step2_screening.py を実行してください")
        return []
    
    df = pd.read_csv(TARGET_FILE)
    
    if df.empty:
        print("⚠️ スクリーニング結果が空です")
        return []
    
    # コードを文字列として取得
    codes = df['code'].astype(str).tolist()
    
    return codes[:MAX_DAILY_SYMBOLS]


def inject_symbols(config, new_codes):
    """
    config の日替わり枠を新しい銘柄で置き換える
    """
    if not config.get('symbols'):
        print("❌ config に symbols セクションがありません")
        return config
    
    # 固定枠と日替わり枠を分離
    fixed_symbols = []
    daily_count = 0
    
    for sym in config['symbols']:
        if sym.get('fixed', False):
            fixed_symbols.append(sym)
        else:
            daily_count += 1
    
    print(f"📊 現在の構成:")
    print(f"   固定枠: {len(fixed_symbols)} 銘柄")
    print(f"   日替わり枠: {daily_count} 銘柄")
    
    # 新しい日替わり枠を作成
    new_daily_symbols = []
    for code in new_codes:
        sym = deepcopy(DEFAULT_DAILY_SYMBOL)
        sym['code'] = str(code)
        new_daily_symbols.append(sym)
    
    # 結合
    config['symbols'] = fixed_symbols + new_daily_symbols
    
    print(f"\n🔄 更新後:")
    print(f"   固定枠: {len(fixed_symbols)} 銘柄")
    print(f"   日替わり枠: {len(new_daily_symbols)} 銘柄")
    
    return config


def update_gatekeeper(config, new_codes):
    """
    gatekeeper の whitelist を更新
    """
    if not config.get('gatekeeper'):
        return config
    
    # 固定枠の銘柄コードを取得
    fixed_codes = []
    for sym in config.get('symbols', []):
        if sym.get('fixed', False):
            fixed_codes.append(str(sym.get('code')))
    
    # 新しい whitelist = 固定枠 + 日替わり枠
    config['gatekeeper']['whitelist'] = fixed_codes + new_codes
    
    print(f"\n🔐 Gatekeeper 更新:")
    print(f"   固定: {fixed_codes}")
    print(f"   日替: {new_codes}")
    
    return config


def main():
    print("=" * 60)
    print("🔧 config.yaml 日替わり銘柄注入ツール v1.0")
    print("=" * 60)
    
    # 1. config.yaml を読み込み
    config = load_config()
    if config is None:
        return
    
    # 2. スクリーニング結果を読み込み
    print(f"\n📋 {TARGET_FILE} を読み込み中...")
    new_codes = load_screening_results()
    
    if not new_codes:
        print("⚠️ 注入する銘柄がありません")
        print("   日替わり枠は変更されません")
        return
    
    print(f"   発見: {new_codes}")
    
    # 3. バックアップ作成
    print(f"\n💾 バックアップを作成中...")
    backup_path = backup_config()
    print(f"   保存先: {backup_path}")
    
    # 4. 日替わり枠を注入
    print(f"\n🔄 日替わり枠を更新中...")
    config = inject_symbols(config, new_codes)
    
    # 5. gatekeeper を更新
    config = update_gatekeeper(config, new_codes)
    
    # 6. 保存
    print(f"\n💾 {CONFIG_FILE} を保存中...")
    save_config(config)
    
    # 7. 完了
    print("\n" + "=" * 60)
    print("✅ 完了！")
    print("=" * 60)
    
    # 最終確認
    print(f"\n📝 明日の監視銘柄:")
    for sym in config.get('symbols', []):
        code = sym.get('code')
        fixed = "📌固定" if sym.get('fixed') else "🔄日替"
        print(f"   {fixed} {code}")


if __name__ == "__main__":
    main()