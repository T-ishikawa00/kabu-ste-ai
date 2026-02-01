# -*- coding: utf-8 -*-
"""
step5_debug_xray.py
===================
【レントゲン撮影】
読み込んだデータの中身を「数値レベル」で可視化します。
なぜトレードが発生しないのか、その瞬間の Price, VWAP, Volume を全て表示します。
"""

import os
import glob
import pandas as pd
import numpy as np

DATA_DIR = "data"

def normalize_column_names(df):
    """v19.5と同じ強力な正規化ロジック"""
    new_names = {}
    raw_cols = [str(c).lower() for c in df.columns]
    
    found_close = False
    found_volume = False
    found_vwap = False
    
    for i, col in enumerate(raw_cols):
        if not found_close and any(x in col for x in ['close', 'price', 'current']):
            new_names[df.columns[i]] = 'close'
            found_close = True
        if not found_volume and any(x in col for x in ['volume', 'vol', 'trading']):
            new_names[df.columns[i]] = 'volume'
            found_volume = True
        if not found_vwap and 'vwap' in col:
            new_names[df.columns[i]] = 'vwap'
            found_vwap = True

    if new_names:
        df.rename(columns=new_names, inplace=True)
        df = df.loc[:, ~df.columns.duplicated()]
    return df

def xray_scan():
    files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not files:
        print("❌ データファイルがありません。")
        return

    # 最初の1ファイルだけピックアップ
    target_file = files[0]
    print(f"🩺 診断対象ファイル: {os.path.basename(target_file)}")
    
    try:
        df = pd.read_csv(target_file)
        print(f"   元のカラム名: {list(df.columns)}")
        
        # 正規化実行
        df = normalize_column_names(df)
        print(f"   正規化後カラム: {list(df.columns)}")
        
        # 数値変換チェック
        if 'close' in df.columns:
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
        else:
            print("❌ 致命的: 'close' カラムが特定できませんでした！")
            return

        if 'vwap' not in df.columns:
            print("⚠️ 'vwap' なし -> closeで代用")
            df['vwap'] = df['close']
        else:
            df['vwap'] = pd.to_numeric(df['vwap'], errors='coerce')

        if 'volume' in df.columns:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
            vol_ma = df['volume'].rolling(20).mean()
            df['vol_ratio'] = df['volume'] / (vol_ma + 1e-9)
        else:
            print("⚠️ 'volume' なし -> 倍率1.0固定")
            df['vol_ratio'] = 1.0

        df = df.dropna(subset=['close'])
        
        print(f"   データ行数: {len(df)} 行")
        if len(df) == 0:
            print("❌ データが空っぽです！")
            return

        # --- ここからレントゲン撮影 ---
        print("\n🔍 【データの先頭10行 (生データ)】")
        # 重要な列だけ表示
        cols_to_show = ['datetime'] if 'datetime' in df.columns else []
        for c in ['close', 'vwap', 'volume', 'vol_ratio']:
            if c in df.columns: cols_to_show.append(c)
        print(df[cols_to_show].head(10).to_string())
        
        print("\n🔍 【判定ロジックの実況中継 (最初の20本)】")
        print("   ※条件: VWAP乖離 3.0%以内 / 出来高 0.5倍以上 (激甘設定)")
        
        closes = df['close'].values
        vwaps = df['vwap'].values
        vol_ratios = df['vol_ratio'].values
        
        # 最初の20本だけループ
        limit = min(20, len(df))
        for i in range(1, limit):
            p = closes[i]
            v = vwaps[i]
            vol = vol_ratios[i]
            
            # 判定
            msg = []
            
            # 1. Price vs VWAP
            if p == v:
                msg.append("❌ 値動きなし(Price==VWAP)")
            else:
                diff = p - v
                if diff > 0: msg.append(f"Price > VWAP (+{diff:.1f})")
                else: msg.append(f"Price < VWAP ({diff:.1f})")
            
            # 2. 乖離率
            dist = abs(p - v) / (v + 1e-9) * 100
            if dist > 3.0:
                msg.append(f"❌ 乖離しすぎ({dist:.1f}%)")
            else:
                msg.append(f"OK 乖離適正({dist:.2f}%)")
                
            # 3. 出来高
            if vol < 0.5:
                msg.append(f"❌ 過疎({vol:.2f}倍)")
            else:
                msg.append(f"OK 出来高({vol:.2f}倍)")
                
            print(f"[{i}] {p}円 | VWAP:{v:.1f} | {' / '.join(msg)}")

    except Exception as e:
        print(f"❌ 診断エラー: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    xray_scan()