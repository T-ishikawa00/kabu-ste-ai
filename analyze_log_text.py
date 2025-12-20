# -*- coding: utf-8 -*-
import os
import glob
import re
import datetime

def main():
    # 1. ログファイルを探す (run_*.log)
    # カレントディレクトリにある run_*.log をすべて取得
    log_files = glob.glob("run_*.log")
    
    if not log_files:
        print("="*60)
        print(" ⚠️ run_*.log ファイルが見つかりません。")
        print("    Botを一度も動かしていないか、場所が違います。")
        print("="*60)
        return

    # 一番新しいファイル（更新日時が最新）を自動で選ぶ
    target_file = max(log_files, key=os.path.getctime)

    print("="*60)
    print(f" 📄 最新ログ解析: {target_file}")
    print("="*60)

    # 2. データを抽出するための「型」を作る
    # ログ例: 2025-12-17 13:26:01 ... [INFO] PAPER EXIT SELL 8306 reason=TIME ... pnl=150
    # 上記のような行を見つけて、中身を抜き出す魔法（正規表現）
    pattern = re.compile(r"(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}).*(PAPER|LIVE) EXIT (BUY|SELL) (\d{4}) .*reason=(\w+).*pnl=([-\d\.]+)")

    trades = []

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            for line in f:
                # 行の中に "EXIT" という文字がある行だけ詳しく見る（処理を速くするため）
                if "EXIT" in line:
                    match = pattern.search(line)
                    if match:
                        # データを抜き出す
                        dt_full = match.group(1)   # 日時 (2025-12-17 13:26:01)
                        time_part = dt_full.split(" ")[1] # 時間だけ (13:26:01)
                        mode = match.group(2)      # PAPER か LIVE か
                        side = match.group(3)      # BUY か SELL か
                        code = match.group(4)      # 銘柄コード (8306など)
                        reason = match.group(5)    # 理由 (TIME, COND, TP, SL)
                        pnl = float(match.group(6)) # 損益 (150.0)

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

    # 3. 集計と表示
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
    
    # 銘柄ごとの成績を記録する辞書
    symbol_stats = {}

    for t in trades:
        # 見やすいようにマークをつける
        mark = " "
        if t["pnl"] > 0: mark = "○"
        elif t["pnl"] < 0: mark = "●"
        
        # 1行ずつ表示
        print(f"{t['time']:<10} | {t['code']:<6} | {t['side']:<4} | {t['reason']:<6} | {mark} {t['pnl']:>8,.0f}")

        # 合計を計算
        total_pnl += t["pnl"]
        if t["pnl"] > 0: wins += 1
        elif t["pnl"] < 0: losses += 1
        else: draws += 1
        
        # 銘柄別集計
        if t['code'] not in symbol_stats:
            symbol_stats[t['code']] = {"pnl": 0, "count": 0}
        symbol_stats[t['code']]["pnl"] += t["pnl"]
        symbol_stats[t['code']]["count"] += 1

    # サマリー表示
    total_trades = len(trades)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

    print("-" * 65)
    print(f" 💰 本日損益:  {total_pnl:+,.0f} 円")
    print(f" 📊 勝率    :  {win_rate:.1f}% ({wins}勝 {losses}敗 {draws}分)")
    print(f" 🔢 取引回数:  {total_trades} 回")
    
    # 銘柄別成績も出す
    print("-" * 65)
    print(" 【銘柄別成績】")
    for code, stat in symbol_stats.items():
        print(f"  - {code}: {stat['pnl']:+,.0f} 円 ({stat['count']}回)")
    
    print("="*60)

if __name__ == "__main__":
    main()