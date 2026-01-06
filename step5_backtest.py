# -*- coding: utf-8 -*-
import os
import glob
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from stable_baselines3 import PPO
# ★追加: 正規化ロード用ライブラリ
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from step2_env import TradingEnv
import warnings

# 警告を無視
warnings.filterwarnings("ignore")

# 設定
DATA_DIR = "data"
MODEL_DIR = "model"
RESULT_DIR = "backtest_results"
VERSION = "v7" 
UNIT_SHARES = 100  # 1単元

def backtest():
    os.makedirs(RESULT_DIR, exist_ok=True)
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    
    if not csv_files:
        print("❌ データなし")
        return

    print(f"🚀 卒業試験 (v7 Audit版 + 正規化対応) - 1単元({UNIT_SHARES}株)換算")
    print("="*70)
    print(f"{'CODE':<6} | {'判定':<6} | {'最終損益(円)':>12} | {'取引回数':>8} | {'1回平均'}")
    print("-" * 70)

    total_profit_all = 0

    for csv_file in csv_files:
        try:
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            df = pd.read_csv(csv_file)
            
            if len(df) < 50: continue

            model_path = os.path.join(MODEL_DIR, f"model_{stock_code}_{VERSION}.zip")
            env_stats_path = os.path.join(MODEL_DIR, f"model_{stock_code}_{VERSION}_env.pkl")

            if not os.path.exists(model_path): continue

            # ★重要: 環境の再現 (正規化情報のロード)
            # 1. まず生の環境を作る
            raw_env = TradingEnv(df, stock_code=stock_code)
            # 2. ベクトル化 (VecNormalizeを使うための必須ラッパー)
            env = DummyVecEnv([lambda: raw_env])

            # 3. 学習時の「平均・分散」ルールをロードして適用する
            if os.path.exists(env_stats_path):
                # 統計情報をロード
                env = VecNormalize.load(env_stats_path, env)
                # ★超重要: テスト時は「学習(アップデート)」を止める
                env.training = False 
                # 報酬の正規化はOFFにする（生の金額が見たいから）
                env.norm_reward = False
            else:
                # 統計ファイルがない場合はそのまま (古いモデル互換)
                pass

            # モデルロード
            model = PPO.load(model_path, device='cpu')
            
            # テスト実行
            # VecEnvを使うと obs の形が変わるが、model.predict は自動対応してくれる
            obs = env.reset()
            done = False
            history = []
            
            # DummyVecEnvは「複数の環境」を扱える仕様なので、
            # 返り値がリスト形式([True], [False]など)になる点に注意が必要
            
            while True:
                action, _ = model.predict(obs, deterministic=True)
                obs, reward, done, info = env.step(action)
                
                # VecEnvの info はリスト形式で返ってくるので [0] を取る
                info_dict = info[0]
                
                # 資産推移記録
                history.append(info_dict['balance'] * UNIT_SHARES)
                
                # done も配列で返ってくる
                if done[0]:
                    break

            # 集計
            final_balance_raw = info_dict['balance']
            final_profit_jpy = final_balance_raw * UNIT_SHARES
            trade_count = info_dict['trades']
            
            total_profit_all += final_profit_jpy
            
            avg_profit = (final_profit_jpy / trade_count) if trade_count > 0 else 0
            
            judge = "✅合格" if final_profit_jpy > 0 else ("💀赤字" if trade_count > 0 else "💤見送")
            print(f"{stock_code:<6} | {judge:<6} | {final_profit_jpy:+12,.0f} | {trade_count:>8} | {avg_profit:+5.0f}円")

            # グラフ保存
            plt.figure(figsize=(10, 5))
            plt.plot(history, label="Profit (100 shares)", color="blue")
            plt.title(f"Backtest: {stock_code} ({VERSION})")
            plt.xlabel("Time")
            plt.ylabel("JPY")
            plt.axhline(0, color='red', linestyle='--', linewidth=1)
            plt.grid(True)
            plt.savefig(os.path.join(RESULT_DIR, f"result_{stock_code}.png"))
            plt.close()

        except Exception as e:
            # print(f"Error {stock_code}: {e}") # デバッグ用
            continue

    print("="*70)
    print(f"💰 全銘柄トータル損益: {total_profit_all:+,.0f} 円")
    print(f"🎉 テスト完了。グラフは {RESULT_DIR} を確認してください。")

if __name__ == "__main__":
    backtest()