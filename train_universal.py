# -*- coding: utf-8 -*-
"""
train_universal.py v20.6 (Simplified)
=====================================
【設計】
- モデルファイルあり → 継続学習
- モデルファイルなし → 新規作成
- 中断時は保存しない（不完全なモデルを防ぐ）

【使い方】
1. ステップ数を設定: TOTAL_TIMESTEPS
2. 実行: py -3.12 train_universal.py
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
# ★設定（ここだけ変更）
# ==========================================
TOTAL_TIMESTEPS = 500_000   # 学習ステップ数

# ハイパーパラメータ
LEARNING_RATE = 0.0003
N_STEPS = 2048
BATCH_SIZE = 64
N_EPOCHS = 10
GAMMA = 0.95
ENT_COEF = 0.01
GAE_LAMBDA = 0.95
CLIP_RANGE = 0.2

# パス
DATA_DIR = "data"
MODEL_DIR = "runtime"
MODEL_NAME = "model_universal_v20"

# その他
MIN_DATA_ROWS = 100
NUM_ENVS = 4


# ==========================================
# 環境クラス
# ==========================================
class UniversalTradingEnv(TradingEnv):
    """複数銘柄をランダムに切り替える"""
    def __init__(self, all_dataframes: list, stock_codes: list):
        self.all_dfs = all_dataframes
        self.all_codes = stock_codes
        super().__init__(all_dataframes[0], stock_code=stock_codes[0])
        
    def reset(self, seed=None, options=None):
        if seed is not None:
            np.random.seed(seed)
        
        idx = np.random.randint(0, len(self.all_dfs))
        self.df = self.all_dfs[idx].copy().reset_index(drop=True)
        self.stock_code = self.all_codes[idx]
        self.MAX_STEPS = len(self.df) - 1
        
        if self.stock_code in self.TIME_LIMITS:
            self.MAX_HOLD = self.TIME_LIMITS[self.stock_code]
        else:
            self.MAX_HOLD = 60
        
        self.trades = []
        return super().reset(seed=seed, options=options)


# ==========================================
# コールバック
# ==========================================
class WinRateCallback(BaseCallback):
    """勝率 + アクション比率モニタリング"""
    def __init__(self, check_freq=20000):
        super().__init__()
        self.check_freq = check_freq
        self.best_win_rate = 0.0
        self.total_wins = 0
        self.total_losses = 0
        self.total_actions = [0, 0, 0]  # STAY, BUY, SHORT
        
    def _on_step(self):
        if self.n_calls % self.check_freq == 0:
            wins, losses = 0, 0
            actions = [0, 0, 0]
            
            for env in self.training_env.envs:
                if hasattr(env, 'trades'):
                    for pnl in env.trades:
                        if pnl > 0:
                            wins += 1
                        elif pnl < 0:
                            losses += 1
                
                # アクションカウント集計
                if hasattr(env, 'action_counts'):
                    for i in range(3):
                        actions[i] += env.action_counts[i]
            
            new_wins = wins - self.total_wins
            new_losses = losses - self.total_losses
            self.total_wins = wins
            self.total_losses = losses
            
            # アクション比率計算
            new_actions = [actions[i] - self.total_actions[i] for i in range(3)]
            self.total_actions = actions
            total_actions = sum(new_actions)
            
            total = new_wins + new_losses
            if total > 0:
                win_rate = new_wins / total * 100
                if win_rate > self.best_win_rate:
                    self.best_win_rate = win_rate
                
                # アクション比率文字列
                if total_actions > 0:
                    stay_pct = new_actions[0] / total_actions * 100
                    buy_pct = new_actions[1] / total_actions * 100
                    short_pct = new_actions[2] / total_actions * 100
                    action_str = f"STAY:{stay_pct:.0f}% BUY:{buy_pct:.0f}% SHORT:{short_pct:.0f}%"
                else:
                    action_str = ""
                
                print(f"   Step {self.n_calls:,}: "
                      f"勝率 {win_rate:.1f}% ({new_wins}W-{new_losses}L) | "
                      f"最高 {self.best_win_rate:.1f}% | {action_str}")
        return True


# ==========================================
# データ読み込み
# ==========================================
def load_all_data():
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not csv_files:
        print("❌ データファイルが見つかりません")
        print("   → data/merged_XXXX.csv を配置してください")
        return [], []
    
    print(f"🔍 検出: {len(csv_files)} ファイル")
    
    all_dfs = []
    all_codes = []
    total_rows = 0
    skipped = 0
    
    for csv_file in csv_files:
        try:
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            
            df = pd.read_csv(csv_file)
            df.columns = [str(c).lower().strip() for c in df.columns]
            
            # カラム名の揺らぎ吸収
            if 'volume' not in df.columns:
                for alias in ['trading_volume', 'vol']:
                    if alias in df.columns:
                        df.rename(columns={alias: 'volume'}, inplace=True)
                        break
                else:
                    df['volume'] = 0
            
            if 'close' not in df.columns:
                for alias in ['current', 'price', 'current_price', 'currentprice']:
                    if alias in df.columns:
                        df.rename(columns={alias: 'close'}, inplace=True)
                        break
            
            if 'close' not in df.columns:
                skipped += 1
                continue
            
            df = df[df['close'] > 0].reset_index(drop=True)
            if len(df) < MIN_DATA_ROWS:
                skipped += 1
                continue
            
            all_dfs.append(df)
            all_codes.append(stock_code)
            total_rows += len(df)
                
        except Exception as e:
            print(f"   ⚠️ {csv_file}: {e}")
            skipped += 1
            continue
    
    if len(all_dfs) == 0:
        print("⚠️ 有効なデータが0件です")
        return [], []
    
    print(f"📊 ロード完了: {len(all_dfs)}銘柄 / {total_rows:,}行")
    if skipped > 0:
        print(f"   （スキップ: {skipped}件）")
    
    return all_dfs, all_codes


# ==========================================
# メイン
# ==========================================
def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    print("=" * 60)
    print(f"🌐 汎用AIモデル学習 v20.6")
    print(f"   Model: {MODEL_NAME}")
    print(f"   Steps: {TOTAL_TIMESTEPS:,}")
    print("=" * 60)
    
    # データ読み込み
    all_dfs, all_codes = load_all_data()
    if not all_dfs:
        return
    
    # 環境構築
    def make_env():
        return UniversalTradingEnv(all_dfs, all_codes)
    
    model_path = os.path.join(MODEL_DIR, MODEL_NAME)
    env_path = f"{model_path}_env.pkl"
    
    model = None
    env = None
    
    # ==========================================
    # モデルファイル有無で自動判定
    # ==========================================
    if os.path.exists(f"{model_path}.zip"):
        print(f"\n📂 既存モデル発見: {model_path}.zip")
        print("   → 継続学習します")
        
        env = DummyVecEnv([make_env for _ in range(NUM_ENVS)])
        if os.path.exists(env_path):
            try:
                env = VecNormalize.load(env_path, env)
                print("   ✅ 正規化統計ロード成功")
            except:
                env = VecNormalize(env, norm_obs=True, norm_reward=False, clip_obs=10.)
        else:
            env = VecNormalize(env, norm_obs=True, norm_reward=False, clip_obs=10.)
        
        try:
            model = PPO.load(model_path, env=env, device='auto')
            print("   ✅ モデルロード成功")
        except Exception as e:
            print(f"   ⚠️ ロード失敗: {e}")
            print("   → 新規作成に切り替え")
            model = None
            env = None
    else:
        print(f"\n📂 モデルファイルなし → 新規作成")
    
    # 新規作成
    if model is None:
        print(f"\n✨ 新規モデル作成")
        
        env = DummyVecEnv([make_env for _ in range(NUM_ENVS)])
        env = VecNormalize(env, norm_obs=True, norm_reward=False, clip_obs=10.)
        
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
            policy_kwargs=dict(net_arch=[dict(pi=[256, 256], vf=[256, 256])])
        )
    
    # 学習開始
    print(f"\n{'='*60}")
    print(f"🚀 学習開始")
    print(f"   ステップ数: {TOTAL_TIMESTEPS:,}")
    print(f"{'='*60}")
    
    callback = WinRateCallback(check_freq=20000)
    
    try:
        model.learn(
            total_timesteps=TOTAL_TIMESTEPS,
            callback=callback,
            progress_bar=True,
            reset_num_timesteps=False
        )
        
        # 完了時のみ保存
        model.save(model_path)
        model.get_env().save(env_path)
        
        print(f"\n🎉 学習完了!")
        print(f"   → {model_path}.zip")
        print(f"   → {env_path}")
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 中断検知 → 保存せずに終了")


if __name__ == "__main__":
    torch.set_num_threads(1)
    train()