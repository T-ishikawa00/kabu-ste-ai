# -*- coding: utf-8 -*-
import yfinance as yf
import pandas as pd
import os

def prepare_data(code="9509.T"):
    print(f"📥 {code} のデータを取得中...")
    
    # 過去7日間の1分足を取得 (yfinanceの制限で最大7日)
    # ※土日だと直近の金曜までのデータが取れるはず
    df = yf.download(code, period="5d", interval="1m")
    
    # yfinanceの仕様変更対策（マルチインデックス解除）
    if isinstance(df.columns, pd.MultiIndex):
        try:
            # カラム名が ('Close', '9509.T') のようになっている場合
            df.columns = df.columns.get_level_values(0)
        except Exception:
            pass
    
    # データ整理（必要な列だけ残す）
    # カラム名を小文字に統一
    df.columns = [c.lower() for c in df.columns]
    
    # 最低限必要なカラムがあるか確認
    required = ['open', 'high', 'low', 'close', 'volume']
    if not all(col in df.columns for col in required):
        print("❌ データの取得に失敗しました。カラム名が想定と異なります。")
        print(f"取得カラム: {df.columns}")
        return

    df = df[required].dropna()
    
    # --- 特徴量エンジニアリング（AIへのヒント作り） ---
    print("⚙️ 特徴量を作成中...")

    # 1. 移動平均線 (15分) との乖離率
    df['ma15'] = df['close'].rolling(window=15).mean()
    df['dist_ma15'] = (df['close'] - df['ma15']) / df['ma15']
    
    # 2. ボラティリティ (高値-安値の比率)
    df['volatility'] = (df['high'] - df['low']) / df['open']
    
    # 3. 出来高変化率 (20分平均との比)
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma']
    
    # 4. RSI (相対力指数) - 買われすぎ/売られすぎの判定用
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # 計算に使った期間（最初の20行など）のNaNを削除
    df = df.dropna().reset_index(drop=True)
    
    print(f"✅ データ準備完了: {len(df)} 行")
    print(df.head()) # 最初の5行を表示
    
    df.to_csv("training_data.csv", index=False)
    print("💾 training_data.csv に保存しました")

if __name__ == "__main__":
    # 北電 (9509) のデータを取得
    prepare_data("9509.T")