# -*- coding: utf-8 -*-
"""
train_universal.py v1.0
=======================
全銘柄のデータを統合して「汎用AIモデル」を学習する

特徴:
- 銘柄に依存しない普遍的なパターンを学習
- 初見の銘柄でもAI取引が可能になる
- データ量が増えるため過学習しにくい
"""

import os
import glob
import pandas as pd
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback
from stable_baselines3.common.utils import set_random_seed
from step2_env import TradingEnv
import warnings

warnings.filterwarnings("ignore")

# ==========================================
# 設定
# ==========================================
DATA_DIR = "data"
MODEL_DIR = "model"
MODEL_NAME = "model_universal_v1"
VERSION = "v1"

# ハイパーパラメータ（汎用モデル用に調整）
LEARNING_RATE = 0.0001      # 学習率（データ量が多いので小さめ）
N_STEPS = 4096              # 1回の更新に使うステップ数
BATCH_SIZE = 256            # ミニバッチサイズ（大きめ）
N_EPOCHS = 4                # 1更新あたりのエポック数
GAMMA = 0.85                # 割引率
ENT_COEF = 0.02             # エントロピー係数（探索を促進）
GAE_LAMBDA = 0.95           # GAEのλ
CLIP_RANGE = 0.2            # PPOのクリップ範囲

# 学習設定
TOTAL_TIMESTEPS = 1_000_000  # 総学習ステップ
MIN_DATA_ROWS = 200          # 最低データ行数
NUM_ENVS = 4                 # 並列環境数


class UniversalTradingEnv(TradingEnv):
    """
    汎用学習用の環境
    - 複数銘柄のデータをランダムに切り替える
    """
    
    def __init__(self, all_dataframes: list, stock_codes: list):
        """
        Args:
            all_dataframes: 各銘柄のDataFrameリスト
            stock_codes: 銘柄コードのリスト
        """
        self.all_dfs = all_dataframes
        self.all_codes = stock_codes
        self.current_df_idx = 0
        
        # 最初の銘柄で初期化
        super().__init__(all_dataframes[0], stock_code="UNIVERSAL")
        
    def reset(self, seed=None, options=None):
        """リセット時にランダムな銘柄を選択"""
        if seed is not None:
            np.random.seed(seed)
        
        # ランダムに銘柄を選択
        self.current_df_idx = np.random.randint(0, len(self.all_dfs))
        self.df = self.all_dfs[self.current_df_idx].reset_index(drop=True)
        self.stock_code = self.all_codes[self.current_df_idx]
        self.MAX_STEPS = len(self.df) - 1
        
        # 親クラスのreset処理
        return super().reset(seed=seed, options=options)


class WinRateCallback(BaseCallback):
    """学習中の勝率をモニタリング"""
    
    def __init__(self, check_freq=10000, verbose=1):
        super().__init__(verbose)
        self.check_freq = check_freq
        self.best_win_rate = 0.0
        self.win_rates = []
        self.total_wins = 0
        self.total_losses = 0
        
    def _on_step(self):
        if self.n_calls % self.check_freq == 0:
            # 全環境から勝率情報を収集
            wins = 0
            losses = 0
            
            for env in self.training_env.envs:
                if hasattr(env, 'episode_wins'):
                    wins += env.episode_wins
                    losses += env.episode_losses
            
            # 前回からの増分
            new_wins = wins - self.total_wins
            new_losses = losses - self.total_losses
            self.total_wins = wins
            self.total_losses = losses
            
            total = new_wins + new_losses
            if total > 0:
                win_rate = new_wins / total * 100
                self.win_rates.append(win_rate)
                
                if win_rate > self.best_win_rate:
                    self.best_win_rate = win_rate
                
                print(f"  Step {self.n_calls:,}: "
                      f"勝率 {win_rate:.1f}% ({new_wins}勝/{new_losses}敗) | "
                      f"累計 {wins}勝/{losses}敗 | "
                      f"最高 {self.best_win_rate:.1f}%")
        
        return True


def load_all_data():
    """全銘柄のデータを読み込む"""
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    
    if not csv_files:
        print("❌ データファイルが見つかりません")
        print("   先に step4_process_daily_log.py を実行してください")
        return [], []
    
    all_dfs = []
    all_codes = []
    total_rows = 0
    
    print(f"📂 データ読み込み中...")
    
    for csv_file in sorted(csv_files):
        try:
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            
            df = pd.read_csv(csv_file)
            
            # データ量チェック
            if len(df) < MIN_DATA_ROWS:
                print(f"  ⚠️ {stock_code}: データ不足 ({len(df)}行) → スキップ")
                continue
            
            all_dfs.append(df)
            all_codes.append(stock_code)
            total_rows += len(df)
            print(f"  ✅ {stock_code}: {len(df):,}行")
            
        except Exception as e:
            print(f"  ❌ {csv_file}: {e}")
            continue
    
    print(f"\n📊 統計:")
    print(f"   銘柄数: {len(all_dfs)}")
    print(f"   総行数: {total_rows:,}")
    print(f"   平均行数: {total_rows // max(1, len(all_dfs)):,}")
    
    return all_dfs, all_codes


def make_env(all_dfs, all_codes, rank=0):
    """環境作成関数"""
    def _init():
        env = UniversalTradingEnv(all_dfs, all_codes)
        return env
    return _init


def train():
    """メイン学習処理"""
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    print("=" * 70)
    print("🌐 汎用AIモデル学習 v1.0")
    print("=" * 70)
    
    # データ読み込み
    all_dfs, all_codes = load_all_data()
    
    if not all_dfs:
        print("❌ 学習可能なデータがありません")
        return
    
    print(f"\n📋 学習パラメータ:")
    print(f"   Learning Rate: {LEARNING_RATE}")
    print(f"   Batch Size: {BATCH_SIZE}")
    print(f"   N Steps: {N_STEPS}")
    print(f"   Gamma: {GAMMA}")
    print(f"   Entropy Coef: {ENT_COEF}")
    print(f"   Total Timesteps: {TOTAL_TIMESTEPS:,}")
    print(f"   Parallel Envs: {NUM_ENVS}")
    
    # 並列環境作成
    print(f"\n🔧 環境構築中...")
    env_fns = [make_env(all_dfs, all_codes, i) for i in range(NUM_ENVS)]
    env = DummyVecEnv(env_fns)  # SubprocVecEnvは重いのでDummyで
    env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.)
    
    # モデル準備
    model_path = os.path.join(MODEL_DIR, MODEL_NAME)
    
    if os.path.exists(f"{model_path}.zip"):
        print(f"\n🔄 既存モデルをロードして継続学習")
        model = PPO.load(model_path, env=env, device='cpu')
        model.learning_rate = LEARNING_RATE
    else:
        print(f"\n✨ 新規モデルを作成")
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
            verbose=0,
            device='cpu',
            tensorboard_log="./tb_logs/"
        )
    
    # コールバック
    callback = WinRateCallback(check_freq=20000)
    
    # 学習開始
    print(f"\n{'─'*70}")
    print(f"🚀 学習開始!")
    print(f"{'─'*70}")
    
    try:
        model.learn(
            total_timesteps=TOTAL_TIMESTEPS,
            callback=callback,
            progress_bar=True
        )
    except KeyboardInterrupt:
        print("\n⚠️ 学習を中断しました")
    
    # 保存
    print(f"\n💾 モデル保存中...")
    model.save(model_path)
    env.save(f"{model_path}_env.pkl")
    
    # 結果サマリー
    print(f"\n{'='*70}")
    print(f"🎉 学習完了!")
    print(f"{'='*70}")
    print(f"   モデル: {model_path}.zip")
    print(f"   環境統計: {model_path}_env.pkl")
    
    if callback.win_rates:
        avg_win_rate = np.mean(callback.win_rates[-10:])  # 最後の10回の平均
        print(f"   最終勝率: {avg_win_rate:.1f}%")
        print(f"   最高勝率: {callback.best_win_rate:.1f}%")
    
    print(f"\n📝 次のステップ:")
    print(f"   1. controller.py で汎用モデルを使用するよう設定")
    print(f"   2. step5_backtest.py でテスト")
    print(f"{'='*70}")


if __name__ == "__main__":
    train()