# -*- coding: utf-8 -*-
"""
train_universal.py v19.3 (VWAP Native & Fresh Data Only)
========================================================
全銘柄データを統合して汎用モデル(先生)を鍛える。

【v19.3 修正点】
1. データの読み込み時、タイムスタンプを確認して
   「直近2時間以内に更新されたファイル（＝さっきStep4で作ったやつ）」
   だけを対象にするフィルタを追加。
   → フォルダに古いゴミデータが残っていても無視して、最新のデータだけで学習します。
"""

import os
import glob
import pandas as pd
import numpy as np
import yaml
import time
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback

from step2_env_universal import TradingEnv
import warnings
import torch

warnings.filterwarnings("ignore")

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data"
MODEL_DIR = "model"
CONFIG_FILE = "config.yaml"
TARGET_LIST_FILE = "target_list.csv"

# モデル名
MODEL_NAME = "model_universal_v19"

# ハイパーパラメータ
LEARNING_RATE = 0.0003
N_STEPS = 2048          
BATCH_SIZE = 64
N_EPOCHS = 10
GAMMA = 0.99           
ENT_COEF = 0.005       
GAE_LAMBDA = 0.95
CLIP_RANGE = 0.2

# 学習回数
TOTAL_TIMESTEPS = 1_000_000 
MIN_DATA_ROWS = 200
NUM_ENVS = 4

# ★データ鮮度リミット (秒)
# Step4実行から時間が経ちすぎているファイルは無視 (2時間 = 7200秒)
DATA_FRESHNESS_LIMIT = 7200


class UniversalTradingEnv(TradingEnv):
    """汎用学習用の環境ラッパー"""
    def __init__(self, all_dataframes: list, stock_codes: list):
        self.all_dfs = all_dataframes
        self.all_codes = stock_codes
        self.current_df_idx = 0
        super().__init__(all_dataframes[0], stock_code="UNIVERSAL")
        
    def reset(self, seed=None, options=None):
        if seed is not None:
            np.random.seed(seed)
        self.current_df_idx = np.random.randint(0, len(self.all_dfs))
        self.df = self.all_dfs[self.current_df_idx].reset_index(drop=True)
        self.stock_code = self.all_codes[self.current_df_idx]
        
        if self.stock_code in self.TIME_LIMITS:
            self.MAX_HOLD = self.TIME_LIMITS[self.stock_code]
        else:
            self.MAX_HOLD = 60 # デフォルト
            
        self.MAX_STEPS = len(self.df) - 1
        return super().reset(seed=seed, options=options)


class WinRateCallback(BaseCallback):
    """学習中の勝率をモニタリング"""
    def __init__(self, check_freq=10000, verbose=1):
        super().__init__(verbose)
        self.check_freq = check_freq
        self.best_win_rate = 0.0
        self.total_wins = 0
        self.total_losses = 0
        
    def _on_step(self):
        if self.n_calls % self.check_freq == 0:
            wins = 0
            losses = 0
            
            for env in self.training_env.envs:
                if hasattr(env, 'episode_wins'):
                    wins += env.episode_wins
                    losses += env.episode_losses
            
            new_wins = wins - self.total_wins
            new_losses = losses - self.total_losses
            self.total_wins = wins
            self.total_losses = losses
            
            total = new_wins + new_losses
            if total > 0:
                win_rate = new_wins / total * 100
                if win_rate > self.best_win_rate:
                    self.best_win_rate = win_rate
                
                print(f"   Step {self.n_calls:,}: "
                      f"勝率 {win_rate:.1f}% ({new_wins}-{new_losses}) | 最高 {self.best_win_rate:.1f}%")
        return True


def get_training_symbols():
    target_codes = set()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                symbols = config.get("symbols", [])
                for s in symbols:
                    target_codes.add(str(s["code"]))
        except: pass

    if os.path.exists(TARGET_LIST_FILE):
        try:
            df = pd.read_csv(TARGET_LIST_FILE)
            for c in df['code'].astype(str).tolist():
                if len(c) == 5 and c.endswith('0'): c = c[:-1]
                target_codes.add(c)
        except: pass
        
    return target_codes

def load_all_data():
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not csv_files:
        print("❌ データファイルが見つかりません")
        return [], []
    
    load_targets = []
    now_ts = time.time()
    skipped_old = 0
    
    # ★修正: タイムスタンプでフィルタリング
    for f in csv_files:
        mtime = os.path.getmtime(f)
        if now_ts - mtime <= DATA_FRESHNESS_LIMIT:
            load_targets.append(f)
        else:
            skipped_old += 1
            
    print(f"🔍 読み込み対象: {len(load_targets)} ファイル (古い {skipped_old} 件を除外)")

    all_dfs = []
    all_codes = []
    total_rows = 0
    
    for csv_file in load_targets:
        try:
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            df = pd.read_csv(csv_file)
            if len(df) < MIN_DATA_ROWS: continue
            
            all_dfs.append(df)
            all_codes.append(stock_code)
            total_rows += len(df)
        except Exception:
            continue
    
    if len(all_dfs) == 0:
        print("⚠️ 有効なデータが0件です。Step4を実行してください。")
        
    print(f"📊 読み込み完了: {len(all_dfs)}銘柄 / 総行数 {total_rows:,}")
    return all_dfs, all_codes


def make_env(all_dfs, all_codes, rank=0):
    def _init():
        return UniversalTradingEnv(all_dfs, all_codes)
    return _init


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    print("=" * 70)
    print(f"🌐 汎用AIモデル学習 v19.3 (Fresh Data Only)")
    print(f"   Model Name: {MODEL_NAME}")
    print("=" * 70)
    
    all_dfs, all_codes = load_all_data()
    if not all_dfs:
        return

    print(f"\n🔧 環境構築中...")
    env_fns = [make_env(all_dfs, all_codes, i) for i in range(NUM_ENVS)]
    env = DummyVecEnv(env_fns)
    env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.)
    
    model_path = os.path.join(MODEL_DIR, MODEL_NAME)
    model = None
    
    # ==========================================
    # ★Smart Resume (継続学習ロジック)
    # ==========================================
    if os.path.exists(f"{model_path}.zip"):
        try:
            print(f"\n🔄 既存の汎用モデルを発見。ロードして続きから学習します...")
            temp_model = PPO.load(model_path, env=env, device='auto')
            
            saved_dim = temp_model.observation_space.shape[0]
            current_dim = env.observation_space.shape[0]
            
            if saved_dim != current_dim:
                print(f"⚠️ 次元不一致 ({saved_dim} != {current_dim}) -> 新規作成します")
                model = None
            else:
                print(f"✅ 互換性OK。継続学習開始。")
                model = temp_model
        except Exception as e:
            print(f"♻️  ロード失敗 ({e}) -> 新規作成します")
            model = None
    
    if model is None:
        print(f"\n✨ 新規汎用モデル作成 (Zero-Scratch v19)")
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
            clip_range=CLIP_RANGE,
            verbose=1,
            device='auto'
        )
    
    callback = WinRateCallback(check_freq=20000)
    
    print(f"\n{'─'*70}")
    print(f"🚀 学習開始! (目標: {TOTAL_TIMESTEPS:,} steps)")
    print(f"{'─'*70}")
    
    try:
        model.learn(total_timesteps=TOTAL_TIMESTEPS, callback=callback, progress_bar=True)
        model.save(model_path)
        env.save(f"{model_path}_env.pkl")
        print(f"\n🎉 学習完了! -> {model_path}.zip")
        
    except KeyboardInterrupt:
        print("\n⚠️ 中断: 現状を保存します")
        model.save(model_path)
        env.save(f"{model_path}_env.pkl")

if __name__ == "__main__":
    torch.set_num_threads(1)
    train()