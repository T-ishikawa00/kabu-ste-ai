# -*- coding: utf-8 -*-
"""
train_individual.py v20.5 (Individual Stock AI)
===============================================
【概要】
銘柄ごとの専用AIを作成するコード。
汎用モデル(v20)の知識をベースに、各銘柄に特化したモデルを育成。

【使い方】
1. まず train_universal.py で汎用モデルを作成
2. データが集まったら本コードで個別AIを作成
3. controller.py が自動的に個別AIを優先使用（要設定）
"""

import os
import glob
import pandas as pd
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback

from step2_env_universal import TradingEnv
import warnings

warnings.filterwarnings("ignore")

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data"
MODEL_DIR = "model"  # 個別モデルは model/ に保存

# バージョン
VERSION = "v20"

# ★学習モード選択
FORCE_NEW_MODEL = False      # True=既存個別モデルを無視して再作成 / False=継続学習
USE_PRETRAINED = True        # True=汎用モデルをベースに / False=完全ゼロから
PRETRAINED_PATH = "runtime/model_universal_v20"  # 汎用モデル（先生）のパス

# ★対象銘柄リスト
TARGET_STOCKS = [
    # --- 固定枠（空売り可能） ---
    "7003",  # 三井E&S
    "6525",  # KOKUSAI
    "7011",  # 三菱重工
    "6269",  # 三井海洋
    "6954",  # ファナック
    "5016",  # 新光商事
    "8306",  # 三菱UFJ
    "278A",  # テラドローン
    "285A",  # キオクシア
    "3110",  # 日東紡
    "8031",  # 三井物産
    "9984",  # ソフトバンクG
    "9107",  # 川崎汽船
]

# 学習パラメータ
LEARNING_RATE = 0.0003
TRANSFER_LEARNING_RATE = 0.0001  # 転移学習時は低めに
N_STEPS = 2048
BATCH_SIZE = 64
N_EPOCHS = 10
GAMMA = 0.99
ENT_COEF = 0.01
GAE_LAMBDA = 0.95
TOTAL_TIMESTEPS = 100_000  # 個別は10万ステップ


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
                if balance > 0:
                    self.interval_wins += 1
                elif balance < 0:
                    self.interval_losses += 1
                self.interval_trades += 1
        
        if self.n_calls % self.check_freq == 0:
            win_rate = 0
            if self.interval_trades > 0:
                win_rate = self.interval_wins / self.interval_trades * 100
            
            icon = "➖"
            if self.interval_pnl > 0:
                icon = "📈"
            if self.interval_pnl < 0:
                icon = "📉"
            if win_rate > 60 and self.interval_pnl > 0:
                icon = "🔥"

            print(f"   [Stats] {icon} 勝率: {win_rate:.1f}% "
                  f"({self.interval_wins}-{self.interval_losses}) | "
                  f"損益: {self.interval_pnl:+.2f}")
            
            self.interval_trades = 0
            self.interval_wins = 0
            self.interval_losses = 0
            self.interval_pnl = 0.0
        return True


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not csv_files:
        print("❌ データなし: data/merged_*.csv を配置してください")
        return

    targets = [t.upper() for t in TARGET_STOCKS]

    print("=" * 60)
    print(f"🎯 個別銘柄AI育成 v20.0")
    print(f"   対象: {len(targets)} 銘柄")
    print(f"   ステップ: {TOTAL_TIMESTEPS:,} / 銘柄")
    print("=" * 60)
    
    # 汎用モデルの存在確認
    has_universal = os.path.exists(f"{PRETRAINED_PATH}.zip")
    if USE_PRETRAINED:
        if has_universal:
            print(f"   📚 汎用モデル: {PRETRAINED_PATH}.zip (転移学習に使用)")
        else:
            print(f"   ⚠️ 汎用モデルなし: 完全新規で作成します")

    success_count = 0
    skip_count = 0

    for stock_code in targets:
        # データファイル検索
        target_file = None
        for f in csv_files:
            if f"merged_{stock_code}.csv" in f.replace("\\", "/"):
                target_file = f
                break
        
        if not target_file:
            print(f"\n⚠️ データなし: {stock_code}")
            skip_count += 1
            continue

        try:
            df = pd.read_csv(target_file)
            df.columns = [str(c).lower().strip() for c in df.columns]
            
            if len(df) < 200:
                print(f"\n⚠️ データ不足: {stock_code} ({len(df)}行)")
                skip_count += 1
                continue
            
            # 訓練データ（90%）
            split_idx = int(len(df) * 0.9)
            df_train = df.iloc[:split_idx].reset_index(drop=True)
            
            # 環境作成
            def make_env():
                return TradingEnv(df_train, stock_code=stock_code)
            
            env = DummyVecEnv([make_env])
            env = VecNormalize(env, norm_obs=True, norm_reward=False, clip_obs=10.)
            
            model_name = f"model_{stock_code}_{VERSION}"
            model_path = os.path.join(MODEL_DIR, model_name)
            
            print(f"\n{'─'*50}")
            print(f"📊 学習開始: {stock_code} ({VERSION})")
            print(f"   データ: {len(df_train):,} 行")
            
            model = None
            learning_rate = LEARNING_RATE
            
            # ==========================================
            # モデル選択ロジック
            # ==========================================
            
            if not FORCE_NEW_MODEL and os.path.exists(f"{model_path}.zip"):
                # 1. 既存の個別モデルで継続学習
                try:
                    print(f"   🔄 既存モデル発見 → 継続学習")
                    model = PPO.load(model_path, env=env, device='auto', verbose=0)
                except Exception as e:
                    print(f"   ⚠️ ロード失敗: {e}")
                    model = None
            
            if model is None and USE_PRETRAINED and has_universal:
                # 2. 汎用モデルから転移学習
                try:
                    print(f"   📚 汎用モデルから転移学習")
                    model = PPO.load(PRETRAINED_PATH, env=env, device='auto', verbose=0)
                    learning_rate = TRANSFER_LEARNING_RATE  # 慎重に学習
                except Exception as e:
                    print(f"   ⚠️ 転移失敗: {e}")
                    model = None
            
            if model is None:
                # 3. 完全新規作成
                print(f"   ✨ 完全新規作成")
                model = PPO(
                    "MlpPolicy",
                    env,
                    learning_rate=learning_rate,
                    n_steps=N_STEPS,
                    batch_size=BATCH_SIZE,
                    n_epochs=N_EPOCHS,
                    gamma=GAMMA,
                    ent_coef=ENT_COEF,
                    gae_lambda=GAE_LAMBDA,
                    verbose=0,
                    device='auto',
                    policy_kwargs=dict(net_arch=[dict(pi=[128, 64], vf=[128, 64])])
                )
            
            # 学習率を更新（転移学習の場合）
            if learning_rate != LEARNING_RATE:
                model.learning_rate = learning_rate
            
            # 学習実行
            callback = DetailedStatsCallback(check_freq=N_STEPS)
            model.learn(total_timesteps=TOTAL_TIMESTEPS, callback=callback)
            
            # 保存
            model.save(model_path)
            env.save(os.path.join(MODEL_DIR, f"{model_name}_env.pkl"))
            
            print(f"   ✅ 完了: {model_name}.zip")
            success_count += 1

        except Exception as e:
            print(f"❌ エラー ({stock_code}): {e}")
            import traceback
            traceback.print_exc()
            continue

    print(f"\n{'='*60}")
    print(f"🎉 全プロセス完了")
    print(f"   成功: {success_count} / {len(targets)}")
    print(f"   スキップ: {skip_count}")
    print(f"{'='*60}")


if __name__ == "__main__":
    torch.set_num_threads(1)
    train()