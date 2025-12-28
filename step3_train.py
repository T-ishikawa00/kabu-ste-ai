# -*- coding: utf-8 -*-
import os
import glob
import pandas as pd
import numpy as np
import pandas_ta as ta  # ★追加: テクニカル分析用ライブラリ
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
import warnings

# 自作の環境クラスをインポート
from step2_env import TradingEnv

# 警告抑制（GPU関連のワーニングがうるさい場合があるため）
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)


# ★ここが重要！RCIを計算する魔法の関数
def calc_rci(series, period=9):
    """
    RCI (Rank Correlation Index) を計算する関数
    Pythonの標準ライブラリにはないので自作します。
    """
    rci = []
    # データが足りない部分はNaN
    for i in range(len(series)):
        if i < period - 1:
            rci.append(np.nan)
            continue
        
        # 直近 period 個のデータを取得
        s = series.iloc[i - period + 1 : i + 1]
        
        # 「日付の順位」と「価格の順位」の相関を計算
        # 日付順位: 1, 2, ..., period
        time_rank = np.arange(1, period + 1)
        # 価格順位: 小さい順にランク付け
        price_rank = s.rank(method='max').values
        
        # RCI計算式: (1 - 6 * sum(d^2) / (n^3 - n)) * 100
        d_sq = np.sum((time_rank - price_rank) ** 2)
        val = (1 - (6 * d_sq) / (period**3 - period)) * 100
        rci.append(val)
        
    return pd.Series(rci, index=series.index)


def load_and_process_data(code):
    """
    データ収集機(DataCollector)が集めたCSV(data_raw配下)を読み込み、
    AI用の新兵器(RCI, Parabolic, MACD, BB, OBI)を追加する
    """
    # data_raw フォルダ内の全日付フォルダから対象銘柄のCSVを探す
    files = glob.glob(f"data_raw/*/{code}.csv")
    
    if not files:
        # まだデータがない場合は、旧フォルダ(data/)も一応探してみる（移行期間用）
        legacy_path = f"data/merged_{code}.csv"
        if os.path.exists(legacy_path):
            files = [legacy_path]
        else:
            print(f"⚠️ スキップ: {code} のデータが見つかりません。")
            return None
    
    print(f"📚 {code}: {len(files)} 個のファイルを統合中...")
    
    df_list = []
    for f in files:
        try:
            tmp = pd.read_csv(f)
            df_list.append(tmp)
        except Exception as e:
            print(f"⚠️ 読み込みエラー {f}: {e}")
            
    if not df_list:
        return None
        
    # 全日付を結合して、時系列順に並べる
    df = pd.concat(df_list).sort_values("timestamp").reset_index(drop=True)
    
    # --- データ加工エリア (AIへのご馳走作り) ---
    
    # 0. カラム名の互換性確保
    if 'price' in df.columns:
        df['close'] = df['price']
    
    # 1. 板気配 (スキャルピング用)
    if 'bid_qty' in df.columns and 'ask_qty' in df.columns:
        # 0除算を防ぐために分母に微小な値を足す
        df['obi'] = (df['bid_qty'] - df['ask_qty']) / (df['bid_qty'] + df['ask_qty'] + 1e-9)
    else:
        df['obi'] = 0.0

    # 2. テクニカル指標の追加 (師匠たちの教えVer)
    try:
        # --- (A) パラボリック (トレンド転換サイン) ---
        # 列名: PSARl_... (Long), PSARs_... (Short)
        df.ta.psar(high='high', low='low', close='close', af0=0.02, af=0.2, append=True)

        # --- (B) RCI (押し目買いの王様) ---
        # 短期(9), 中期(26), 長期(52)
        df['rci_9'] = calc_rci(df['close'], period=9)
        df['rci_26'] = calc_rci(df['close'], period=26)
        # df['rci_52'] = calc_rci(df['close'], period=52) # データ量不足ならコメントアウトしてもOK

        # --- (C) ボリンジャーバンド (行き過ぎ判定) ---
        # 列名: BBP_20_2.0 等
        df.ta.bbands(close='close', length=20, std=2, append=True)
        
        # --- (D) MACD (トレンド) ---
        # 列名: MACD_12_26_9 等
        df.ta.macd(close='close', fast=12, slow=26, signal=9, append=True)

        # --- (E) RSI (一応残す) ---
        df.ta.rsi(close='close', length=14, append=True)

    except Exception as e:
        print(f"⚠️ 指標計算エラー: {e}")

    # 3. NaN (欠損値) 対策
    # 計算の過程で最初の数行はNaNになるので、0で埋める
    df.fillna(0, inplace=True)
    
    print(f"✅ データ加工完了: {len(df)}行 (RCI, Parabolic, MACD, BB, OBI 追加済み)")
    return df


def train_and_save(code):
    print("="*60)
    print(f"🤖 AIトレーニングを開始します: 銘柄コード {code}")
    print("="*60)

    # 1. データ読み込み & 加工
    df = load_and_process_data(code)
    if df is None:
        return

    # 2. 環境の作成
    # ※注意: step2_env.py の observation_space もこの指標数に合わせて修正が必要ですが、
    #  とりあえず今の step2_env が NaN対策版(7次元)なら、余った指標は無視されて学習されます。
    #  (本当は step2 も書き換えて、RCIやパラボリックを受け取るようにすると最強になります)
    env = DummyVecEnv([lambda: TradingEnv(df)])

    # 3. AIモデルの構築
    # 指標とロジックが変わったので、モデルのバージョンを v6 (師匠Ver) に上げる
    model_name = f"model_{code}_v6"
    model_path = f"{model_name}.zip"

    if os.path.exists(model_path):
        print(f"🔄 既存の脳みそ ({model_name}) をロードして、学習を再開します...")
        try:
            model = PPO.load(model_name, env=env)
        except Exception as e:
            print(f"⚠️ ロード失敗のため新規作成: {e}")
            model = PPO("MlpPolicy", env, verbose=1, device="auto")
    else:
        print(f"✨ 新しい脳みそ ({model_name}) を作成します...")
        # 過去のモデルとは構造が違うので、新規作成推奨
        model = PPO("MlpPolicy", env, verbose=1, device="auto")

    print("🔥 学習スタート！ (RTX 4060 Ti 稼働中...)")
    print("-" * 60)

    # 4. 学習実行
    # データ量が増えてきたら step 数を増やしてもOK
    model.learn(total_timesteps=50000)

    print("-" * 60)
    print(f"✅ {code} の学習完了！")

    # 5. モデルの保存
    model.save(model_name)
    print(f"💾 脳みそを保存しました: {model_name}.zip")
    print("="*60)


if __name__ == "__main__":
    # 今日のログに出てきた銘柄 + α
    target_codes = [
        "6146", "9501", "9509", "8031", "6758", "6954", "9107", "8411",
        "9984", "8058", "7014", "8306", "6920", "7203", "7003", "5803"
        "7267", "6526", "7974", "7011", "6506", "285A"
    ]
    
    for code in target_codes:
        train_and_save(code)