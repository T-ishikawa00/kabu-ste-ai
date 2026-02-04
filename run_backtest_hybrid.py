# -*- coding: utf-8 -*-
"""
run_backtest_hybrid.py v20.16 (Hybrid Logic)
============================================
【機能】
実戦と同じ「ハイブリッドロジック」でバックテストを行います。
1. その銘柄専用の `model_individual_{code}` があればそれを優先使用。
2. なければ `model_universal_v20` を使用。
3. これにより、個別学習の成果を正確に測定します。
"""

import os
import glob
import pandas as pd
import numpy as np
import random
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
UNIVERSAL_MODEL_NAME = "Master_AI_v2"
RESULT_DIR = "backtest_results"
UNIT_SHARES = 100  # 1単元
TEST_LIMIT = 50    # テスト銘柄数上限

def load_data_files():
    files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    if not files:
        print("❌ データファイルがありません")
        return []
    
    # ターゲット銘柄（個別モデルがあるもの）を優先的にリストアップ
    priority_files = []
    other_files = []
    
    for f in files:
        code = os.path.basename(f).replace("merged_", "").replace(".csv", "")
        if os.path.exists(os.path.join(MODEL_DIR, f"model_individual_{code}.zip")):
            priority_files.append(f)
        else:
            other_files.append(f)
            
    # 優先ファイルを先頭に、残りをランダムに
    if len(other_files) > (TEST_LIMIT - len(priority_files)):
        random.shuffle(other_files)
        other_files = other_files[:(TEST_LIMIT - len(priority_files))]
        
    return priority_files + other_files

def run():
    print("=" * 60)
    print("🚀 ハイブリッドAI バックテスト (Individual + Universal)")
    print("=" * 60)

    # 汎用モデルの確認
    univ_path = os.path.join(MODEL_DIR, f"{UNIVERSAL_MODEL_NAME}.zip")
    univ_model = None
    
    if os.path.exists(univ_path):
        try:
            univ_model = PPO.load(univ_path, device='cpu')
            print(f"✅ 汎用モデルロード: {UNIVERSAL_MODEL_NAME}")
        except:
            print("⚠️ 汎用モデルのロードに失敗")
    else:
        print("⚠️ 汎用モデルが見つかりません")

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
            
            if 'close' not in df.columns or len(df) < 100: continue

            # テストデータ (全期間)
            test_df = df.copy().reset_index(drop=True)
            
            # --- モデル選択ロジック ---
            model = None
            model_type = "Univ"
            
            # 1. 個別モデルを探す
            indiv_path = os.path.join(MODEL_DIR, f"model_individual_{code}.zip")
            indiv_env_path = os.path.join(MODEL_DIR, f"model_individual_{code}_env.pkl")
            
            if os.path.exists(indiv_path):
                try:
                    # 個別環境設定をロード
                    dummy_env = DummyVecEnv([lambda: TradingEnv(test_df, stock_code=code)])
                    if os.path.exists(indiv_env_path):
                        env = VecNormalize.load(indiv_env_path, dummy_env)
                        env.training = False
                        env.norm_reward = False
                    else:
                        env = VecNormalize(dummy_env, norm_obs=True, norm_reward=False, clip_obs=10.)
                        
                    model = PPO.load(indiv_path, device='cpu')
                    model_type = "Indiv"
                except Exception as e:
                    # print(f"Load Error {code}: {e}")
                    pass
            
            # 2. なければ汎用モデルを使う
            if model is None and univ_model is not None:
                # 汎用環境設定（簡易）
                # ※厳密には汎用の統計を使うべきだが、個別銘柄ごとに新規Normalizerで代用可
                dummy_env = DummyVecEnv([lambda: TradingEnv(test_df, stock_code=code)])
                env = VecNormalize(dummy_env, norm_obs=True, norm_reward=False, clip_obs=10.)
                model = univ_model
                model_type = "Univ"
            
            if model is None:
                continue

            # 環境にデータをセット
            raw_env = TradingEnv(test_df, stock_code=code)
            env.venv.envs[0] = raw_env
            
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
                'type': model_type,
                'win_rate': win_rate,
                'profit': profit,
                'trades': trades
            })
            
            icon = "💰" if profit > 0 else ("💸" if profit < 0 else "➖")
            type_icon = "👤" if model_type == "Indiv" else "🌐"
            print(f"[{i+1}/{len(csv_files)}] {code} {type_icon}: {icon} {profit:+,.0f}円 (勝率 {win_rate:.1f}%)")

        except Exception as e:
            # print(f"⚠️ Error {code}: {e}")
            pass

    if not results:
        print("\n❌ テスト結果なし")
        return

    results.sort(key=lambda x: x['profit'], reverse=True)
    
    print("\n" + "="*65)
    print(f"{'CODE':<8} {'TYPE':<6} {'勝率':>8} {'取引数':>8} {'損益(円)':>15}")
    print("-" * 65)
    
    total_profit = 0
    for r in results:
        judge = "🔥" if r['profit'] > 0 and r['win_rate'] >= 50 else ""
        print(f"{r['code']:<8} {r['type']:<6} {r['win_rate']:>7.1f}% {r['trades']:>8} {r['profit']:>+15,.0f} {judge}")
        total_profit += r['profit']
        
    print("-" * 65)
    print(f"💰 総損益: {total_profit:+,.0f} 円")
    print("="*65)

    os.makedirs(RESULT_DIR, exist_ok=True)
    pd.DataFrame(results).to_csv(os.path.join(RESULT_DIR, "backtest_result_hybrid.csv"), index=False)

if __name__ == "__main__":
    run()