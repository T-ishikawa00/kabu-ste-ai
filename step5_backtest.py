# -*- coding: utf-8 -*-
"""
step5_backtest.py v12.4 (Target Only Fix)
=========================================
個別モデルと汎用モデルを自動で使い分けるバックテスト
【修正点】
 1. 全てのデータファイルではなく、ConfigとTarget Listにある
    「監視対象銘柄」だけを厳選してバックテストします。
 2. ログ解析ロジックは最新版(v19対応)を維持。
"""

import os
import glob
import re
import yaml
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
LOG_DIR = "logs"
RESULT_DIR = "backtest_results"
CONFIG_FILE = "config.yaml"
TARGET_LIST_FILE = "target_list.csv"
UNIT_SHARES = 100 

def get_target_symbols():
    """ConfigとTarget Listから対象銘柄を取得"""
    targets = set()
    
    # 1. Config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                for s in cfg.get("symbols", []):
                    targets.add(str(s["code"]))
        except: pass

    # 2. Target List
    if os.path.exists(TARGET_LIST_FILE):
        try:
            df = pd.read_csv(TARGET_LIST_FILE)
            for c in df['code'].astype(str).tolist():
                if len(c)==5 and c.endswith('0'): c=c[:-1]
                targets.add(c)
        except: pass
    
    return targets

def get_battle_results():
    """ログ解析 (v17対応)"""
    log_files = glob.glob(os.path.join(LOG_DIR, "strategy_*.log"))
    results = {}
    print(f"🔍 ログ解析中... ({len(log_files)} files)")

    for filepath in log_files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "EXIT" not in line: continue
                    code, pnl = None, 0
                    
                    if "LIVE EXIT_EXEC" in line:
                        try:
                            parts = line.split()
                            idx = parts.index("EXIT_EXEC")
                            code = parts[idx + 1]
                            pnl_str = [x for x in parts if x.startswith("pnl=")][0]
                            pnl = float(pnl_str.split("=")[1])
                        except: pass
                    elif "PAPER EXIT" in line:
                        try:
                            parts = line.split()
                            idx = parts.index("EXIT")
                            code = parts[idx + 2]
                            pnl_str = [x for x in parts if x.startswith("pnl=")][0]
                            pnl = float(pnl_str.split("=")[1])
                        except: pass
                    
                    if code and str(code).strip().isdigit():
                        code = str(code).strip()
                        if code not in results: results[code] = 0
                        results[code] += int(pnl)
        except: pass
    return results

def find_model(stock_code):
    candidates = glob.glob(os.path.join(MODEL_DIR, f"model_{stock_code}*.zip"))
    indv = [c for c in candidates if "universal" not in c]
    if indv:
        indv.sort(key=os.path.getmtime, reverse=True)
        return indv[0], os.path.basename(indv[0]).replace(".zip",""), False
    
    univ = glob.glob(os.path.join(MODEL_DIR, "model_universal*.zip"))
    if univ:
        univ.sort(key=os.path.getmtime, reverse=True)
        return univ[0], "univ", True
    return None, None, False

def backtest():
    os.makedirs(RESULT_DIR, exist_ok=True)
    targets = get_target_symbols()
    
    if not targets:
        print("❌ 対象銘柄が見つかりません (Config/TargetListを確認してください)")
        return

    print(f"🎯 テスト対象: {len(targets)} 銘柄")
    battle_results = get_battle_results()
    results = []

    for stock_code in targets:
        csv_file = os.path.join(DATA_DIR, f"merged_{stock_code}.csv")
        if not os.path.exists(csv_file):
            # print(f"⚠️ データなし: {stock_code}")
            continue

        try:
            df = pd.read_csv(csv_file)
            if len(df) < 300: continue

            model_path, version, is_univ = find_model(stock_code)
            if not model_path: continue

            score = battle_results.get(stock_code, 0)
            df['battle_score'] = score / 10000.0

            start_idx = int(len(df) * 0.8)
            test_df = df.iloc[start_idx:].reset_index(drop=True)
            if len(test_df) < 50: continue

            raw_env = TradingEnv(test_df, stock_code=stock_code)
            env = DummyVecEnv([lambda: raw_env])
            
            # 統計ロード
            env_stats = model_path.replace(".zip", "_env.pkl")
            if os.path.exists(env_stats):
                try:
                    env = VecNormalize.load(env_stats, env)
                    env.training = False
                    env.norm_reward = False
                except: pass

            model = PPO.load(model_path, device='cpu')
            obs = env.reset()
            done = False
            
            while not done:
                action, _ = model.predict(obs, deterministic=True)
                obs, reward, done_arr, info = env.step(action)
                done = done_arr[0]
                info_d = info[0]

            res = {
                'code': stock_code,
                'version': version,
                'type': 'Univ' if is_univ else 'Indv',
                'win_rate': 0,
                'wins': info_d.get('wins',0),
                'losses': info_d.get('losses',0),
                'profit': info_d['balance'] * UNIT_SHARES,
                'battle_score': score
            }
            total = res['wins'] + res['losses']
            if total > 0: res['win_rate'] = res['wins']/total*100
            results.append(res)
            
            icon = "💰" if res['profit']>0 else "💸"
            print(f"[{stock_code}] {version} {icon} {res['profit']:+,.0f}円 | 勝率 {res['win_rate']:.1f}%")

        except Exception as e:
            # print(f"Error {stock_code}: {e}")
            pass

    if not results:
        print("❌ 結果なし")
        return

    results = sorted(results, key=lambda x: x['profit'], reverse=True)
    print(f"\n{'CODE':<8} {'TYPE':<4} {'勝率':>8} {'損益(円)':>12} {'実戦益':>10}")
    print("-" * 60)
    
    total_p = 0
    for r in results:
        judge = "🔥" if r['win_rate']>=60 else ("✅" if r['profit']>0 else "❌")
        b_str = f"{r['battle_score']:+}" if r['battle_score']!=0 else "-"
        print(f"{r['code']:<8} {r['type']:<4} {r['win_rate']:>7.1f}% {r['profit']:>+12,.0f} {b_str:>10} {judge}")
        total_p += r['profit']

    print("-" * 60)
    print(f"💰 総損益: {total_p:+,.0f} 円")
    pd.DataFrame(results).to_csv(os.path.join(RESULT_DIR, "backtest_summary.csv"), index=False)

if __name__ == "__main__":
    backtest()