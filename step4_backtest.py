# -*- coding: utf-8 -*-
import os
import matplotlib.pyplot as plt
import pandas as pd
from stable_baselines3 import PPO
from step2_env import TradingEnv

# ★重要: step3で作った「データ加工機能」を再利用する
# これにより、学習時と全く同じ指標(MACD, OBI等)でテストできる
try:
    from step3_train import load_and_process_data
except ImportError:
    print("❌ step3_train.py が見つかりません。同じフォルダにありますか？")
    exit()

def backtest(code):
    print("="*60)
    print(f"🧪 AIバックテスト開始 (卒業試験): {code}")
    print("="*60)

    # 1. データの読み込み & 加工 (Step3の機能を借用)
    # これで MACD, BB, OBI などが入った状態のデータが手に入る
    df = load_and_process_data(code)
    
    if df is None:
        print("❌ データがないためテストを中止します。")
        return

    print(f"📚 テストデータ: {len(df)} 行")

    # 2. 環境の作成 (v5対応)
    env = TradingEnv(df)
    
    # 3. 学習済みモデルのロード
    model_name = f"model_{code}_v5" # ★ v5に変更
    model_path = f"{model_name}.zip"
    
    if not os.path.exists(model_path):
        print(f"❌ {model_path} が見つかりません。先にStep3で学習してください。")
        return

    try:
        model = PPO.load(model_path)
        print(f"🤖 脳みそロード成功: {model_name}")
    except Exception as e:
        print(f"❌ モデル読み込みエラー: {e}")
        return

    # 4. シミュレーション実行
    obs, _ = env.reset()
    done = False
    
    print("🏃‍♂️ トレードシミュレーション中...")
    
    history = []
    actions = []
    
    while not done:
        # AIに「次どうする？」と聞く (真剣勝負)
        action, _states = model.predict(obs, deterministic=True)
        
        # 行動を実行
        obs, reward, done, truncated, info = env.step(action)
        
        # 資産状況を記録
        history.append(info['balance'])
        actions.append(action)

    # 5. 結果発表
    final_balance = info['balance']
    trade_count = info['trades']
    
    print("-" * 60)
    print(f"💰 最終損益: {final_balance:+.1f} 円")
    print(f"🔢 取引回数: {trade_count} 回")
    
    # 勝率などの簡易分析
    buy_count = actions.count(1)
    exit_count = actions.count(2)
    print(f"📊 内訳: BUY {buy_count}回 / EXIT {exit_count}回")

    if final_balance > 0:
        print("🏆 合格！ 利益が出ています！")
    else:
        print("💀 不合格... 赤字です。戦略かデータの見直しが必要です。")
    print("-" * 60)

    # 6. グラフ表示
    plt.figure(figsize=(12, 6))
    plt.plot(history, label="AI Balance (JPY)", color="blue")
    plt.title(f"Backtest Result: {code} (Model v5)")
    plt.xlabel("Time Steps (Minutes)")
    plt.ylabel("Cumulative Profit (JPY)")
    plt.axhline(0, color='red', linestyle='--', linewidth=1)
    
    # 最後に取引終了した地点にマーカー
    plt.scatter(len(history)-1, history[-1], color="red" if final_balance < 0 else "green", s=100)
    
    plt.legend()
    plt.grid(True)
    
    # 画像保存
    img_name = f"result_{code}.png"
    plt.savefig(img_name)
    print(f"📊 グラフを '{img_name}' に保存しました。")
    
    # 画面表示 (環境によっては出ない場合もあるので保存を優先)
    # plt.show()

if __name__ == "__main__":
    # テストしたい銘柄のリスト
    # データが溜まっているもの＆モデルを作ったものを指定してください
    target_codes = [
        "6146", "9501", "9509", "8031", "6758", "6954", "9107", "8411",
        "9984", "8058", "7014", "8306", "6920", "7203", "7003", "5803"
        "7267", "6526", "7974", "7011", "6506", "285A"
    ]
    
    print(f"🚀 全 {len(target_codes)} 銘柄の卒業試験を開始します！")
    
    for code in target_codes:
        backtest(code)
        
    print("🎉 すべてのテストが完了しました！")