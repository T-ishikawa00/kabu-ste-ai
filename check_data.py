# -*- coding: utf-8 -*-
"""
check_data.py
=============
個別学習データの健康診断
"""
import os
import pandas as pd
import glob

# 診断対象（step3_train_individual.py と同じリスト）
TARGET_STOCKS = [
    "6525", "6526", "7011", "7003", "6269",
    "8031", "9107", "5016", "3110", "8306", "6954", "278A", "285A", "9984", "5803"
]

DATA_DIR = "data"

def check():
    print(f"{'CODE':<6} {'行数':>8} {'推定日数':>8} {'判定':<10} {'詳細'}")
    print("-" * 60)
    
    warnings = 0
    
    for code in TARGET_STOCKS:
        path = os.path.join(DATA_DIR, f"merged_{code}.csv")
        
        if not os.path.exists(path):
            print(f"{code:<6} {'---':>8} {'---':>8} ❌ なし      ファイルが見つかりません")
            warnings += 1
            continue
            
        try:
            df = pd.read_csv(path)
            rows = len(df)
            
            # 1分足と仮定して、1日約300行 (9:00-15:00)
            est_days = rows / 300
            
            judge = ""
            msg = ""
            
            if rows < 1000: # 3日未満
                judge = "💀 危険"
                msg = "データ不足すぎます (暗記して終わり)"
                warnings += 1
            elif rows < 5000: # 2週間程度
                judge = "⚠️ 注意"
                msg = "最低限レベル (過学習リスクあり)"
            elif rows < 20000: # 2ヶ月程度
                judge = "✅ 良好"
                msg = "個別学習に十分"
            else:
                judge = "💎 最高"
                msg = "文句なし"
                
            print(f"{code:<6} {rows:>8,d} {est_days:>8.1f}日 {judge:<10} {msg}")
            
        except Exception as e:
            print(f"{code:<6} Error: {e}")

    print("-" * 60)
    if warnings > 0:
        print(f"🚨 {warnings} 銘柄でデータ不足または欠損があります！")
        print("   → 学習しても「井の中の蛙」AIになってしまいます。")
    else:
        print("✨ 全銘柄、データ量は十分です！")

if __name__ == "__main__":
    check()