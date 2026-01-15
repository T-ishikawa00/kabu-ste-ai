# -*- coding: utf-8 -*-
"""
step5_backtest.py v12.1 (Hybrid & Universal Support)
====================================================
個別モデルと汎用モデルを自動で使い分けるバックテスト
改善点:
  - 個別モデル(v12等)がない場合、汎用モデル(universal_v1)を自動適用
  - 正規化統計(env.pkl)の読み込み先をモデルに合わせて切り替え
  - 勝率と損益の詳細分析
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
    """
    利用可能なモデルを探す
    Return: (path, version, is_universal)
    """
    # 1. まず個別銘柄の専用モデルを探す (v12優先)
    for v in ['v12', 'v11', 'v10', 'v8', 'v7']:
        path = os.path.join(MODEL_DIR, f"model_{stock_code}_{v}.zip")
        if os.path.exists(path):
            return path, v, False

    # 2. なければ汎用モデルを探す
    univ_path = os.path.join(MODEL_DIR, "model_universal_v1.zip")
    if os.path.exists(univ_path):
        return univ_path, "univ", True

    return None, None, False


def backtest():
    os.makedirs(RESULT_DIR, exist_ok=True)
    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    
    if not csv_files:
        print("❌ データなし")
        return

    print(f"{'='*80}")
    print(f"🎯 ハイブリッドAI バックテスト v12.1")
    print(f"{'='*80}")
    
    results = []
    
    # ランダムにピックアップしてテスト時間を短縮したい場合はここを調整
    # csv_files = csv_files[:30] 

    for csv_file in csv_files:
        try:
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            df = pd.read_csv(csv_file)
            
            # データ量チェック (テスト用データ確保のため)
            if len(df) < 300:
                continue

            # モデル検索
            model_path, version, is_universal = find_model(stock_code)
            if not model_path:
                continue
            
            # テスト期間の設定（データの後半20%を未知データとしてテスト）
            start_idx = int(len(df) * 0.8)
            test_df = df.iloc[start_idx:].reset_index(drop=True)
            
            if len(test_df) < 50:
                continue

            # 環境準備
            raw_env = TradingEnv(test_df, stock_code=stock_code)
            env = DummyVecEnv([lambda: raw_env])

            # 正規化統計のロード
            if is_universal:
                # 汎用モデル用の統計データ
                env_stats_path = os.path.join(MODEL_DIR, "model_universal_v1_env.pkl")
            else:
                # 個別モデル用の統計データ
                env_stats_path = model_path.replace(".zip", "_env.pkl")

            if os.path.exists(env_stats_path):
                env = VecNormalize.load(env_stats_path, env)
                env.training = False
                env.norm_reward = False
            else:
                # 統計ファイルがない場合は正規化なしで進める（警告）
                pass

            # モデルロード
            model = PPO.load(model_path, device='cpu')
            
            # テスト実行
            obs = env.reset()
            
            equity = [0]
            actions = []
            
            done = False
            while not done:
                action, _ = model.predict(obs, deterministic=True)
                obs, reward, done_array, info = env.step(action)
                done = done_array[0]
                
                info_dict = info[0]
                equity.append(info_dict['balance'] * UNIT_SHARES)
                
                # actionは配列で返る場合があるのでスカラ化
                act_val = action[0] if isinstance(action, (np.ndarray, list)) else action
                actions.append(int(act_val))

            # 結果集計
            wins = info_dict.get('wins', 0)
            losses = info_dict.get('losses', 0)
            total_trades = wins + losses
            win_rate = wins / total_trades * 100 if total_trades > 0 else 0.0
            final_profit = info_dict['balance'] * UNIT_SHARES
            
            # アクション分析
            action_series = pd.Series(actions)
            action_counts = action_series.value_counts()
            hold_pct = action_counts.get(0, 0) / len(actions) * 100 if actions else 0
            entry_count = action_counts.get(1, 0) + action_counts.get(2, 0) # Buy + Sell
            
            result = {
                'code': stock_code,
                'version': version,
                'type': 'Univ' if is_universal else 'Indv',
                'win_rate': win_rate,
                'wins': wins,
                'losses': losses,
                'total_trades': total_trades,
                'profit': final_profit,
                'hold_pct': hold_pct,
                'entry_count': entry_count
            }
            results.append(result)
            
            # グラフ保存 (オプション)
            # save_graph(stock_code, version, equity, actions, win_rate)

            # 進捗表示
            icon = "💰" if final_profit > 0 else "💸"
            print(f"[{stock_code}] {version} ({len(test_df)}bars) {icon} {final_profit:+,.0f}円 | 勝率 {win_rate:.1f}%")

        except Exception as e:
            print(f"⚠️ エラー {stock_code}: {e}")
            continue

    if not results:
        print("❌ テスト可能なモデル/データがありません")
        return

    # 結果表示（勝率順にソート）
    results = sorted(results, key=lambda x: x['win_rate'], reverse=True)
    
    print(f"\n{'CODE':<8} {'TYPE':<4} {'勝率':>8} {'勝敗':>10} {'損益(円)':>12} {'見送%':>8}")
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
        
        print(f"{r['code']:<8} {r['type']:<4} {r['win_rate']:>7.1f}% "
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
    
    # CSV保存
    df_results = pd.DataFrame(results)
    df_results.to_csv(os.path.join(RESULT_DIR, "backtest_summary.csv"), index=False)
    
    print(f"\n📁 詳細保存: {RESULT_DIR}/backtest_summary.csv")
    print(f"{'='*80}")


if __name__ == "__main__":
    backtest()