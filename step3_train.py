# -*- coding: utf-8 -*-
"""
step3_train.py (Fixed: Existing Model Priority)
===============================================
固定40銘柄の一括トレーニングスクリプト。

【修正ロジック】
1. まず「既存の個別モデル(v13)」を探します。
   → あれば、それをロードして続きから学習します（上書きしません）。
   
2. なければ、「汎用モデル」を探します。
   → あれば、それをベースに新規作成します（転移学習）。
   
3. どちらもなければ、ゼロから新規作成します。
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
import torch

warnings.filterwarnings("ignore")

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data"
MODEL_DIR = "model"
VERSION = "v13"  # ★ファイル名は変えずに固定

# ベースにする汎用モデル
UNIVERSAL_MODEL_PATH = os.path.join(MODEL_DIR, "model_universal_v1.zip")

# ★固定40銘柄リスト
TARGET_STOCKS = [
    "9501", "8411", "5803", "9984", "6954", "6269", "285A", "5801", "485A", "4570",
    "3110", "7974", "6525", "6330", "1662", "3350", "7746", "5016", "8306", "4506",
    "1893", "278A", "6702", "464A", "5101", "6501", "6758", "7267", "3382", "4446",
    "4082", "7003", "7735", "7711", "8135", "186A", "5802", "9107", "7453", "9227"
]

# 学習パラメータ
LEARNING_RATE = 0.0003
N_STEPS = 2048
BATCH_SIZE = 64
N_EPOCHS = 4
GAMMA = 0.90
ENT_COEF = 0.02
GAE_LAMBDA = 0.95
TOTAL_TIMESTEPS = 40000 


class DetailedStatsCallback(BaseCallback):
    """トレード成績ログ"""
    def __init__(self, check_freq=2048, verbose=1): 
        super().__init__(verbose)
        self.check_freq = check_freq
        self.interval_trades = 0
        self.interval_wins = 0
        self.interval_losses = 0
        self.interval_pnl = 0.0

    def _on_step(self):
        dones = self.locals['dones']
        infos = self.locals['infos']
        for i, done in enumerate(dones):
            if done:
                info = infos[i]
                balance = info.get('balance', 0)
                self.interval_pnl += balance
                if balance > 0: self.interval_wins += 1
                elif balance < 0: self.interval_losses += 1
                self.interval_trades += 1
        
        if self.n_calls % self.check_freq == 0:
            win_rate = 0
            if self.interval_trades > 0:
                win_rate = self.interval_wins / self.interval_trades * 100
            
            icon = "➖"
            if self.interval_pnl > 0: icon = "📈"
            if self.interval_pnl < 0: icon = "📉"
            if win_rate > 60 and self.interval_pnl > 0: icon = "🔥"

            print(f"   [Stats] {icon} 勝率: {win_rate:.1f}% ({self.interval_wins}-{self.interval_losses}) | 損益: {self.interval_pnl:+.2f}")
            self.interval_trades = 0
            self.interval_wins = 0
            self.interval_losses = 0
            self.interval_pnl = 0.0
        return True


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not csv_files:
        print("❌ データなし: utils_restore_data.py を実行してください")
        return

    # 汎用モデルがあるか確認しておく
    has_universal = os.path.exists(UNIVERSAL_MODEL_PATH)
    
    targets = [t.upper() for t in TARGET_STOCKS]

    print(f"{'='*60}")
    print(f"🏋️‍♀️ 固定銘柄トレーニング (既存優先モード)")
    print(f"   対象: {len(targets)} 銘柄")
    print(f"{'='*60}")

    success_count = 0
    skip_count = 0

    for stock_code in targets:
        target_file = None
        for f in csv_files:
            if f"merged_{stock_code}.csv" in f:
                target_file = f
                break
        
        if not target_file:
            print(f"⚠️ データなし: {stock_code}")
            skip_count += 1
            continue

        try:
            df = pd.read_csv(target_file)
            if len(df) < 200: 
                print(f"⚠️ データ不足: {stock_code}")
                skip_count += 1
                continue
            
            split_idx = int(len(df) * 0.9)
            df_train = df.iloc[:split_idx].reset_index(drop=True)
            
            def make_env():
                return TradingEnv(df_train, stock_code=stock_code)
            
            env = DummyVecEnv([make_env])
            env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.)
            
            model_name = f"model_{stock_code}_{VERSION}"
            model_path = os.path.join(MODEL_DIR, model_name)
            
            print(f"\n──────────────────────────────────────────────────")
            print(f"📊 学習開始: {stock_code}")
            
            model = None
            
            # =========================================================
            # ★ 修正ポイント: 既存モデル優先ロジック
            # =========================================================
            
            # 1. まず既存の個別モデルを探す
            if os.path.exists(f"{model_path}.zip"):
                try:
                    print(f"   🔄 既存モデル({VERSION})を発見。ロードして継続学習します。")
                    model = PPO.load(model_path, env=env, device='auto', verbose=1)
                    
                    # 次元の不一致チェック
                    saved_dim = model.policy.observation_space.shape[0]
                    current_dim = env.observation_space.shape[0]
                    
                    if saved_dim != current_dim:
                        print(f"   ⚠️ 次元不一致 ({saved_dim}!={current_dim}) -> 新規作成します。")
                        model = None
                    else:
                        # 既存モデルの場合は通常の学習率でしっかり学ぶ
                        model.learning_rate = LEARNING_RATE
                        
                except Exception as e:
                    print(f"   ⚠️ 既存モデル読み込みエラー: {e}")
                    model = None

            # 2. 既存がない場合 -> 汎用モデルから転移学習
            if model is None and has_universal:
                try:
                    print(f"   🆕 個別モデルなし -> 汎用モデルから転移学習します (Fine-Tuning)")
                    model = PPO.load(UNIVERSAL_MODEL_PATH, env=env, device='auto', verbose=1)
                    
                    # 転移学習なので学習率を下げて、知識を壊さないように微調整
                    model.learning_rate = 0.0001
                    
                except Exception as e:
                    print(f"   ⚠️ 転移学習失敗: {e}")
                    model = None
            
            # 3. それでもダメなら -> 完全新規作成
            if model is None:
                print(f"   ✨ 完全新規作成します (Zero-Scratch)")
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
                    verbose=1,
                    device='auto'
                )
            
            # 学習実行
            callback = DetailedStatsCallback(check_freq=N_STEPS)
            model.learn(total_timesteps=TOTAL_TIMESTEPS, callback=callback)
            
            # 保存
            model.save(model_path)
            env.save(os.path.join(MODEL_DIR, f"{model_name}_env.pkl"))
            
            print(f"   ✅ 保存完了: {model_name}.zip")
            success_count += 1

        except Exception as e:
            print(f"❌ エラー ({stock_code}): {e}")
            continue

    print(f"\n{'='*60}")
    print(f"🎉 全プロセス完了")
    print(f"   成功: {success_count} / {len(targets)}")
    print(f"{'='*60}")

if __name__ == "__main__":
    torch.set_num_threads(1)
    train()