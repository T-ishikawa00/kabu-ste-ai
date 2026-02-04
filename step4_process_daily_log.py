# -*- coding: utf-8 -*-
"""
step4_process_daily_log_v21_safe.py
==============================================
【修正点】
1. データの「追記（Merge）」ロジックを安全化
2. 読み込みエラー時に上書きせずスキップする安全装置（Safety Guard）を追加
3. データが以前より減った場合に警告を出すサニティチェックを追加
"""

import os
import glob
import pandas as pd
import numpy as np
import warnings

warnings.simplefilter('ignore')

# ==========================================
# 設定
# ==========================================
SEARCH_DIR = "data_raw"
DATA_OUT_DIR = "data"

def find_live_data_files():
    print(f"🔍 {SEARCH_DIR} 以下からCSVファイルを捜索中...")
    all_csvs = glob.glob(os.path.join(SEARCH_DIR, "**", "*.csv"), recursive=True)
    return all_csvs

def process_file(filepath):
    try:
        # 1. 読み込み
        try:
            df = pd.read_csv(filepath, encoding='utf-8-sig', on_bad_lines='skip')
        except:
            df = pd.read_csv(filepath, encoding='cp932', on_bad_lines='skip')

        # カラム名正規化
        df.columns = [str(c).lower().strip().replace("'", "").replace('"', "") for c in df.columns]
        cols = list(df.columns)

        # カラム特定
        col_dt = next((c for c in ['timestamp', 'date', 'datetime'] if c in cols), None)
        col_close = next((c for c in ['current_price', 'currentprice', 'trade_price', 'current', 'price', 'close', 'last_price'] if c in cols), None)
        col_vol = next((c for c in ['trading_volume', 'tradingvolume', 'volume', 'vol'] if c in cols), None)
        col_vwap = 'vwap' if 'vwap' in cols else None
        
        # Open/High/Low
        col_open = 'open' if 'open' in cols else None
        col_high = 'high' if 'high' in cols else None
        col_low = 'low' if 'low' in cols else None
        
        # ★ 板情報カラム
        col_bid_qty = next((c for c in ['bid_qty', 'bidqty', 'best_bid_qty'] if c in cols), None)
        col_ask_qty = next((c for c in ['ask_qty', 'askqty', 'best_ask_qty'] if c in cols), None)
        col_over_sell = next((c for c in ['over_sell_qty', 'oversellqty', 'over_sell'] if c in cols), None)
        col_under_buy = next((c for c in ['under_buy_qty', 'underbuyqty', 'under_buy'] if c in cols), None)

        if not col_dt or not col_close: 
            return None, "必須カラム不足"
        
        use_cols = {col_dt: 'datetime', col_close: 'close'}
        if col_vol: use_cols[col_vol] = 'volume'
        if col_vwap: use_cols[col_vwap] = 'vwap'
        if col_open: use_cols[col_open] = 'open'
        if col_high: use_cols[col_high] = 'high'
        if col_low: use_cols[col_low] = 'low'
        
        # 板情報マッピング
        if col_bid_qty: use_cols[col_bid_qty] = 'bid_qty'
        if col_ask_qty: use_cols[col_ask_qty] = 'ask_qty'
        if col_over_sell: use_cols[col_over_sell] = 'over_sell_qty'
        if col_under_buy: use_cols[col_under_buy] = 'under_buy_qty'
        
        df = df[list(use_cols.keys())].rename(columns=use_cols)

        # 2. 日時変換
        df['datetime'] = pd.to_datetime(df['datetime'], format='mixed', errors='coerce')
        df = df.dropna(subset=['datetime'])
        df.set_index('datetime', inplace=True)
        df.sort_index(inplace=True)

        # 3. 数値変換
        if 'volume' in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors='coerce').fillna(0)
        else:
            df["volume"] = 0
            
        df["close"] = pd.to_numeric(df["close"], errors='coerce').ffill()
        if 'vwap' in df.columns: 
            df['vwap'] = pd.to_numeric(df['vwap'], errors='coerce')
        
        for col in ['bid_qty', 'ask_qty', 'over_sell_qty', 'under_buy_qty']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 4. リサンプリング (1分足)
        agg_dict = {'close': 'last', 'volume': 'last'}
        if 'vwap' in df.columns: agg_dict['vwap'] = 'last'
        
        for col in ['bid_qty', 'ask_qty', 'over_sell_qty', 'under_buy_qty']:
            if col in df.columns: agg_dict[col] = 'last'
        
        if 'open' in df.columns: 
            agg_dict.update({'open':'first', 'high':'max', 'low':'min'})
        else: 
            agg_dict.update({'close': ['first', 'max', 'min', 'last']})

        df_1min = df.resample('1min').agg(agg_dict)
        
        # カラム名整理
        if isinstance(df_1min.columns, pd.MultiIndex):
            if 'open' not in df.columns:
                ohlc_cols = ['open', 'high', 'low', 'close']
                other_cols = [c[0] for c in df_1min.columns[4:]]
                df_1min.columns = ohlc_cols + other_cols
            else:
                df_1min.columns = [c[0] if isinstance(c, tuple) else c for c in df_1min.columns]

        # 5. 欠損補完
        df_1min['close'] = df_1min['close'].ffill()
        for c in ['open', 'high', 'low', 'vwap']:
            if c in df_1min.columns: 
                df_1min[c] = df_1min[c].fillna(df_1min['close'])
        
        for col in ['bid_qty', 'ask_qty', 'over_sell_qty', 'under_buy_qty']:
            if col in df_1min.columns:
                df_1min[col] = df_1min[col].ffill().fillna(0)

        # 6. 出来高の差分計算（累積値の場合）
        if 'volume' in df_1min.columns:
            vol_filled = df_1min['volume'].ffill()
            vol_diff = vol_filled.diff().fillna(0)
            if (vol_diff >= 0).mean() > 0.8: # 8割以上が増加なら累積とみなす
                df_1min['volume'] = vol_diff.clip(lower=0)
            else:
                df_1min['volume'] = df_1min['volume'].fillna(0)
        
        df = df_1min.dropna(subset=['close'])
        df = df.iloc[5:] 
        
        # 特徴量計算 (OBIなど)
        if 'bid_qty' in df.columns and 'ask_qty' in df.columns:
            total_qty = df['bid_qty'] + df['ask_qty']
            df['obi'] = np.where(total_qty > 0, df['bid_qty'] / total_qty, 0.5)
            df['obi_change'] = df['obi'].diff(5).fillna(0)
        
        if 'over_sell_qty' in df.columns and 'ask_qty' in df.columns:
            df['sell_pressure'] = np.where(df['ask_qty'] > 0, df['over_sell_qty'] / (df['ask_qty'] + 1), 0)
        
        if 'under_buy_qty' in df.columns and 'bid_qty' in df.columns:
            df['buy_pressure'] = np.where(df['bid_qty'] > 0, df['under_buy_qty'] / (df['bid_qty'] + 1), 0)
        
        if 'over_sell_qty' in df.columns and 'under_buy_qty' in df.columns:
            total_depth = df['over_sell_qty'] + df['under_buy_qty']
            df['ndbi'] = np.where(total_depth > 0, (df['under_buy_qty'] - df['over_sell_qty']) / total_depth, 0)
        
        # SMA
        try:
            df['SMA_25'] = df['close'].rolling(25).mean()
            df['dist_sma25'] = (df['close'] - df['SMA_25']) / (df['SMA_25'] + 1e-9) * 100
        except: 
            pass

        if len(df) == 0: 
            return None, "データ消失"
        
        return df, "OK"

    except Exception as e:
        return None, f"Err: {str(e)}"

def main():
    print("=" * 60)
    print("🚀 データ救出作戦 v21.0 Safe (板情報対応)")
    print("=" * 60)
    os.makedirs(DATA_OUT_DIR, exist_ok=True)
    
    files = find_live_data_files()
    if not files:
        print("❌ ファイルが見つかりません")
        return

    print(f"✨ {len(files)} ファイルを処理します...")
    success_count = 0
    board_info_count = 0
    
    for filepath in files:
        filename = os.path.basename(filepath)
        code = filename.replace("daily_", "").replace(".csv", "")
        # board_xxxx.csv などの対応
        if "board_" in filename:
            parts = filename.split("_")
            if len(parts) >= 2: code = parts[1]
        
        # 数字を含まないファイル名はスキップ
        if not any(char.isdigit() for char in code): continue

        # 新しいデータの処理
        df_new_data, status = process_file(filepath)
        
        if df_new_data is not None and len(df_new_data) > 0:
            save_path = os.path.join(DATA_OUT_DIR, f"merged_{code}.csv")
            df_new_data['code'] = code
            
            # 板情報チェック
            has_board_info = 'obi' in df_new_data.columns
            if has_board_info: board_info_count += 1
            
            # === 安全なマージ処理 ===
            if os.path.exists(save_path):
                try:
                    # 既存ファイルの読み込み
                    df_old = pd.read_csv(save_path)
                    
                    # インデックス設定（エラー回避のため明示的に指定）
                    if 'datetime' in df_old.columns:
                        df_old['datetime'] = pd.to_datetime(df_old['datetime'])
                        df_old.set_index('datetime', inplace=True)
                    else:
                        # datetimeカラムがない場合は異常ありとしてスキップするか、index_col=0を試す
                        df_old = pd.read_csv(save_path, index_col=0, parse_dates=True)

                    # 結合
                    old_len = len(df_old)
                    df_merged = pd.concat([df_old, df_new_data], axis=0)
                    
                    # 重複削除 (インデックスの重複を削除、新しいデータを優先)
                    df_merged = df_merged[~df_merged.index.duplicated(keep='last')]
                    df_merged.sort_index(inplace=True)
                    
                    # サニティチェック: データが減っていたらおかしい
                    if len(df_merged) < old_len:
                        print(f"⚠️ {code}: データが減少しています ({old_len} -> {len(df_merged)})。結合をスキップします。")
                        continue
                        
                    # 採用
                    df_final = df_merged
                    
                except Exception as e:
                    print(f"🚨 {code}: 既存データの結合に失敗しました。上書きを防止します。Error: {e}")
                    continue  # 重大なエラーなので、このファイルの保存はスキップする
            else:
                # 新規作成
                df_final = df_new_data

            # 保存
            try:
                df_final.to_csv(save_path)
                success_count += 1
                board_mark = "📊" if has_board_info else "  "
                print(f"   {board_mark} ✅ {code}: 成功 ({len(df_final)}行)")
            except Exception as e:
                print(f"❌ {code}: 保存エラー {e}")

        else:
            print(f"   ❌ {code}: {status}")

    print("-" * 60)
    print(f"🎉 完了！ 成功: {success_count} / 全体: {len(files)}")
    print(f"📊 板情報あり: {board_info_count} 銘柄")
    print("=" * 60)

if __name__ == "__main__":
    main()