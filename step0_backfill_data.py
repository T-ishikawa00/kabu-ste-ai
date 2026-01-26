# -*- coding: utf-8 -*-
"""
step1_backfill_fast_v2.py (Library Edition)
===========================================
【修正版】
requests ではなく jquantsapi ライブラリを使用します。
これにより、認証やデータ形式の問題を回避し、確実にデータを取得します。
"""

import os
import pandas as pd
import jquantsapi
import yaml
from datetime import datetime

# ==========================================
# ★API Key (リフレッシュトークン)
# ==========================================
# もし config.yaml や 環境変数で設定していない場合は、ここに入れてください
REFRESH_TOKEN = "4tRpsBOq_cEf9yo6Db19OwS-3yeange8-saImd0mzCc"

DATA_DIR = "data_archive_daily"
CONFIG_FILE = "config.yaml"

def get_target_codes():
    """Configから取得対象の銘柄リストを抽出"""
    codes = set()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                symbols = cfg.get("symbols", [])
                for s in symbols:
                    codes.add(str(s["code"]))
        except: pass
    
    # 念のためハードコードでも追加
    backup_targets = [
        "7003", "6525", "9501", "6269", "6954", "5016", "8306", 
        "278A", "285A", "3110", "7974", "9984", "6506"
    ]
    for c in backup_targets:
        codes.add(c)
        
    return list(codes)

def main():
    print("=" * 60)
    print("🚀 主力銘柄 過去データ復元 (jquantsapi版)")
    print("=" * 60)
    
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # 1. クライアント初期化
    try:
        # トークンを指定してログイン
        cli = jquantsapi.Client(refresh_token=REFRESH_TOKEN)
        print("✅ J-Quants ログイン成功")
    except Exception as e:
        print(f"❌ ログイン失敗: {e}")
        print("   トークンが正しいか、有効期限が切れていないか確認してください。")
        return

    # 2. 対象リスト取得
    targets = get_target_codes()
    print(f"🎯 対象: {len(targets)} 銘柄")
    
    # 3. 一括取得 (過去2年分)
    end_dt = datetime.now()
    start_dt = end_dt.replace(year=end_dt.year - 2)
    
    print(f"📥 データをダウンロード中 (期間: {start_dt.strftime('%Y-%m-%d')} ~ )...")
    
    success_count = 0
    
    for code in targets:
        try:
            print(f"   Downloading {code} ...", end="")
            
            # ライブラリで取得 (5桁コード変換などはライブラリがよしなにしてくれる場合もあるが、念のため渡す)
            # get_prices_daily_quotes は便利
            df = cli.get_prices_daily_quotes(code=code, start_dt=start_dt, end_dt=end_dt)
            
            if df is not None and not df.empty:
                # 保存用に整形
                # Date, Open, High, Low, Close, Volume
                df = df.sort_values('Date')
                
                # Step1と同じ形式で保存
                save_path = os.path.join(DATA_DIR, f"daily_{code}.csv")
                
                # カラム名を小文字に統一して保存 (Step4が読みやすいように)
                df_save = pd.DataFrame({
                    'datetime': df['Date'].dt.strftime('%Y-%m-%d'),
                    'open': df['AdjustmentOpen'],
                    'high': df['AdjustmentHigh'],
                    'low': df['AdjustmentLow'],
                    'close': df['AdjustmentClose'],
                    'volume': df['AdjustmentVolume']
                })
                
                df_save.to_csv(save_path, index=False)
                print(f" ✅ {len(df)}日分")
                success_count += 1
            else:
                print(" ⚠️ データなし")
                
        except Exception as e:
            print(f" ❌ エラー: {e}")
            
    print("-" * 60)
    print(f"🎉 完了: {success_count} / {len(targets)} 銘柄")
    print("👉 step4_process_daily_log.py を実行してください")

if __name__ == "__main__":
    main()