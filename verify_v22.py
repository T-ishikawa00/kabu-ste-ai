# -*- coding: utf-8 -*-
"""
verify_v22.py (Master_AI_v2 クールダウン検証)
=======================================
機能:
1. 損切り・利確ルールの適用 (SL=1.2%)
2. ★追加: クールダウン (取引終了後、一定時間は再エントリー禁止)
   これにより「往復ビンタ」や「ポジポジ病」を防ぐ
"""

import os
import yaml
import pandas as pd
import numpy as np
from stable_baselines3 import PPO
from step2_env_universal import TradingEnv

# 設定
MODEL_PATH = "runtime/Master_AI_v99"
DATA_DIR = "data"
CONFIG_FILE = "config.yaml"

FIXED_TARGETS = [
    "7003", "6525", "7011", "6269", "6954", "5016", "8306", 
    "285A", "3110", "8031", "9984", "9107"
]

def load_config_values():
    """configからルールを読み込む"""
    default = {"sl": 1.2, "tp": 3.0, "hold": 30, "cool": 30} # デフォルト30分
    if not os.path.exists(CONFIG_FILE): return default
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
            common = raw.get("common", {})
            safe = raw.get("safety", {})
            return {
                "sl": common.get("sl_pct", 1.2),
                "tp": common.get("tp_pct", 3.0),
                "hold": int(common.get("max_hold_sec", 1800) / 60),
                # ★コンフィグに設定がなければ強制的に30分(1800秒)休む
                "cool": int(safe.get("cooldown_after_exit_sec", 1800) / 60) 
            }
    except: return default

def run_verification():
    print("=" * 60)
    print("🤖 Master_AI_v2 検証 (クールダウン適用)")
    
    conf = load_config_values()
    # ★強制的にクールダウンを30分に設定（連敗阻止のため）
    COOLDOWN_STEPS = 10 
    
    print(f"🔧 ルール: SL={conf['sl']}% | Limit={conf['hold']}分 | 🧊Cool={COOLDOWN_STEPS}分")
    print("=" * 60)

    if not os.path.exists(MODEL_PATH + ".zip"):
        print(f"❌ モデルなし: {MODEL_PATH}")
        return
    
    try:
        model = PPO.load(MODEL_PATH, device='cpu')
        print("✅ AIロード成功")
    except:
        print("❌ AIロード失敗"); return

    total_results = []

    for code in FIXED_TARGETS:
        file_path = os.path.join(DATA_DIR, f"merged_{code}.csv")
        if not os.path.exists(file_path): continue

        try:
            df = pd.read_csv(file_path)
            df.columns = [c.lower() for c in df.columns]
            if 'close' not in df.columns or len(df) < 50: continue
            df = df.tail(400).reset_index(drop=True) # 少し長めに
        except: continue

        env = TradingEnv(df, stock_code=code, max_hold=conf['hold'])
        env.STOP_LOSS_PCT = -conf['sl']
        env.EXIT_WIN_BONUS = 0.0
        env.HOLD_LOSS_PENALTY = 0.0

        obs, _ = env.reset()
        done = False
        step = 0
        
        # クールダウン管理用
        cooldown_counter = 0
        last_trade_end_step = -999
        
        pnl_history = []

        print("-" * 60)
        print(f"📄 {code}: {len(df)} bars")

        while not done:
            # クールダウン中は強制STAY
            if step < last_trade_end_step + COOLDOWN_STEPS:
                action = 0 # STAY
            else:
                action, _ = model.predict(obs, deterministic=True)

            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            
            # ログ出力
            if action in [1, 2, 3]:
                act_str = ["STAY", "BUY", "SHORT", "EXIT"][action]
                price = df.iloc[env.current_step]['close']
                print(f"   {step:03d}: {act_str} @ {price:.0f}")
            
            # トレード終了検知（ポジションが0になった瞬間）
            if env.position == 0 and len(env.trades) > len(pnl_history):
                # 直前のトレード結果を取得
                new_trade_pnl = env.trades[-1]
                pnl_history.append(new_trade_pnl)
                
                # トレード終了時刻を記録 -> ここからクールダウン開始
                last_trade_end_step = step
                print(f"      [🧊 CoolDown... {COOLDOWN_STEPS} min]")

            step += 1

        # 集計
        win = len([x for x in pnl_history if x > 0])
        loss = len([x for x in pnl_history if x <= 0])
        pnl = sum(pnl_history)
        icon = "💰" if pnl > 0 else "💸"
        
        if len(pnl_history) > 0:
            win_rate = (win/len(pnl_history))*100
            print(f"   📊 {win}勝{loss}敗 (勝率{win_rate:.0f}%) | 損益: {pnl:+.2f}% {icon}")
            total_results.append(pnl)
        else:
            print("   💤 トレードなし")

    print("=" * 60)
    avg = sum(total_results)/len(total_results) if total_results else 0
    print(f"📈 平均損益: {avg:+.2f}%")

if __name__ == "__main__":
    run_verification()