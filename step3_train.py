# -*- coding: utf-8 -*-
import os
import pandas as pd
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from step2_env import TradingEnv

def train():
    print("="*60)
    print("🤖 AIトレーニングを開始します (Project KABU-EVO)")
    print("="*60)

    # 1. データの読み込み
    csv_path = "training_data.csv"
    if not os.path.exists(csv_path):
        print("❌ training_data.csv が見つかりません。Step 1 を実行してください。")
        return

    df = pd.read_csv(csv_path)
    print(f"📚 学習データ: {len(df)} 行 (北電 9509)")

    # 2. 環境の作成
    # Stable Baselines3 は「ベクトル化環境(VecEnv)」を好むのでラップする
    env = DummyVecEnv([lambda: TradingEnv(df)])

    # 3. AIモデルの構築 (PPOアルゴリズム)
    # MlpPolicy: 画像ではなく数値データ用の脳みそ
    # verbose=1: 学習状況を画面に表示する
    model = PPO("MlpPolicy", env, verbose=1, learning_rate=0.0003)

    print("🔥 学習スタート！ (完了まで数分お待ちください...)")
    print("-" * 60)

    # 4. 学習実行 (5万回試行錯誤させる)
    # PCスペックにもよるが、1分〜5分程度で終わるはず
    model.learn(total_timesteps=50000)

    print("-" * 60)
    print("✅ 学習完了！")

    # 5. モデルの保存
    save_name = "model_9509_v4"
    model.save(save_name)
    print(f"💾 脳みそを保存しました: {save_name}.zip")
    print("="*60)

if __name__ == "__main__":
    train()