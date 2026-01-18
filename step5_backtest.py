# -*- coding: utf-8 -*-
"""
step5_backtest.py v12.2 (Hybrid & Universal + Battle Score)
===========================================================
個別モデルと汎用モデルを自動で使い分けるバックテスト
改善点:
  - 個別モデル(v13等)がない場合、汎用モデル(universal_v1)を自動適用
  - 正規化統計(env.pkl)の読み込み先をモデルに合わせて切り替え
  - ★ログから「戦績スコア」を読み込み、テスト環境にも反映させる
"""

import os
import glob
import re
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
LOG_DIR = "logs"  # main.pyのログ
RESULT_DIR = "backtest_results"
UNIT_SHARES = 100  # 1単元


def get_battle_results():
    """ログから戦績を取得する（Step4と同じロジック）"""
    log_files = glob.glob(os.path.join(LOG_DIR, "*.log"))
    results = {}
    pattern = re.compile(r"LIVE EXIT_EXEC (\d{4}) pnl=([+\-]?\d+)")
    
    for filepath in log_files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    match = pattern.search(line)
                    if match:
                        code = match.group(1)
                        pnl = int(match.group(2))
                        if code not in results: results[code] = 0
                        results[code] += pnl
        except: pass
    return results


def find_model(stock_code):
    """
    利用可能なモデルを探す
    Return: (path, version, is_universal)
    """
    # 1. まず個別銘柄の専用モデルを探す (v13優先)
    # バージョン名を動的に探す
    candidates = glob.glob(os.path.join(MODEL_DIR, f"model_{stock_code}*.zip"))
    
    # "universal" を含まないものを抽出
    indv_models = [c for c in candidates if "universal" not in c]
    
    if indv_models:
        # 最新のものを採用（更新日時が新しい順）
        indv_models.sort(key=os.path.getmtime, reverse=True)
        best_model = indv_models[0]
        version = os.path.basename(best_model).replace(f"model_{stock_code}", "").replace(".zip", "").strip("_")
        return best_model, version, False

    # 2. なければ汎用モデルを探す
    univ_path = os.path.join(MODEL_DIR, "model_universal_v1.zip") # v2.0対応なら名前変更が必要かも
    
    # 名前が変わっている可能性があるので検索
    univ_candidates = glob.glob(os.path.join(MODEL_DIR, "universal_model*.zip"))
    if univ_candidates:
        univ_candidates.sort(key=os.path.getmtime, reverse=True)
        return univ_candidates[0], "univ", True

    return None, None, False


def backtest():
    os.makedirs(RESULT_DIR, exist_ok=True)
    
    # 戦績データの取得（テスト用データにも注入するため）
    battle_results = get_battle_results()
    print(f"📊 戦績データロード: {len(battle_results)} 銘柄")

    csv_files = glob.glob(os.path.join(DATA_DIR, "merged_*.csv"))
    
    if not csv_files:
        print("❌ データなし")
        return

    print(f"{'='*80}")
    print(f"🎯 ハイブリッドAI バックテスト v12.2")
    print(f"{'='*80}")
    
    results = []
    
    for csv_file in csv_files:
        try:
            filename = os.path.basename(csv_file)
            stock_code = filename.replace("merged_", "").replace(".csv", "")
            df = pd.read_csv(csv_file)
            
            # データ量チェック
            if len(df) < 300:
                continue

            # モデル検索
            model_path, version, is_universal = find_model(stock_code)
            if not model_path:
                continue
            
            # ★戦績スコアの注入（もしCSVになければ注入）
            if 'battle_score' not in df.columns:
                score = battle_results.get(stock_code, 0)
                df['battle_score'] = score / 1000.0

            # テスト期間の設定（後半20%）
            start_idx = int(len(df) * 0.8)
            test_df = df.iloc[start_idx:].reset_index(drop=True)
            
            if len(test_df) < 50:
                continue

            # 環境準備
            # 個別モデルなら個別用Env、汎用なら汎用Envを使うべきだが、
            # 現状は step2_env (個別用) と step2_env_universal (汎用用) がほぼ同じ構造(18次元)なら
            # step2_env.TradingEnv で統一しても動くはず。
            # もし次元エラーが出たらここで分岐が必要。
            raw_env = TradingEnv(test_df, stock_code=stock_code)
            env = DummyVecEnv([lambda: raw_env])

            # 正規化統計のロード
            env_stats_path = model_path.replace(".zip", "_env.pkl")
            
            if os.path.exists(env_stats_path):
                try:
                    env = VecNormalize.load(env_stats_path, env)
                    env.training = False
                    env.norm_reward = False
                except Exception as e:
                    print(f"⚠️ 統計ロード失敗({stock_code}): {e}")
            else:
                # 統計がない場合は警告しつつ続行
                # print(f"⚠️ 統計なし: {stock_code}")
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
            entry_count = action_counts.get(1, 0) + action_counts.get(2, 0)
            
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
            
            icon = "💰" if final_profit > 0 else "💸"
            print(f"[{stock_code}] {version} {icon} {final_profit:+,.0f}円 | 勝率 {win_rate:.1f}% ({wins}勝{losses}敗)")

        except Exception as e:
            # print(f"⚠️ エラー {stock_code}: {e}")
            continue

    if not results:
        print("❌ テスト可能なモデル/データがありません")
        return

    # 結果表示
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
    
    df_results = pd.DataFrame(results)
    df_results.to_csv(os.path.join(RESULT_DIR, "backtest_summary.csv"), index=False)
    
    print(f"\n📁 詳細保存: {RESULT_DIR}/backtest_summary.csv")
    print(f"{'='*80}")


if __name__ == "__main__":
    backtest()