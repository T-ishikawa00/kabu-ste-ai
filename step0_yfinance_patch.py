# -*- coding: utf-8 -*-
"""
step0_yfinance_patch.py
=======================
【最終兵器】
J-Quantsでデータが取れない銘柄（285A, 278Aなど）を
yfinanceの「1分足」データで強制的に穴埋めします。

保存先: data_archive_daily
→ これを実行した後、step4 を通すことでテクニカル指標が付与されます。
"""

import os
import yfinance as yf
import pandas as pd
import time

# ==========================================
# 設定
# ==========================================
# ★重要: Step4が読み込める場所に保存する
DATA_DIR = "data_archive_daily"
os.makedirs(DATA_DIR, exist_ok=True)

# 穴埋め対象の銘柄コード
TARGET_CODES = [
    "8031", 
    # リストになくてもConfigにある銘柄はここに追加すれば救済されます
]

def download_and_save(code):
    # ファイル名もStep4に合わせて daily_XXXX.csv にする
    file_path = os.path.join(DATA_DIR, f"daily_{code}.csv")
    
    # 既存チェック: ファイルがあり、かつ十分な行数(30行以上)があるならスキップ
    if os.path.exists(file_path):
        try:
            df_check = pd.read_csv(file_path)
            if len(df_check) > 30:
                print(f"🛡️  [{code}] はJ-Quantsデータが十分あります ({len(df_check)}行) -> Skip")
                return
            else:
                print(f"⚠️ [{code}] データ不足 ({len(df_check)}行) -> yfinanceで上書きします")
        except:
            pass

    print(f"📥 [{code}] yfinance(1分足)を取得中...", end=" ")
    
    ticker = f"{code}.T"
    try:
        # 1分足を5日分取得 (これで数千行になるのでStep4の基準をクリアできる)
        df = yf.download(ticker, period="5d", interval="1m", progress=False)
        
        if df.empty:
            print("❌ データなし (yfinanceにもありません)")
            return

        # マルチインデックス解除
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
        # カラム名を統一
        df.columns = [c.lower() for c in df.columns]
        
        # 必要なカラム作成
        # yfinanceは 'open', 'high', 'low', 'close', 'volume' を持っている
        required = ['open', 'high', 'low', 'close', 'volume']
        
        # 欠損除去
        df = df.dropna()
        
        if len(df) == 0:
            print("❌ 有効データなし")
            return

        # 整形して保存
        # J-Quants形式に合わせる: datetime, open, high, low, close, volume
        # yfinanceのindexはdatetime型
        
        output_df = pd.DataFrame({
            'datetime': df.index,
            'open': df['open'],
            'high': df['high'],
            'low': df['low'],
            'close': df['close'],
            'volume': df['volume']
        })
        
        # タイムゾーン情報を削除 (Step4が読みやすいように)
        output_df['datetime'] = output_df['datetime'].astype(str).str.replace('+09:00', '')
        
        output_df.to_csv(file_path, index=False)
        print(f"✅ 保存完了 ({len(output_df)}行)")
        
    except Exception as e:
        print(f"❌ エラー: {e}")

def main():
    print("=" * 60)
    print("🚑 yfinance データ救済 (1分足モード)")
    print("   J-Quantsで取れなかった銘柄を強制的に埋めます")
    print("=" * 60)
    
    count = 0
    for code in TARGET_CODES:
        download_and_save(str(code))
        time.sleep(0.5) 
        
    print("-" * 60)
    print("🎉 補完完了！")
    print("👉 必ず 'step4_process_daily_log.py' を実行して、テクニカル指標を計算させてください。")
    print("=" * 60)

if __name__ == "__main__":
    main()