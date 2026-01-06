# -*- coding: utf-8 -*-
import os
import glob
import pandas as pd
from stable_baselines3 import PPO
# ★追加: 正規化に必要なライブラリ
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from step2_env import TradingEnv  # v7.0 (Audit対応) の環境
import warnings

# 警告を無視
warnings.filterwarnings("ignore")

# 設定
DATA_DIR = "data"
MODEL_DIR = "model"
VERSION = "v7"  # ★重要: v6 -> v7 に変更 (心機一転)

def train():
    # step4で作った merged_xxxx.csv を全部自動で拾う
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    
    if not csv_files:
        print("❌ データファイルが見つかりません。先に step4_process_daily_log.py を実行してください。")
        return

    print(f"🔍 {len(csv_files)} 個の銘柄データを検出しました。")
    print(f"🚀 新ルール(v7: 利益追求型 + 正規化)での学習を開始します...")

    for csv_file in csv_files:
        try:
            # 1. ファイル名から銘柄コードを抽出
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            
            # 2. データを読み込む
            df = pd.read_csv(csv_file)
            
            if len(df) < 50:
                print(f"⚠️ データ不足のためスキップ: {stock_code} ({len(df)}行)")
                continue

            print(f"\n📈 学習開始: {stock_code}")
            
            # 3. 環境を作る (v7環境 + 正規化)
            # ★ここが重要変更ポイント
            raw_env = TradingEnv(df, stock_code=stock_code)
            env = DummyVecEnv([lambda: raw_env]) # ベクトル化
            # 正規化ラッパー: 株価の桁違いを吸収し、AIの偏差値を上げる
            env = VecNormalize(env, norm_obs=True, norm_reward=True, clip_obs=10.)
            
            # 4. モデルの準備
            model_name = f"model_{stock_code}_{VERSION}"
            model_path = os.path.join(MODEL_DIR, model_name)
            
            # ★重要: 今回はルールが激変したので、基本的に「新規作成」を推奨
            if os.path.exists(f"{model_path}.zip"):
                print(f" 🔄 既存のv7モデルをロードして続きから学習: {model_name}")
                # 既存モデルがある場合は、環境の統計情報もロードするのが理想だが
                # ここでは簡易的にロードして上書き学習する
                model = PPO.load(model_path, env=env, device='cpu')
            else:
                print(f" ✨ 新規モデルを作成 (スパルタ教育開始): {model_name}")
                # ent_coef=0.01: 少し探検させて、新しい利益の出し方を見つけさせる
                model = PPO("MlpPolicy", env, verbose=0, device='cpu', ent_coef=0.01)
            
            # 5. 学習実行
            # 難易度が上がったので、練習量(steps)を増やす
            # データ行数 x 20回 (最低 50,000回)
            total_steps = max(len(df) * 20, 50000)
            
            model.learn(total_timesteps=total_steps)
            
            # 6. 保存
            model.save(model_path)
            # ★重要: 正規化の統計情報(平均・分散)も保存する
            # これがないと、本番で「今の株価が高いか低いか」が分からなくなる
            env.save(os.path.join(MODEL_DIR, f"{model_name}_env.pkl"))
            
            print(f" ✅ 学習完了・保存: {model_name} (+Env stats)")

        except Exception as e:
            print(f" ⚠️ エラー発生 ({stock_code}): {e}")
            import traceback
            traceback.print_exc()
            continue

    print("\n🎉 全銘柄の学習(v7)が終了しました！")
    print("👉 次は step5_backtest.py を実行して、実力を確認してください。")

if __name__ == "__main__":
    # フォルダがなければ作る
    os.makedirs(MODEL_DIR, exist_ok=True)
    train()