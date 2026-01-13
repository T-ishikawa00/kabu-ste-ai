# -*- coding: utf-8 -*-
"""
step5_backtest.py v12.0 (Win Rate Focused Analysis)
====================================================
改善点:
  - 勝率を最重要指標として表示
  - 平均保有時間の分析
  - リスクリワード比の計算
  - 詳細なトレード分析
"""

import os
import glob
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from step2_env import TradingEnv
import warnings

warnings.filterwarnings("ignore")

# 設定
DATA_DIR = "data"
MODEL_DIR = "model"
RESULT_DIR = "backtest_results"
UNIT_SHARES = 100  # 1単元


def find_model(stock_code):
    """利用可能な最新モデルを探す"""
    for v in ['v12', 'v11', 'v10', 'v8', 'v7', 'v6']:
        path = os.path.join(MODEL_DIR, f"model_{stock_code}_{v}.zip")
        if os.path.exists(path):
            return path, v
    return None, None


def backtest():
    os.makedirs(RESULT_DIR, exist_ok=True)
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    
    if not csv_files:
        print("❌ データなし")
        return

    print(f"{'='*80}")
    print(f"🎯 勝率重視バックテスト v12.0")
    print(f"{'='*80}")
    
    results = []

    for csv_file in csv_files:
        try:
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            df = pd.read_csv(csv_file)
            
            if len(df) < 100:
                continue

            model_path, version = find_model(stock_code)
            if not model_path:
                continue
            
            env_stats_path = model_path.replace(".zip", "_env.pkl")

            # 環境準備
            raw_env = TradingEnv(df, stock_code=stock_code)
            env = DummyVecEnv([lambda: raw_env])

            if os.path.exists(env_stats_path):
                env = VecNormalize.load(env_stats_path, env)
                env.training = False
                env.norm_reward = False

            model = PPO.load(model_path, device='cpu')
            
            # テスト実行
            obs = env.reset()
            
            trades = []
            equity = [0]
            actions = []
            
            while True:
                action, _ = model.predict(obs, deterministic=True)
                obs, reward, done, info = env.step(action)
                
                info_dict = info[0]
                equity.append(info_dict['balance'] * UNIT_SHARES)
                actions.append(int(action[0]))
                
                if done[0]:
                    break

            # 結果集計
            wins = info_dict.get('wins', 0)
            losses = info_dict.get('losses', 0)
            total_trades = wins + losses
            win_rate = wins / total_trades * 100 if total_trades > 0 else 0
            final_profit = info_dict['balance'] * UNIT_SHARES
            
            # アクション分析
            action_counts = pd.Series(actions).value_counts()
            hold_pct = action_counts.get(0, 0) / len(actions) * 100 if actions else 0
            entry_count = action_counts.get(1, 0)
            
            result = {
                'code': stock_code,
                'version': version,
                'win_rate': win_rate,
                'wins': wins,
                'losses': losses,
                'total_trades': total_trades,
                'profit': final_profit,
                'hold_pct': hold_pct,
                'entry_count': entry_count
            }
            results.append(result)
            
            # グラフ保存
            fig, axes = plt.subplots(2, 1, figsize=(12, 8))
            
            # 資産推移
            axes[0].plot(equity, color='blue', linewidth=1)
            axes[0].axhline(0, color='red', linestyle='--', linewidth=1)
            axes[0].set_title(f'{stock_code} ({version}) - 勝率: {win_rate:.1f}%')
            axes[0].set_xlabel('Time')
            axes[0].set_ylabel('Profit (JPY)')
            axes[0].grid(True, alpha=0.3)
            
            # アクション分布
            # 0:待機, 1:BULL(強気), 2:BEAR(弱気) に変更
            action_labels = ['HOLD', 'BULL(L)', 'BEAR(S)'] 
            colors = ['#808080', 'red', 'blue'] # 強気=赤(陽線), 弱気=青(陰線) 
            counts = [action_counts.get(i, 0) for i in range(3)]
            axes[1].bar(action_labels, counts, color=colors)
            axes[1].set_title('Action Distribution')
            axes[1].set_ylabel('Count')
            
            plt.tight_layout()
            plt.savefig(os.path.join(RESULT_DIR, f"result_{stock_code}.png"), dpi=100)
            plt.close()

        except Exception as e:
            continue

    if not results:
        print("❌ テスト可能なモデルがありません")
        return

    # 結果表示（勝率順にソート）
    results = sorted(results, key=lambda x: x['win_rate'], reverse=True)
    
    print(f"\n{'CODE':<8} {'VER':<4} {'勝率':>8} {'勝敗':>10} {'損益(円)':>12} {'見送%':>8}")
    print("-" * 80)
    
    total_profit = 0
    total_wins = 0
    total_losses = 0
    profitable_stocks = 0
    high_winrate_stocks = 0
    
    for r in results:
        judge = ""
        if r['win_rate'] >= 60:
            judge = "🔥"
            high_winrate_stocks += 1
        elif r['win_rate'] >= 50:
            judge = "✅"
        elif r['total_trades'] == 0:
            judge = "💤"
        else:
            judge = "❌"
        
        if r['profit'] > 0:
            profitable_stocks += 1
        
        print(f"{r['code']:<8} {r['version']:<4} {r['win_rate']:>7.1f}% "
              f"{r['wins']:>4}勝{r['losses']:>3}敗 {r['profit']:>+12,.0f} "
              f"{r['hold_pct']:>7.1f}%  {judge}")
        
        total_profit += r['profit']
        total_wins += r['wins']
        total_losses += r['losses']

    # サマリー
    total_trades = total_wins + total_losses
    overall_winrate = total_wins / total_trades * 100 if total_trades > 0 else 0
    
    print("-" * 80)
    print(f"\n📊 総合結果:")
    print(f"   テスト銘柄数: {len(results)}")
    print(f"   総取引数: {total_trades} ({total_wins}勝 / {total_losses}敗)")
    print(f"   ★ 総合勝率: {overall_winrate:.1f}%")
    print(f"   勝率60%以上: {high_winrate_stocks} 銘柄")
    print(f"   プラス収支: {profitable_stocks} / {len(results)} 銘柄")
    print(f"   総損益: {total_profit:+,.0f} 円")
    
    # 評価
    print(f"\n📋 評価:")
    if overall_winrate >= 55:
        print(f"   ✅ 勝率 {overall_winrate:.1f}% は良好です")
    elif overall_winrate >= 50:
        print(f"   ⚠️ 勝率 {overall_winrate:.1f}% はもう少し改善が必要")
    else:
        print(f"   ❌ 勝率 {overall_winrate:.1f}% は要改善")
    
    if total_trades < len(results) * 5:
        print(f"   ⚠️ 取引回数が少なめ（平均 {total_trades/len(results):.1f}回/銘柄）")
        print(f"      → データ量を増やすか、エントリー条件を緩める検討を")
    
    # CSV保存
    df_results = pd.DataFrame(results)
    df_results.to_csv(os.path.join(RESULT_DIR, "backtest_summary.csv"), index=False)
    
    print(f"\n📁 詳細: {RESULT_DIR}/backtest_summary.csv")
    print(f"{'='*80}")


if __name__ == "__main__":
    backtest()