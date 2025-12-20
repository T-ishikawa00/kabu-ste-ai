# -*- coding: utf-8 -*-
import os
import re
from pathlib import Path
from datetime import datetime

def main():
    print("="*80)
    print(" 🕵️‍♂️ 詳細トレード分析 (Entry -> Exit)")
    
    # 最新ログを探す
    log_files = list(Path(".").rglob("run_*.log"))
    if not log_files:
        print(" ⚠️ ログファイルが見つかりません")
        return
    target_file_path = max(log_files, key=lambda p: p.stat().st_mtime)
    print(f" 📄 解析ファイル: {target_file_path}")
    print("="*80)

    # パターン定義
    pat_entry = re.compile(r"(\d{2}:\d{2}:\d{2}).*(PAPER|LIVE) (BUY|SHORT|SELL) (\d{4}).*@\s?([\d\.]+)")
    
    # ★修正: pnl が先、reason が後！
    pat_exit  = re.compile(r"(\d{2}:\d{2}:\d{2}).*(PAPER|LIVE) EXIT (BUY|SELL) (\d{4}).*pnl=([-\d\.]+).*reason=(\w+)")

    positions = {}
    trades = []

    with open(target_file_path, "r", encoding="utf-8") as f:
        for line in f:
            # --- エントリー検知 ---
            if "BUY" in line or "SHORT" in line or ("SELL" in line and "EXIT" not in line):
                if "EXIT" in line: 
                    pass
                else:
                    if "BUY" in line and "EXIT" not in line:
                        m = re.search(r"(\d{2}:\d{2}:\d{2}).*(PAPER|LIVE) BUY (\d{4}).*cur=([\d\.]+)", line)
                        if m:
                            t, mode, code, px = m.groups()
                            positions[code] = {"entry_time": t, "side": "BUY", "entry_px": float(px)}
                    
                    elif ("SELL" in line or "SHORT" in line) and "EXIT" not in line:
                        m = re.search(r"(\d{2}:\d{2}:\d{2}).*(LIVE|PAPER) (?:SHORT|SELL) (\d{4}).*[@=]\s?([\d\.]+)", line)
                        if m:
                            t, mode, code, px = m.groups()
                            positions[code] = {"entry_time": t, "side": "SELL", "entry_px": float(px)}

            # --- イグジット検知 ---
            if "EXIT" in line:
                m = pat_exit.search(line)
                if m:
                    exit_time, mode, side, code, pnl_str, reason = m.groups()
                    pnl = float(pnl_str)
                    
                    entry_info = positions.get(code)
                    
                    if entry_info:
                        entry_time = entry_info["entry_time"]
                        entry_px = entry_info["entry_px"]
                        trade_side = entry_info["side"]
                        
                        fmt = "%H:%M:%S"
                        t1 = datetime.strptime(entry_time, fmt)
                        t2 = datetime.strptime(exit_time, fmt)
                        hold_sec = int((t2 - t1).total_seconds())
                        
                        trades.append({
                            "code": code,
                            "side": trade_side,
                            "in_time": entry_time,
                            "out_time": exit_time,
                            "hold": hold_sec,
                            "in_px": entry_px,
                            "pnl": pnl,
                            "reason": reason
                        })
                        del positions[code]
                    else:
                        # ログがない場合（私の書き忘れなど）
                        trades.append({
                            "code": code,
                            "side": side,
                            "in_time": "--:--:--", # ログ欠損
                            "out_time": exit_time,
                            "hold": 0,
                            "in_px": 0.0,
                            "pnl": pnl,
                            "reason": reason
                        })

    # --- 結果表示 ---
    print(f"{'時刻(IN)':<9} | {'時刻(OUT)':<9} | {'CODE':<4} | {'売買':<4} | {'IN価格':>7} | {'秒数':>3} | {'理由':<4} | {'損益'}")
    print("-" * 80)
    
    for t in trades:
        mark = "○" if t['pnl'] > 0 else "●" if t['pnl'] < 0 else "-"
        in_px_str = f"{t['in_px']:.0f}" if t['in_px'] > 0 else "----"
        print(f"{t['in_time']:<9} | {t['out_time']:<9} | {t['code']:<4} | {t['side']:<4} | {in_px_str:>7} | {t['hold']:>3}s | {t['reason']:<4} | {mark} {t['pnl']:+.0f}")

    print("-" * 80)
    print(" ※ 時刻(IN)が '--:--:--' の箇所は、ログ出力漏れのためエントリー時刻不明です。")

if __name__ == "__main__":
    main()