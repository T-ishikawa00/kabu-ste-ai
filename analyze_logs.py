# -*- coding: utf-8 -*-
import os
import re
import datetime
from pathlib import Path

def main():
    print("="*60)
    print(" 🔍 ログファイルを全検索中...")

    # 1. ログファイルを探す
    log_files = list(Path(".").rglob("run_*.log"))
    
    if not log_files:
        print("="*60)
        print(" ⚠️ run_*.log ファイルが見つかりません。")
        print("="*60)
        return

    # 最新ファイルを選択
    target_file_path = max(log_files, key=lambda p: p.stat().st_mtime)
    target_file = str(target_file_path)

    print(f" 📄 発見: {target_file}")
    print("="*60)

    # 2. データを抽出する（修正版：pnlが先、reasonが後のパターンに対応）
    # ログ例: ... PAPER EXIT SELL 8306 pnl=150 reason=TIME
    pattern = re.compile(r"(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}).*(PAPER|LIVE) EXIT (BUY|SELL) (\d{4}) .*pnl=([-\d\.]+).*reason=(\w+)")

    trades = []

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            for line in f:
                if "EXIT" in line:
                    match = pattern.search(line)
                    if match:
                        # 抽出データの順番が変わったので修正
                        dt_full = match.group(1)   
                        time_part = dt_full.split(" ")[1] 
                        mode = match.group(2)      
                        side = match.group(3)      
                        code = match.group(4)
                        # pnlが先に来る
                        pnl = float(match.group(5))
                        # reasonが後に来る
                        reason = match.group(6)

                        trades.append({
                            "time": time_part,
                            "code": code,
                            "side": side,
                            "reason": reason,
                            "pnl": pnl
                        })

    except Exception as e:
        print(f" ❌ 読み込みエラー: {e}")
        return

    # 3. 結果表示
    if not trades:
        print("\n 取引履歴が見つかりませんでした。")
        print(" (まだ決済していないか、起動しただけで取引していません)")
        print("="*60)
        return

    print(f"\n{'時刻':<10} | {'CODE':<6} | {'売買':<4} | {'理由':<6} | {'損益 (円)':>10}")
    print("-" * 65)

    total_pnl = 0
    wins = 0
    losses = 0
    draws = 0
    symbol_stats = {}

    for t in trades:
        mark = " "
        if t["pnl"] > 0: mark = "○"
        elif t["pnl"] < 0: mark = "●"
        
        print(f"{t['time']:<10} | {t['code']:<6} | {t['side']:<4} | {t['reason']:<6} | {mark} {t['pnl']:>8,.0f}")

        total_pnl += t["pnl"]
        if t["pnl"] > 0: wins += 1
        elif t["pnl"] < 0: losses += 1
        else: draws += 1
        
        if t['code'] not in symbol_stats:
            symbol_stats[t['code']] = {"pnl": 0, "count": 0}
        symbol_stats[t['code']]["pnl"] += t["pnl"]
        symbol_stats[t['code']]["count"] += 1

    total_trades = len(trades)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

    print("-" * 65)
    print(f" 💰 本日損益:  {total_pnl:+,.0f} 円")
    print(f" 📊 勝率    :  {win_rate:.1f}% ({wins}勝 {losses}敗 {draws}分)")
    print(f" 🔢 取引回数:  {total_trades} 回")
    
    print("-" * 65)
    print(" 【銘柄別成績】")
    for code, stat in symbol_stats.items():
        print(f"  - {code}: {stat['pnl']:+,.0f} 円 ({stat['count']}回)")
    
    print("="*60)

if __name__ == "__main__":
    main()