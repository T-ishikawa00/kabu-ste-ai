# -*- coding: utf-8 -*-
"""
run_backtest.py v20.12 (Universal Model Only)
=============================================
【機能】
1. 最新の汎用モデル (runtime/model_universal_v20) をロード。
2. dataフォルダ内の全データに対してバックテストを実行。
3. 結果をランキング形式で表示。
"""

import os
import glob
import pandas as pd
import numpy as np
import warnings
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

# ★最新の環境ファイルをインポート
try:
    from step2_env_universal import TradingEnv
except ImportError:
    raise ImportError("❌ 'step2_env_universal.py' が見つかりません。")

warnings.filterwarnings("ignore")

# 設定
DATA_DIR = "data"
MODEL_DIR = "runtime"
MODEL_NAME = "model_universal_v20"
RESULT_DIR = "backtest_results"
UNIT_SHARES = 100  # 1単元

def load_data_files():
    """dataフォルダのCSVを全取得"""
    files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not files:
        print("❌ データファイルがありません")
        return []
    return files

def run():
    print("=" * 60)
    print("🚀 最新汎用モデル バックテスト")
    print("=" * 60)

    # モデルパス確認
    model_path = os.path.join(MODEL_DIR, f"{MODEL_NAME}.zip")
    env_path = os.path.join(MODEL_DIR, f"{MODEL_NAME}_env.pkl")

    if not os.path.exists(model_path):
        print(f"❌ モデルが見つかりません: {model_path}")
        print("   → 先に学習 (train_universal.py) を実行してください")
        return

    # モデルロード
    print(f"📂 モデルロード: {MODEL_NAME}")
    try:
        # ダミー環境作成（ロード用）
        dummy_df = pd.DataFrame({
            'close': [100]*100, 'volume': [100]*100,
            'high': [100]*100, 'low': [100]*100, 'open': [100]*100
        })
        dummy_env = DummyVecEnv([lambda: TradingEnv(dummy_df, "DUMMY")])
        
        # 正規化統計の復元
        if os.path.exists(env_path):
            env = VecNormalize.load(env_path, dummy_env)
            env.training = False  # テストモード（統計更新しない）
            env.norm_reward = False
            print("   ✅ 正規化統計を適用しました")
        else:
            env = VecNormalize(dummy_env, norm_obs=True, norm_reward=False, clip_obs=10.)
            print("   ⚠️ 正規化ファイルなし（初期状態で実行）")

        model = PPO.load(model_path, device='auto')
        print("   ✅ モデルロード完了")
    
    except Exception as e:
        print(f"❌ ロード失敗: {e}")
        return

    # バックテスト実行
    csv_files = load_data_files()
    results = []

    print(f"\n📊 テスト対象: {len(csv_files)} 銘柄")
    print("-" * 60)

    for i, csv_file in enumerate(csv_files):
        try:
            filename = os.path.basename(csv_file)
            code = filename.replace("merged_", "").replace(".csv", "")
            
            # データ読み込み
            df = pd.read_csv(csv_file)
            df.columns = [c.lower() for c in df.columns]
            
            # カラム名補正
            if 'close' not in df.columns:
                for alias in ['current', 'price']:
                    if alias in df.columns: df.rename(columns={alias: 'close'}, inplace=True); break
            
            if 'close' not in df.columns or len(df) < 100:
                continue

            # テストデータ作成 (全期間または直近20%)
            # ※今回は学習データと同じデータを使うことになりますが、動作確認としてはOKです
            test_df = df.copy().reset_index(drop=True)
            
            # 環境再構築
            raw_env = TradingEnv(test_df, stock_code=code)
            env.venv.envs[0] = raw_env # 内部環境を差し替え
            
            # 実行
            obs = env.reset()
            done = False
            
            while not done:
                action, _ = model.predict(obs, deterministic=True)
                obs, reward, done_arr, info = env.step(action)
                done = done_arr[0]
                info_d = info[0]

            # 結果集計
            wins = info_d.get('wins', 0)
            losses = info_d.get('losses', 0)
            trades = wins + losses
            profit = info_d['balance'] * UNIT_SHARES
            win_rate = (wins / trades * 100) if trades > 0 else 0

            results.append({
                'code': code,
                'win_rate': win_rate,
                'profit': profit,
                'trades': trades
            })
            
            icon = "💰" if profit > 0 else ("💸" if profit < 0 else "➖")
            print(f"[{i+1}/{len(csv_files)}] {code}: {icon} {profit:+,.0f}円 (勝率 {win_rate:.1f}% / {trades}回)")

        except Exception as e:
            print(f"⚠️ Error {code}: {e}")

    # 最終結果表示
    if not results:
        print("\n❌ テスト結果なし")
        return

    results.sort(key=lambda x: x['profit'], reverse=True)
    
    print("\n" + "="*60)
    print(f"{'CODE':<8} {'勝率':>8} {'取引数':>8} {'損益(円)':>15} {'判定':>6}")
    print("-" * 60)
    
    total_profit = 0
    for r in results:
        judge = "🔥" if r['profit'] > 0 and r['win_rate'] >= 50 else ""
        print(f"{r['code']:<8} {r['win_rate']:>7.1f}% {r['trades']:>8} {r['profit']:>+15,.0f} {judge:>6}")
        total_profit += r['profit']
        
    print("-" * 60)
    print(f"💰 総損益: {total_profit:+,.0f} 円")
    print("="*60)

    # CSV保存
    os.makedirs(RESULT_DIR, exist_ok=True)
    pd.DataFrame(results).to_csv(os.path.join(RESULT_DIR, "backtest_result_v20.csv"), index=False)
    print(f"💾 結果を保存しました: {RESULT_DIR}/backtest_result_v20.csv")

if __name__ == "__main__":
    run()