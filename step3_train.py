# -*- coding: utf-8 -*-
"""
step3_train.py v12.0 (Win Rate Focused Training)
================================================
目標: 勝率重視のミニスイングAIを育てる
改善点:
  - 学習/検証データの分離（過学習防止）
  - 学習中の勝率モニタリング
  - 銘柄ごとの最適なハイパーパラメータ
"""

import os
import glob
import pandas as pd
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback
from step2_env import TradingEnv
import warnings

warnings.filterwarnings("ignore")

# 設定
DATA_DIR = "data"
MODEL_DIR = "model"
VERSION = "v12"

# ハイパーパラメータ
LEARNING_RATE = 0.0003      # 学習率
N_STEPS = 2048              # 1回の更新に使うステップ数
BATCH_SIZE = 64             # ミニバッチサイズ
N_EPOCHS = 4               # 1更新あたりのエポック数
GAMMA = 0.80                # 割引率（長期報酬を重視）
ENT_COEF = 0.01             # エントロピー係数
GAE_LAMBDA = 0.95           # GAEのλ


class WinRateCallback(BaseCallback):
    """学習中の勝率をモニタリングするコールバック"""
    
    def __init__(self, check_freq=5000, verbose=1):
        super().__init__(verbose)
        self.check_freq = check_freq
        self.best_win_rate = 0.0
        self.win_rates = []
        
    def _on_step(self):
        if self.n_calls % self.check_freq == 0:
            # 環境から勝率情報を取得
            if hasattr(self.training_env, 'envs'):
                env = self.training_env.envs[0]
                if hasattr(env, 'episode_wins'):
                    wins = env.episode_wins
                    losses = env.episode_losses
                    total = wins + losses
                    if total > 0:
                        win_rate = wins / total * 100
                        self.win_rates.append(win_rate)
                        
                        if win_rate > self.best_win_rate:
                            self.best_win_rate = win_rate
                        
                        print(f"  Step {self.n_calls}: 勝率 {win_rate:.1f}% ({wins}勝/{losses}敗) | 最高: {self.best_win_rate:.1f}%")
        
        return True


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    
    if not csv_files:
        print("❌ データファイルが見つかりません。")
        print("   先に step4_process_daily_log.py を実行してデータを作成してください。")
        return

    print(f"{'='*60}")
    print(f"🎯 勝率重視AI学習 v12.0")
    print(f"{'='*60}")
    print(f"📋 検出銘柄数: {len(csv_files)}")
    print(f"📊 学習パラメータ:")
    print(f"   Learning Rate: {LEARNING_RATE}")
    print(f"   Gamma (長期報酬重視): {GAMMA}")
    print(f"   Entropy Coef: {ENT_COEF}")
    print(f"{'='*60}")

    trained_count = 0
    skipped_count = 0

    for csv_file in csv_files:
        try:
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            
            df = pd.read_csv(csv_file)
            
            # データ量チェック
            if len(df) < 200:
                print(f"\n⚠️ データ不足: {stock_code} ({len(df)}行) → 最低200行必要")
                skipped_count += 1
                continue
            
            print(f"\n{'─'*50}")
            print(f"📈 学習開始: {stock_code} ({len(df)}行)")
            
            # データ分割（80%訓練、20%検証）
            split_idx = int(len(df) * 0.8)
            df_train = df.iloc[:split_idx].reset_index(drop=True)
            df_test = df.iloc[split_idx:].reset_index(drop=True)
            
            print(f"   訓練: {len(df_train)}行 / 検証: {len(df_test)}行")
            
            # 環境作成
            def make_env():
                return TradingEnv(df_train, stock_code=stock_code)
            
            env = DummyVecEnv([make_env])
            env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.)
            
            # モデルの準備
            model_name = f"model_{stock_code}_{VERSION}"
            model_path = os.path.join(MODEL_DIR, model_name)
            
            if os.path.exists(f"{model_path}.zip"):
                print(f"   🔄 既存モデルをロードして継続学習")
                model = PPO.load(model_path, env=env, device='cpu')
                model.learning_rate = LEARNING_RATE
            else:
                print(f"   ✨ 新規モデルを作成")
                model = PPO(
                    "MlpPolicy",
                    env,
                    learning_rate=LEARNING_RATE,
                    n_steps=N_STEPS,
                    batch_size=BATCH_SIZE,
                    n_epochs=N_EPOCHS,
                    gamma=GAMMA,
                    ent_coef=ENT_COEF,
                    gae_lambda=GAE_LAMBDA,
                    verbose=0,
                    device='cpu'
                )
            
            # 学習ステップ数（データ不足のため、過学習を防ぐために短く固定）
            # total_steps = min(300000, max(50000, len(df_train) * 100)) 
            total_steps = 30000  # ★これに書き換え！
            
            print(f"   📊 学習ステップ: {total_steps:,} (過学習防止モード)")
            
            # コールバック
            callback = WinRateCallback(check_freq=10000)
            
            # 学習実行
            model.learn(total_timesteps=total_steps, callback=callback)
            
            # 保存
            model.save(model_path)
            env.save(os.path.join(MODEL_DIR, f"{model_name}_env.pkl"))
            
            # 最終勝率表示
            if callback.win_rates:
                final_win_rate = callback.win_rates[-1]
                print(f"   ✅ 完了: 最終勝率 {final_win_rate:.1f}% / 最高 {callback.best_win_rate:.1f}%")
            else:
                print(f"   ✅ 完了: {model_name}")
            
            trained_count += 1

        except Exception as e:
            print(f"\n⚠️ エラー ({stock_code}): {e}")
            import traceback
            traceback.print_exc()
            continue

    print(f"\n{'='*60}")
    print(f"🎉 学習完了!")
    print(f"   成功: {trained_count} 銘柄")
    print(f"   スキップ: {skipped_count} 銘柄")
    print(f"\n👉 次: py -3.12 step5_backtest.py でテスト")
    print(f"{'='*60}")


if __name__ == "__main__":
    train()