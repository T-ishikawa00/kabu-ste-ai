# -*- coding: utf-8 -*-
"""
step0_fix_1min_data.py
======================
外部から持ってきた1分足データ(5020.csvなど)を
AIが学習できる「merged_*.csv」形式に変換・クリーンアップする
"""
import pandas as pd
import os
import glob

# 設定
INPUT_DIR = "."        # 5020.csv などがある場所
OUTPUT_DIR = "data"    # AIが読みに行く場所
os.makedirs(OUTPUT_DIR, exist_ok=True)

def fix_data():
    # 数字4桁.csv のファイルを探す
    files = glob.glob(os.path.join(INPUT_DIR, "[0-9][0-9][0-9][0-9].csv"))
    
    if not files:
        print("❌ 変換対象のCSV(5020.csvなど)が見つかりません。")
        return

    for filepath in files:
        code = os.path.basename(filepath).split('.')[0]
        print(f"🔧 加工中: {code} ...", end="")
        
        # 1. 2行目のゴミ(5020.Tなど)を飛ばして読み込む
        # header=0で1行目をヘッダーにし、1行目(index 0)をskiprowsで飛ばす
        df = pd.read_csv(filepath, skiprows=[1])
        
        # 2. 列名の修正 (ts -> datetime)
        if 'ts' in df.columns:
            df.rename(columns={'ts': 'datetime'}, inplace=True)
        
        # 3. データの型を数値に変換 (ゴミが混じっている可能性の排除)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # 4. 欠損値のある行を削除
        df.dropna(subset=['datetime', 'close'], inplace=True)
        
        # 5. 保存 (AIが探す merged_コード.csv にリネーム)
        out_path = os.path.join(OUTPUT_DIR, f"merged_{code}.csv")
        df.to_csv(out_path, index=False)
        print(f" ✅ -> {out_path} で保存完了")

if __name__ == "__main__":
    fix_data()