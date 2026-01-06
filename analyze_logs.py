# -*- coding: utf-8 -*-
import os
import glob
import re
import pandas as pd
from datetime import datetime

# ==========================================
# 設定: ログを探す場所（複数指定）
# ==========================================
SEARCH_DIRS = ["runtime", "logs", ".", "output"]  # ここにあるフォルダを全部探します
REPORT_DIR = "reports"

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def load_latest_log():
    all_files = []
    
    # 定義されたフォルダを全部巡回
    for d in SEARCH_DIRS:
        # .log と .txt 両方探す
        patterns = [
            os.path.join(d, "*.log"),
            os.path.join(d, "*.txt"),
            os.path.join(d, "**", "*.log"), # サブフォルダも
        ]
        for p in patterns:
            found = glob.glob(p, recursive=True)
            all_files.extend(found)
    
    # 重複除去
    all_files = list(set(all_files))

    if not all_files:
        return None
        
    # 一番新しいファイルを特定
    latest_file = max(all_files, key=os.path.getctime)
    return latest_file

def analyze(log_file):
    trades = []
    open_positions = {} 
    
    daily_pnl = 0.0
    wins = 0
    losses = 0
    draws = 0
    symbol_pnl = {}
    symbol_count = {}

    print(f"📂 読み込み成功: {log_file}")

    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if "LIVE" not in line: continue

            # --- 時刻抽出 ---
            dt = None
            try:
                parts = line.split()
                if len(parts) >= 2:
                    ts_str = f"{parts[0]} {parts[1].split(',')[0]}"
                    try:
                        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    except:
                        if re.match(r"\d{2}:\d{2}:\d{2}", parts[0]):
                            today = datetime.now().strftime("%Y-%m-%d")
                            dt = datetime.strptime(f"{today} {parts[0]}", "%Y-%m-%d %H:%M:%S")
            except:
                pass
            
            if dt is None: continue

            # --- ENTRY検知 ---
            if ("LIVE BUY" in line or "LIVE SHORT" in line) and "EXIT" not in line:
                match = re.search(r"LIVE (BUY|SHORT) (\w+)", line)
                if match:
                    side = match.group(1)
                    code = match.group(2)
                    open_positions[code] = {"start_time": dt, "side": side}

            # --- EXIT検知 ---
            if "LIVE EXIT" in line and "pnl=" in line:
                code_match = re.search(r"LIVE EXIT (BUY|SELL|SHORT) (\w+)", line)
                pnl_match = re.search(r"pnl=([-\d\.]+)", line)
                reason_match = re.search(r"reason=(\w+)", line)
                
                if code_match and pnl_match:
                    code = code_match.group(2)
                    pnl = float(pnl_match.group(1))
                    reason = reason_match.group(1) if reason_match else "UNKNOWN"
                    
                    hold_min = 999.9
                    if code in open_positions:
                        start_time = open_positions.pop(code)["start_time"]
                        duration = (dt - start_time).total_seconds()
                        hold_min = round(duration / 60, 1)

                    daily_pnl += pnl
                    if pnl > 0: wins += 1
                    elif pnl < 0: losses += 1
                    else: draws += 1
                    
                    if code not in symbol_pnl:
                        symbol_pnl[code] = 0.0
                        symbol_count[code] = 0
                    symbol_pnl[code] += pnl
                    symbol_count[code] += 1

                    trades.append({
                        "Time": dt.strftime("%H:%M:%S"),
                        "Code": code,
                        "Reason": reason,
                        "PnL": pnl,
                        "HoldMin": hold_min
                    })

    return trades, daily_pnl, wins, losses, draws, symbol_pnl, symbol_count

def main():
    ensure_dir(REPORT_DIR)
    
    # ログ探索
    print("🔍 ログファイルを探しています...")
    log_file = load_latest_log()
    
    if not log_file:
        print("\n❌ ログファイルが見つかりません。")
        print("   ボットがログをファイルに出力していないか、フォルダ名が異なります。")
        print(f"   探索対象: {SEARCH_DIRS}")
        return

    trades, total_pnl, w, l, d, s_pnl, s_cnt = analyze(log_file)
    
    if not trades:
        print("⚠️ ログファイルは見つかりましたが、'LIVE EXIT' の記録がまだありません。")
        return

    # 結果表示
    print("\n" + "="*70)
    print(f"📊 分析レポート (対象: {os.path.basename(log_file)})")
    print("="*70)
    print(f"{'時刻':<10} | {'CODE':<6} | {'理由':<8} | {'保有(分)':<8} | {'損益 (円)':>10}")
    print("-" * 70)
    
    hold_times = []
    for t in trades:
        mark = "○" if t['PnL'] > 0 else "●" if t['PnL'] < 0 else " "
        h_str = f"{t['HoldMin']}m" if t['HoldMin'] != 999.9 else "-"
        if t['HoldMin'] != 999.9: hold_times.append(t['HoldMin'])
        print(f"{t['Time']:<10} | {t['Code']:<6} | {t['Reason']:<8} | {h_str:<8} | {mark} {t['PnL']:>10,.0f}")

    print("-" * 70)
    
    total_trades = w + l + d
    win_rate = (w / total_trades * 100) if total_trades > 0 else 0.0
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0
    
    print(f"💰 本日合計:  {total_pnl:+,.0f} 円")
    print(f"📊 勝率    :  {win_rate:.1f}% ({w}勝 {l}敗 {d}分)")
    print(f"⏱️ 平均保有:  {avg_hold:.1f} 分")
    print("-" * 70)
    
    print("【銘柄別成績】")
    sorted_syms = sorted(s_pnl.items(), key=lambda x: x[1], reverse=True)
    for code, pnl in sorted_syms:
        cnt = s_cnt[code]
        print(f"  - {code}: {pnl:+,.0f} 円 ({cnt}回)")
    print("=" * 70)

if __name__ == "__main__":
    main()