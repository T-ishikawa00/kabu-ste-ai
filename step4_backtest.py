# -*- coding: utf-8 -*-
import pandas as pd
import matplotlib.pyplot as plt
from stable_baselines3 import PPO
from step2_env import TradingEnv

def backtest():
    print("="*60)
    print("🧪 AIバックテスト開始 (卒業試験)")
    print("="*60)

    # 1. データの読み込み
    df = pd.read_csv("training_data.csv")
    print(f"📚 テストデータ: {len(df)} 行 (北電 9509)")

    # 2. 環境の作成
    env = TradingEnv(df)
    
    # 3. 学習済みモデルのロード
    model_name = "model_9509_v4"
    try:
        model = PPO.load(model_name)
        print(f"🤖 脳みそロード成功: {model_name}.zip")
    except FileNotFoundError:
        print(f"❌ {model_name}.zip が見つかりません。学習(Step3)を先にしてください。")
        return

    # 4. シミュレーション実行
    obs, _ = env.reset()
    done = False
    
    print("🏃‍♂️ トレードシミュレーション中...")
    
    # 資産推移の記録用
    history = []
    
    while not done:
        # AIに「次どうする？」と聞く (deterministic=True で真剣勝負させる)
        action, _states = model.predict(obs, deterministic=True)
        
        # 行動を実行
        obs, reward, done, truncated, info = env.step(action)
        
        # 資産状況を記録
        history.append(info['balance'])

    # 5. 結果発表
    final_balance = info['balance']
    trade_count = info['trades']
    
    print("-" * 60)
    print(f"💰 最終損益: {final_balance:+.1f} 円 (※スプレッド考慮後)")
    print(f"🔢 取引回数: {trade_count} 回")
    
    if final_balance > 0:
        print("🏆 合格！ 利益が出ています！")
    else:
        print("💀 不合格... 赤字です。報酬設計の見直しが必要です。")
    print("-" * 60)

    # 6. グラフ表示
    plt.figure(figsize=(10, 5))
    plt.plot(history, label="AI Balance (JPY)")
    plt.title(f"Backtest Result: {model_name}")
    plt.xlabel("Time Steps (Minutes)")
    plt.ylabel("Cumulative Profit (JPY)")
    plt.axhline(0, color='red', linestyle='--', linewidth=1)
    plt.legend()
    plt.grid(True)
    
    # 画像として保存
    plt.savefig("backtest_result.png")
    print("📊 資産推移グラフを 'backtest_result.png' に保存しました。確認してください！")
    
    # 画面にも表示
    plt.show()

if __name__ == "__main__":
    backtest()