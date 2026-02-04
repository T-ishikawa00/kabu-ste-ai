# -*- coding: utf-8 -*-
"""
step0_yfinance_patch.py
=======================
【最終兵器・直保存版】
Configから全銘柄を読み込み、yfinanceの「1分足」データ(最大7日分)を取得。
これを直接 `data_raw` フォルダに保存します。

保存先: data_raw/daily_{code}.csv
注意: 既存の「板情報付きデータ」がある場合は上書きしません。
"""

import os
import sys
import time
import yfinance as yf
import pandas as pd

# Config読み込み
try:
    from config_loader import load_cfg
except ImportError:
    sys.path.append(os.getcwd())
    from config_loader import load_cfg

# ==========================================
# 設定: 保存先を data_raw に変更
# ==========================================
DATA_DIR = "data_raw"
os.makedirs(DATA_DIR, exist_ok=True)

def get_target_codes():
    """Configから監視銘柄リストを取得"""
    try:
        cfg = load_cfg()
        return [str(s.code) for s in cfg.symbols]
    except Exception as e:
        print(f"❌ Config読み込み失敗: {e}")
        return []

def download_and_save(code):
    file_path = os.path.join(DATA_DIR, f"daily_{code}.csv")
    
    # 既存チェック (安全装置)
    # data_rawにある既存データが「板情報(bid_qty)」を持っているなら、
    # yfinanceの劣るデータで上書きしてはいけない。
    if os.path.exists(file_path):
        try:
            df_check = pd.read_csv(file_path)
            # 既に1000行以上あり、かつ bid_qty (板情報) があるなら上書きしない
            has_board = 'bid_qty' in df_check.columns or 'BidQty' in df_check.columns
            if len(df_check) > 1000 and has_board:
                print(f"🛡️  [{code}] 実戦データあり(板情報付)のため上書き回避 -> Skip")
                return
        except:
            pass

    print(f"📥 [{code}] yfinance取得中(7日分) -> data_raw...", end=" ")
    
    ticker = f"{code}.T"
    try:
        # 1分足を最大期間(7日)取得
        df = yf.download(ticker, period="7d", interval="1m", progress=False)
        
        if df.empty:
            print("❌ データなし")
            return

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
        df.columns = [c.lower() for c in df.columns]
        df = df.dropna()

        if len(df) == 0:
            print("❌ 有効データなし")
            return

        # 整形
        output_df = pd.DataFrame({
            'datetime': df.index,
            'open': df['open'],
            'high': df['high'],
            'low': df['low'],
            'close': df['close'],
            'volume': df['volume']
        })
        
        # タイムゾーン削除
        output_df['datetime'] = output_df['datetime'].astype(str).str.replace(r'\+.*', '', regex=True)
        
        # 保存 (data_rawへ)
        output_df.to_csv(file_path, index=False)
        print(f"✅ 保存完了 ({len(output_df)}行)")
        
    except Exception as e:
        print(f"❌ エラー: {e}")

def main():
    print("=" * 60)
    print("🚑 yfinance データ救済 (data_raw 直保存モード)")
    print("=" * 60)
    
    targets = get_target_codes()
    print(f"📋 処理対象: {len(targets)} 銘柄")
    
    if not targets:
        print("❌ 銘柄が見つかりません。")
        return

    for code in targets:
        download_and_save(code)
        time.sleep(1.0) 
        
    print("-" * 60)
    print("🎉 完了！ data_raw フォルダを確認してください。")
    print("👉 この後は通常通り 'step4' を実行すれば統合されます。")
    print("=" * 60)

if __name__ == "__main__":
    main()