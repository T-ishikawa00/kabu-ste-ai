# -*- coding: utf-8 -*-
"""
train_universal.py v22.2 (継続学習対応)
=============================================
【v22.2 変更点】
- ENT_COEF: 0.02 → 0.03 (SHORT偏重緩和)
- RESUME機能追加（既存モデルから継続学習）
"""

import os
import glob
import pandas as pd
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback

from step2_env_universal import TradingEnv
import warnings
warnings.filterwarnings("ignore")

# ==========================================
# ★設定 v22.2
# ==========================================
TOTAL_TIMESTEPS = 500_000    # 学習ステップ数

# ★ 継続学習フラグ
RESUME = True  # True: 既存モデルから継続 / False: 新規学習

# ハイパーパラメータ
LEARNING_RATE = 0.0003
N_STEPS = 2048
BATCH_SIZE = 64
N_EPOCHS = 10
GAMMA = 0.99
ENT_COEF = 0          # 0.02→0.03 (SHORT偏重緩和)
GAE_LAMBDA = 0.95
CLIP_RANGE = 0.2

# パス
DATA_DIR = "data"
MODEL_DIR = "runtime"
MODEL_NAME = "model_universal_v23"
BEST_MODEL_DIR = "runtime/best_model"

# その他
MIN_DATA_ROWS = 100
NUM_ENVS = 4


# ==========================================
# 環境クラス
# ==========================================
class UniversalTradingEnv(TradingEnv):
    """複数銘柄をランダムに切り替える + 累計カウント機能"""
    def __init__(self, all_dataframes: list, stock_codes: list):
        self.all_dfs = all_dataframes
        self.all_codes = stock_codes
        
        self.persistent_action_counts = [0, 0, 0, 0]
        
        super().__init__(all_dataframes[0], stock_code=stock_codes[0])
        
    def reset(self, seed=None, options=None):
        if seed is not None:
            np.random.seed(seed)
        
        idx = np.random.randint(0, len(self.all_dfs))
        self.df = self.all_dfs[idx].copy().reset_index(drop=True)
        self.stock_code = self.all_codes[idx]
        self.MAX_STEPS = len(self.df) - 1
        self.MAX_HOLD = 120
        
        self.trades = []
        return super().reset(seed=seed, options=options)

    def step(self, action):
        self.persistent_action_counts[action] += 1
        return super().step(action)


# ==========================================
# コールバック
# ==========================================
class WinRateCallback(BaseCallback):
    """勝率 + アクション比率モニタリング"""
    def __init__(self, check_freq=20000):
        super().__init__()
        self.check_freq = check_freq
        self.best_win_rate = 0.0
        self.last_actions = [0, 0, 0, 0]
        
    def _get_true_envs(self):
        env = self.training_env
        if hasattr(env, 'venv'):
            env = env.venv
        if hasattr(env, 'envs'):
            return env.envs
        return []

    def _on_step(self):
        if self.n_calls % self.check_freq == 0:
            wins, losses = 0, 0
            current_total_actions = [0, 0, 0, 0]
            
            true_envs = self._get_true_envs()
            
            for env in true_envs:
                if hasattr(env, 'trades'):
                    for pnl in env.trades:
                        if pnl > 0: wins += 1
                        elif pnl < 0: losses += 1
                
                if hasattr(env, 'persistent_action_counts'):
                    for i in range(4):
                        current_total_actions[i] += env.persistent_action_counts[i]
            
            diff_actions = [current_total_actions[i] - self.last_actions[i] for i in range(4)]
            self.last_actions = current_total_actions[:] 
            
            total_diff = sum(diff_actions)
            
            ep_total = wins + losses
            win_rate = (wins / ep_total * 100) if ep_total > 0 else 0.0
            
            if win_rate > self.best_win_rate and ep_total > 10:
                self.best_win_rate = win_rate
                
            if total_diff > 0:
                stay_pct = diff_actions[0] / total_diff * 100
                buy_pct = diff_actions[1] / total_diff * 100
                short_pct = diff_actions[2] / total_diff * 100
                exit_pct = diff_actions[3] / total_diff * 100
                action_str = f"STAY:{stay_pct:.0f}% BUY:{buy_pct:.0f}% SHORT:{short_pct:.0f}% EXIT:{exit_pct:.0f}%"
            else:
                action_str = "Wait..."
                
            print(f"   Step {self.n_calls:,}: "
                  f"勝率 {win_rate:.1f}% ({wins}W-{losses}L) | "
                  f"最高 {self.best_win_rate:.1f}% | {action_str}")
        return True


# ==========================================
# データ読み込み
# ==========================================
def load_all_data():
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not csv_files:
        print("❌ データファイルが見つかりません")
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
            
            if 'volume' not in df.columns:
                for alias in ['trading_volume', 'vol']:
                    if alias in df.columns:
                        df.rename(columns={alias: 'volume'}, inplace=True)
                        break
                else:
                    df['volume'] = 0
            
            if 'close' not in df.columns:
                for alias in ['current', 'price', 'current_price']:
                    if alias in df.columns: df.rename(columns={alias: 'close'}, inplace=True)
            
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
    return all_dfs, all_codes


# ==========================================
# メイン
# ==========================================
def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(BEST_MODEL_DIR, exist_ok=True)
    
    print("=" * 60)
    print(f"🌐 汎用AIモデル学習 v22.2")
    print(f"   Model: {MODEL_NAME}")
    print(f"   Steps: {TOTAL_TIMESTEPS:,}")
    print(f"   LR: {LEARNING_RATE} | ENT: {ENT_COEF}")
    print(f"   RESUME: {RESUME}")
    print("=" * 60)
    
    all_dfs, all_codes = load_all_data()
    if not all_dfs: return
    
    def make_env():
        return UniversalTradingEnv(all_dfs, all_codes)
    
    print("🛠️ 環境構築中...")
    
    # 評価用環境
    eval_env = DummyVecEnv([make_env for _ in range(NUM_ENVS)])
    eval_env = VecNormalize(eval_env, norm_obs=True, norm_reward=True, clip_obs=10., clip_reward=10.)
    
    # 学習用環境
    env = DummyVecEnv([make_env for _ in range(NUM_ENVS)])
    env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10., clip_reward=10.)
    
    # ★ 継続学習 or 新規作成
    model_path = os.path.join(MODEL_DIR, f"{MODEL_NAME}.zip")
    env_path = os.path.join(MODEL_DIR, f"{MODEL_NAME}_env.pkl")
    
    if RESUME and os.path.exists(model_path):
        print(f"\n📂 既存モデルから継続学習: {model_path}")
        
        # 保存済みの正規化統計をロード
        if os.path.exists(env_path):
            try:
                # 方法1: VecNormalize.load()
                env = VecNormalize.load(env_path, env)
                eval_env = VecNormalize.load(env_path, eval_env)
                print(f"   ✓ 正規化統計をロード: {env_path}")
            except Exception as e1:
                print(f"   ⚠️ 標準ロード失敗: {e1}")
                try:
                    # 方法2: cloudpickleで直接ロードして統計を転送
                    import cloudpickle
                    with open(env_path, 'rb') as f:
                        old_env = cloudpickle.load(f)
                    
                    # 正規化統計を転送
                    if hasattr(old_env, 'obs_rms'):
                        env.obs_rms = old_env.obs_rms
                        eval_env.obs_rms = old_env.obs_rms
                    if hasattr(old_env, 'ret_rms'):
                        env.ret_rms = old_env.ret_rms
                        eval_env.ret_rms = old_env.ret_rms
                    print(f"   ✓ cloudpickleで正規化統計を復元")
                except Exception as e2:
                    print(f"   ⚠️ cloudpickleも失敗（新規統計で継続）: {e2}")
        
        # モデルをロード（新しい環境とハイパーパラメータで）
        model = PPO.load(
            model_path, 
            env=env,
            learning_rate=LEARNING_RATE,
            ent_coef=ENT_COEF,  # ★新しいENT_COEFを適用
            tensorboard_log="./tensorboard_logs/",
        )
        print(f"   ✓ モデルをロード（ENT_COEF={ENT_COEF}で継続）")
    else:
        print(f"\n✨ 新規モデル作成 (v22.2)")
        model = PPO(
            "MlpPolicy",
            env,
            verbose=1,
            learning_rate=LEARNING_RATE,
            n_steps=N_STEPS,
            batch_size=BATCH_SIZE,
            n_epochs=N_EPOCHS,
            gamma=GAMMA,
            ent_coef=ENT_COEF,
            gae_lambda=GAE_LAMBDA,
            clip_range=CLIP_RANGE,
            tensorboard_log="./tensorboard_logs/",
            device='auto',
            policy_kwargs=dict(net_arch=[dict(pi=[256, 256], vf=[256, 256])])
        )
    
    # コールバック
    eval_callback = EvalCallback(
        eval_env,
        best_model_save_path=BEST_MODEL_DIR,
        log_path=BEST_MODEL_DIR,
        eval_freq=10000,
        deterministic=True,
        render=False,
        verbose=1
    )
    win_rate_callback = WinRateCallback(check_freq=20000)

    print(f"\n🚀 学習開始")
    
    try:
        model.learn(
            total_timesteps=TOTAL_TIMESTEPS,
            callback=[eval_callback, win_rate_callback],
            progress_bar=True,
            reset_num_timesteps=False if RESUME else True,  # 継続時はステップ数を維持
        )
        
        print(f"\n🎉 学習完了!")
        print(f"   ★最強モデル: {BEST_MODEL_DIR}/best_model.zip")
        
        model.save(os.path.join(MODEL_DIR, MODEL_NAME))
        env.save(os.path.join(MODEL_DIR, f"{MODEL_NAME}_env.pkl"))
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 中断検知 - 現時点のモデルを保存中...")
        model.save(os.path.join(MODEL_DIR, f"{MODEL_NAME}_interrupted"))
        env.save(os.path.join(MODEL_DIR, f"{MODEL_NAME}_interrupted_env.pkl"))
        print(f"   保存完了: {MODEL_NAME}_interrupted")


if __name__ == "__main__":
    torch.set_num_threads(1)
    train()