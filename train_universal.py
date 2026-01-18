# -*- coding: utf-8 -*-
"""
train_universal.py v16.1 (Safe Loader)
======================================
全銘柄のデータを統合して「汎用AIモデル」を学習する。

【安全機能】
- dataフォルダに大量のファイルがあっても、
  メモリ保護のため「最大300銘柄」までしか読み込みません。
- ただし、config.yaml にある「監視銘柄」は最優先で読み込みます。
"""

import os
import glob
import pandas as pd
import numpy as np
import yaml
import random
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
MODEL_NAME = "model_universal_v1"
VERSION = "v16"

# ★メモリ保護リミッター
# 32GBメモリなら 300~500 くらいが安全圏
MAX_FILES_TO_LOAD = 300 

# ハイパーパラメータ
LEARNING_RATE = 0.0001
N_STEPS = 4096
BATCH_SIZE = 256
N_EPOCHS = 4
GAMMA = 0.85
ENT_COEF = 0.02
GAE_LAMBDA = 0.95
CLIP_RANGE = 0.2

# 学習設定
TOTAL_TIMESTEPS = 1_000_000
MIN_DATA_ROWS = 200
NUM_ENVS = 4


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
                
                print(f"  Step {self.n_calls:,}: "
                      f"勝率 {win_rate:.1f}% | 最高 {self.best_win_rate:.1f}%")
        return True


def get_priority_symbols():
    """Configから優先的に読み込むべき銘柄リストを取得"""
    priority_codes = set()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                symbols = config.get("symbols", [])
                for s in symbols:
                    priority_codes.add(str(s["code"]))
        except:
            pass
    return priority_codes

def load_all_data():
    """リミッター付きデータ読み込み"""
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not csv_files:
        print("❌ データファイルが見つかりません")
        return [], []
    
    # 優先銘柄とその他を分ける
    priority_codes = get_priority_symbols()
    priority_files = []
    other_files = []
    
    for f in csv_files:
        filename = os.path.basename(f)
        code = filename.replace("merged_", "").replace(".csv", "")
        if code in priority_codes:
            priority_files.append(f)
        else:
            other_files.append(f)
            
    print(f"🔍 ファイル検出: 計 {len(csv_files)} 件")
    print(f"   ★優先(Config): {len(priority_files)} 件")
    print(f"   ・その他: {len(other_files)} 件")

    # 読み込みリストを作成
    # まず優先ファイルを全部入れる
    load_targets = priority_files[:]
    
    # まだ枠が余っていれば、その他からランダムに追加
    remaining_slots = MAX_FILES_TO_LOAD - len(load_targets)
    if remaining_slots > 0 and other_files:
        # ランダムに選ぶ（毎回違う銘柄で学習できるメリットも）
        sampled_others = random.sample(other_files, min(len(other_files), remaining_slots))
        load_targets.extend(sampled_others)
    
    print(f"📉 メモリ保護のため {len(load_targets)} ファイルに絞って読み込みます...")

    all_dfs = []
    all_codes = []
    total_rows = 0
    
    for csv_file in load_targets:
        try:
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            df = pd.read_csv(csv_file)
            
            if len(df) < MIN_DATA_ROWS:
                continue
            
            all_dfs.append(df)
            all_codes.append(stock_code)
            total_rows += len(df)
            
        except Exception:
            continue
    
    print(f"📊 読み込み完了: {len(all_dfs)}銘柄 / 総行数 {total_rows:,}")
    return all_dfs, all_codes


def make_env(all_dfs, all_codes, rank=0):
    def _init():
        return UniversalTradingEnv(all_dfs, all_codes)
    return _init


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    print("=" * 70)
    print("🌐 汎用AIモデル学習 v16.1 (Safe Loader)")
    print("=" * 70)
    
    all_dfs, all_codes = load_all_data()
    if not all_dfs:
        print("❌ 学習データなし")
        return

    print(f"\n🔧 環境構築中...")
    env_fns = [make_env(all_dfs, all_codes, i) for i in range(NUM_ENVS)]
    env = DummyVecEnv(env_fns)
    env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.)
    
    model_path = os.path.join(MODEL_DIR, MODEL_NAME)
    model = None
    is_new = True

    # 既存モデルチェック
    if os.path.exists(f"{model_path}.zip"):
        try:
            print(f"\n🔄 既存モデルをチェック中...")
            temp_model = PPO.load(model_path, env=env, device='auto')
            saved_dim = temp_model.policy.observation_space.shape[0]
            current_dim = env.observation_space.shape[0]
            
            if saved_dim != current_dim:
                print(f"⚠️ 次元不一致 ({saved_dim} != {current_dim}) -> 新規作成")
                model = None
            else:
                print(f"✅ 互換性OK。継続学習します。")
                model = temp_model
                is_new = False
        except Exception as e:
            print(f"♻️  新規作成します ({e})")
            model = None
    
    if model is None:
        if is_new: print(f"\n✨ 新規汎用モデル作成")
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
            device='auto',
            tensorboard_log="./tb_logs/"
        )
    
    callback = WinRateCallback(check_freq=20000)
    
    print(f"\n{'─'*70}")
    print(f"🚀 学習開始! (目標: {TOTAL_TIMESTEPS:,} steps)")
    print(f"{'─'*70}")
    
    try:
        model.learn(total_timesteps=TOTAL_TIMESTEPS, callback=callback, progress_bar=True)
        model.save(model_path)
        env.save(f"{model_path}_env.pkl")
        print(f"\n🎉 学習完了!")
        
    except KeyboardInterrupt:
        print("\n⚠️ 中断: 現状を保存します")
        model.save(model_path)
        env.save(f"{model_path}_env.pkl")

if __name__ == "__main__":
    torch.set_num_threads(1)
    train()