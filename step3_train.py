# -*- coding: utf-8 -*-
"""
step3_train.py v19.2 (Smart Resume & New Portfolio)
===================================================
【v19.2 最終完成版】
1. 銘柄リストを「資金効率重視」に更新 (SCREEN除外、三井物産・IHI追加)。
2. 学習ロジックを「継続学習優先」に変更。
   - 既存v19があれば続きから。
   - なければ汎用モデルから転移。
   - それもなければ新規作成。
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

# バージョン管理 (v19系: VWAP対応)
VERSION = "v19" 

# 汎用モデル（先生）のパス
UNIVERSAL_MODEL_PATH = os.path.join(MODEL_DIR, "model_universal_v19.zip")

# ★最新ポートフォリオ (SCREEN, SBGを除外 / 8031, 7013等を追加)
TARGET_STOCKS = [
    # --- 攻撃担当 (半導体・ハイテク) ---
    "6525", # KOKUSAI
    "6526", # ソシオネクスト
    "6871", # 日本マイクロニクス
    
    # --- 主力担当 (重工・防衛) ---
    "7011", # 三菱重工
    "7013", # IHI
    "7003", # 三井E&S
    "6269", # 三井海洋

    # --- 安定担当 (商社・海運・素材) ---
    "8031", # 三井物産 (New!)
    "9107", # 川崎汽船
    "5016", # 良品計画 or JX金属
    "3110", # 日東紡

    # --- 低位株・その他 ---
    "8306", # 三菱UFJ
    "6954", # ファナック (残す場合)
    "278A", # テラドローン
    "285A", # キオクシア
]

# 学習パラメータ
LEARNING_RATE = 0.0003
N_STEPS = 2048
BATCH_SIZE = 64
N_EPOCHS = 10        
GAMMA = 0.99       
ENT_COEF = 0.01   
GAE_LAMBDA = 0.95
TOTAL_TIMESTEPS = 100000 


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
        print("❌ データなし: Step4を実行してください")
        return

    targets = [t.upper() for t in TARGET_STOCKS]

    print(f"{'='*60}")
    print(f"🏋️‍♀️ スナイパーAI育成 (v19.2: Smart Resume)")
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
            
            # 環境初期化
            def make_env():
                return TradingEnv(df_train, stock_code=stock_code)
            
            env = DummyVecEnv([make_env])
            env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.)
            
            model_name = f"model_{stock_code}_{VERSION}"
            model_path = os.path.join(MODEL_DIR, model_name)
            
            print(f"\n──────────────────────────────────────────────────")
            print(f"📊 学習開始: {stock_code} ({VERSION})")
            
            model = None
            
            # ==========================================
            # ★優先順位ロジック (ここが心臓部)
            # ==========================================
            
            # 1. 自分の過去モデル(v19)があるか？ -> あれば続きから (Continue)
            if os.path.exists(f"{model_path}.zip"):
                try:
                    print(f"   🔄 既存の個別モデルを発見。続きから学習します。")
                    model = PPO.load(model_path, env=env, device='auto', verbose=0)
                except Exception as e:
                    print(f"   ⚠️ 既存モデル読み込み失敗: {e}")
                    model = None

            # 2. なければ先生(Universal)がいるか？ -> いれば転移 (Transfer)
            if model is None and os.path.exists(UNIVERSAL_MODEL_PATH):
                try:
                    print(f"   🆕 汎用モデル(先生)から知識を引き継ぎます...")
                    model = PPO.load(UNIVERSAL_MODEL_PATH, env=env, device='auto', verbose=0)
                    model.learning_rate = 0.0001 # 先生を壊さないよう慎重に
                except Exception as e:
                    print(f"   ⚠️ 転移失敗: {e}")
                    model = None

            # 3. どちらもなければ新規 (New)
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
                    verbose=0,
                    device='auto'
                )
            
            # 学習実行
            callback = DetailedStatsCallback(check_freq=N_STEPS)
            model.learn(total_timesteps=TOTAL_TIMESTEPS, callback=callback)
            
            # 保存
            model.save(model_path)
            env.save(os.path.join(MODEL_DIR, f"{model_name}_env.pkl"))
            
            print(f"   ✅ 学習完了: {model_name}.zip")
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