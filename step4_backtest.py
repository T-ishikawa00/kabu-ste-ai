# -*- coding: utf-8 -*-
import os
import glob
import matplotlib.pyplot as plt
import pandas as pd
from stable_baselines3 import PPO
from step2_env import TradingEnv  # v6.6の環境を読み込む

# 設定
DATA_DIR = "data"           # データがある場所
MODEL_DIR = "model"         # モデルがある場所
RESULT_DIR = "backtest_results" # グラフ保存先

def backtest():
    # グラフ保存用フォルダ作成
    os.makedirs(RESULT_DIR, exist_ok=True)

    # dataフォルダにある merged_xxxx.csv を全部取得
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    
    if not csv_files:
        print("❌ テストデータが見つかりません。step4を実行しましたか？")
        return

    print(f"🚀 全 {len(csv_files)} 銘柄の卒業試験を開始します！")
    print("="*60)

    for csv_file in csv_files:
        try:
            # 1. ファイル名から銘柄コードを抽出
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            
            # 2. データの読み込み
            # (step4ですでにRCIなどの計算は終わっているので読むだけでOK)
            df = pd.read_csv(csv_file)
            
            if len(df) < 50:
                print(f"⚠️ データ不足のためスキップ: {stock_code}")
                continue

            # 3. モデルファイルの確認 (v6モデルを探す)
            model_path = os.path.join(MODEL_DIR, f"model_{stock_code}_v6.zip")
            if not os.path.exists(model_path):
                print(f"⏩ モデルがないためスキップ: {stock_code} (学習していません)")
                continue

            print(f"\n🧪 テスト実行中: {stock_code} ... ", end="")

            # 4. 環境の作成 (★ここで銘柄コードを渡して性格を再現！)
            # 東電ならスキャモード、レーザーならスイングモードでテストされます
            env = TradingEnv(df, stock_code=stock_code)
            
            # 5. モデルのロード
            model = PPO.load(model_path)

            # 6. シミュレーション実行
            obs, _ = env.reset()
            done = False
            
            history = []
            actions = []
            
            while not done:
                # deterministic=True で「真剣モード（確率的な遊びなし）」で予測
                action, _ = model.predict(obs, deterministic=True)
                
                obs, reward, done, truncated, info = env.step(action)
                
                history.append(info['balance'])
                actions.append(action)

            # 7. 結果集計
            final_balance = info['balance']
            trade_count = info['trades']
            buy_count = actions.count(1)
            exit_count = actions.count(2)
            
            print(f"完了 ✅")
            print(f"   💰 最終損益: {final_balance:+.0f} 円")
            print(f"   🔢 取引回数: {trade_count} 回 (BUY: {buy_count} / EXIT: {exit_count})")
            
            # 判定
            if final_balance > 0:
                print("   🏆 合格 (黒字)")
            else:
                print("   💀 不合格 (赤字)")

            # 8. グラフ作成・保存
            plt.figure(figsize=(10, 5))
            plt.plot(history, label="Balance (JPY)", color="blue")
            plt.title(f"Backtest: {stock_code} (Model v6)")
            plt.xlabel("Time Steps")
            plt.ylabel("Profit (JPY)")
            plt.axhline(0, color='red', linestyle='--', linewidth=1)
            
            # 最後の地点にマーカー
            color = "green" if final_balance > 0 else "red"
            plt.scatter(len(history)-1, history[-1], color=color, s=100)
            
            plt.legend()
            plt.grid(True)
            
            # 画像保存
            save_path = os.path.join(RESULT_DIR, f"result_{stock_code}.png")
            plt.savefig(save_path)
            plt.close() # メモリ解放

        except Exception as e:
            print(f"\n❌ エラー発生 ({stock_code}): {e}")
            continue

    print("="*60)
    print(f"🎉 すべてのテストが完了しました！ 結果は '{RESULT_DIR}' フォルダを確認してください。")

if __name__ == "__main__":
    backtest()