# -*- coding: utf-8 -*-
"""
step3_train_individual.py v20.15 (Portfolio Specialization)
===========================================================
【機能】
1. 最新の汎用モデル (model_universal_v20) を「先生」として読み込む。
2. 指定された「ターゲット銘柄ポートフォリオ」に特化して追加学習を行う。
3. 汎用の基礎知識（損切りの重要性など）を持った状態でスタートするため、
   ゼロから学習するより圧倒的に早く、かつ高精度に育つ。

【設定】
- 入場料: 0.002
- STAY報酬: 0.0001
- SL: -1.0% / TP: +2.0% (環境設定は step2_env_universal.py 依存)
"""

import os
import glob
import pandas as pd
import numpy as np
import torch
import warnings
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback

# ★最新の環境クラスを読み込む
try:
    from step2_env_universal import TradingEnv
except ImportError:
    raise ImportError("❌ 'step2_env_universal.py' が見つかりません。")

warnings.filterwarnings("ignore")

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data"
MODEL_DIR = "runtime"  # v20系は runtime フォルダを使用

# 親モデル（先生）
UNIVERSAL_MODEL_NAME = "model_universal_v20"
UNIVERSAL_MODEL_PATH = os.path.join(MODEL_DIR, f"{UNIVERSAL_MODEL_NAME}.zip")

# ★学習対象ポートフォリオ
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
    "8031", # 三井物産
    "9107", # 川崎汽船
    "5016", # 良品計画 or JX金属 (※CSVがあるか要確認)
    "3110", # 日東紡

    # --- 低位株・その他 ---
    "8306", # 三菱UFJ
    "6954", # ファナック
    "278A", # テラドローン
    "285A", # キオクシア
]

# 学習パラメータ
LEARNING_RATE = 0.0001  # 転移学習なので、少し慎重に学習させる
N_STEPS = 2048
BATCH_SIZE = 64
N_EPOCHS = 10        
GAMMA = 0.95            # スキャルピング特化
ENT_COEF = 0.01   
TOTAL_TIMESTEPS = 70_000 # 1銘柄あたり50万ステップ (十分です)


class DetailedStatsCallback(BaseCallback):
    """トレード成績ログ"""
    def __init__(self, check_freq=2048, verbose=1): 
        super().__init__(verbose)
        self.check_freq = check_freq

    def _on_step(self):
        if self.n_calls % self.check_freq == 0:
            # 環境内部の stats を覗き見る
            try:
                env = self.training_env.envs[0]
                if hasattr(env, "episode_wins") and hasattr(env, "episode_losses"):
                    wins = env.episode_wins
                    losses = env.episode_losses
                    total = wins + losses
                    win_rate = (wins / total * 100) if total > 0 else 0
                    
                    print(f"   [Stats] 累積勝率: {win_rate:.1f}% ({wins}W-{losses}L)")
            except:
                pass
        return True


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not csv_files:
        print("❌ データなし: dataフォルダを確認してください")
        return

    print(f"{'='*60}")
    print(f"🏋️‍♀️ ポートフォリオ個別AI育成 (Parent: {UNIVERSAL_MODEL_NAME})")
    print(f"   対象: {len(TARGET_STOCKS)} 銘柄")
    print(f"{'='*60}")

    for stock_code in TARGET_STOCKS:
        # ファイル検索
        target_file = None
        for f in csv_files:
            if f"merged_{stock_code}.csv" in f:
                target_file = f
                break
        
        if not target_file:
            print(f"⚠️ データなし: {stock_code} -> スキップ")
            continue

        try:
            # データ読み込み
            df = pd.read_csv(target_file)
            df.columns = [str(c).lower().strip() for c in df.columns]
            
            if len(df) < 300: 
                print(f"⚠️ データ不足: {stock_code} ({len(df)}行) -> スキップ")
                continue
            
            # 学習用データセット作成
            df_train = df.reset_index(drop=True)
            
            # 環境初期化 (最新の step2_env_universal を使用)
            def make_env():
                return TradingEnv(df_train, stock_code=stock_code)
            
            env = DummyVecEnv([make_env])
            # 個別銘柄ごとに正規化統計を新規作成
            env = VecNormalize(env, norm_obs=True, norm_reward=False, clip_obs=10.)
            
            # モデル保存パス
            model_name = f"model_individual_{stock_code}"
            model_path = os.path.join(MODEL_DIR, model_name)
            
            print(f"\n──────────────────────────────────────────────────")
            print(f"📊 学習開始: {stock_code}")
            
            model = None
            
            # 1. 既存の個別モデルがあれば続きから (Resume)
            if os.path.exists(f"{model_path}.zip"):
                print(f"   🔄 既存モデルあり -> 継続学習")
                try:
                    model = PPO.load(model_path, env=env, device='auto')
                except:
                    model = None

            # 2. なければ汎用モデルから転移 (Transfer)
            if model is None and os.path.exists(UNIVERSAL_MODEL_PATH):
                print(f"   🆕 汎用モデル(先生)から転移学習 -> 新規作成")
                try:
                    model = PPO.load(UNIVERSAL_MODEL_PATH, env=env, device='auto')
                    model.learning_rate = LEARNING_RATE
                    model.num_timesteps = 0
                except Exception as e:
                    print(f"   ⚠️ 転移失敗 ({e}) -> 完全新規作成")
                    model = None
            
            # 3. それもなければ完全新規 (Scratch)
            if model is None:
                print(f"   ✨ 完全新規作成 (Zero-Scratch)")
                model = PPO(
                    "MlpPolicy",
                    env,
                    learning_rate=LEARNING_RATE,
                    n_steps=N_STEPS,
                    batch_size=BATCH_SIZE,
                    n_epochs=N_EPOCHS,
                    gamma=GAMMA,
                    ent_coef=ENT_COEF,
                    verbose=0,
                    device='auto'
                )
            
            # 学習実行
            callback = DetailedStatsCallback(check_freq=20000)
            model.learn(total_timesteps=TOTAL_TIMESTEPS, callback=callback, progress_bar=True)
            
            # 保存
            model.save(model_path)
            env.save(os.path.join(MODEL_DIR, f"{model_name}_env.pkl"))
            
            print(f"   ✅ 学習完了: {model_name}.zip")

        except Exception as e:
            print(f"❌ エラー ({stock_code}): {e}")
            continue

    print(f"\n{'='*60}")
    print(f"🎉 全個別モデルの学習完了")
    print(f"{'='*60}")

if __name__ == "__main__":
    torch.set_num_threads(1)
    train()