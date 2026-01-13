# -*- coding: utf-8 -*-
import os
import glob
import re
import pandas as pd
from datetime import datetime, timedelta

# ==========================================
# 設定
# ==========================================
SEARCH_DIRS = ["runtime", "logs", ".", "output"]
REPORT_DIR = "reports"

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def load_yesterday_log():
    # 昨日の日付を計算
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_nodash = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    
    print(f"🔍 昨日の日付 ({yesterday}) のログを探しています...")

    all_files = []
    for d in SEARCH_DIRS:
        patterns = [os.path.join(d, "*.log"), os.path.join(d, "**", "*.log")]
        for p in patterns:
            found = glob.glob(p, recursive=True)
            for f in found:
                fname = os.path.basename(f)
                if (yesterday in fname) or (yesterday_nodash in fname):
                    all_files.append(f)
    
    if not all_files: return None
    # 最新のものを返す
    return max(all_files, key=os.path.getctime)

def read_file_safely(filepath):
    """文字コードを自動判別して読み込む"""
    encodings = ['utf-8', 'cp932', 'shift_jis', 'latin-1']
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
    return []

def analyze(log_file):
    trades = []
    open_positions = {} 
    
    total_pnl = 0.0
    wins = 0
    losses = 0
    draws = 0
    symbol_pnl = {}
    symbol_count = {}

    print(f"📂 解析対象: {log_file}")
    
    lines = read_file_safely(log_file)
    print(f"📄 読み込んだ行数: {len(lines)} 行")

    for line in lines:
        line = line.strip()
        
        # --- 時刻抽出 (強化版) ---
        current_dt = None
        # 行のどこかにある時刻パターンを探す
        ts_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if ts_match:
            current_dt = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
        else:
            t_match = re.search(r"(\d{2}:\d{2}:\d{2})", line)
            if t_match:
                # 日付がない場合は昨日の日付を補完
                yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                try:
                    current_dt = datetime.strptime(f"{yesterday_str} {t_match.group(1)}", "%Y-%m-%d %H:%M:%S")
                except: pass
        
        time_str = current_dt.strftime("%H:%M:%S") if current_dt else "--:--:--"

        # --- A. エントリー記録 (保有時間計算用) ---
        if ("LIVE BUY" in line or "LIVE SHORT" in line) and "EXIT" not in line:
            code_match = re.search(r"LIVE (?:BUY|SHORT) (\w+)", line)
            if code_match and current_dt:
                code = code_match.group(1)
                open_positions[code] = current_dt

        # --- B. イグジット記録 (損益計算用) ---
        # ★重要: "pnl=" がなくても "EXIT" があれば必ず読み込む
        if "LIVE EXIT" in line:
            
            # 1. 銘柄コード
            code = "UNKNOWN"
            code_match = re.search(r"EXIT \w+ (\w+)", line)
            if code_match: code = code_match.group(1)
            
            # 2. 売買方向
            side = "BUY"
            if "SELL" in line: side = "SELL"

            # 3. 理由
            reason = "-"
            reason_match = re.search(r"reason=(\w+)", line)
            if reason_match: reason = reason_match.group(1)

            # 4. 損益 (昨日のログ対策: pnlがなくてもエラーにせず 0円 とする)
            pnl = 0.0
            pnl_match = re.search(r"pnl[=:\s]+([-\d\.]+)", line, re.IGNORECASE)
            if pnl_match:
                try:
                    pnl = float(pnl_match.group(1))
                except: pass

            # 5. 保有時間
            hold_min = 999.9
            if code in open_positions and current_dt:
                start_dt = open_positions.pop(code)
                duration = (current_dt - start_dt).total_seconds()
                hold_min = round(duration / 60, 1)

            # 集計
            total_pnl += pnl
            if pnl > 0: wins += 1
            elif pnl < 0: losses += 1
            else: draws += 1
            
            if code not in symbol_pnl:
                symbol_pnl[code] = 0.0
                symbol_count[code] = 0
            symbol_pnl[code] += pnl
            symbol_count[code] += 1

            trades.append({
                "Time": time_str,
                "Code": code,
                "Side": side,
                "Reason": reason,
                "PnL": pnl,
                "HoldMin": hold_min
            })

    return trades, total_pnl, wins, losses, draws, symbol_pnl, symbol_count

def main():
    ensure_dir(REPORT_DIR)
    
    log_file = load_yesterday_log()
    if not log_file:
        print("❌ 昨日のログが見つかりませんでした。")
        return

    trades, total_pnl, w, l, d, s_pnl, s_cnt = analyze(log_file)

    if not trades:
        print(f"⚠️ ログファイル ({log_file}) は読み込めましたが、'EXIT' の行が1つも検出できませんでした。")
        return

    # === レポート出力 ===
    print("\n" + "="*80)
    print(f"📊 昨日の成績レポート (ファイル: {os.path.basename(log_file)})")
    print("="*80)
    print(f"{'時刻':<10} | {'CODE':<6} | {'売買':<4} | {'理由':<8} | {'保有(分)':<8} | {'      損益 (円)'}")
    print("-" * 80)
    
    hold_times = []
    for t in trades:
        mark = "○" if t['PnL'] > 0 else "●" if t['PnL'] < 0 else " "
        
        h_str = f"{t['HoldMin']}m" if t['HoldMin'] != 999.9 else "-"
        if t['HoldMin'] != 999.9:
            hold_times.append(t['HoldMin'])

        # PnLが0の場合は「(記録なし)」とみなして表示を変えるか、そのまま0とするか
        # ここではそのまま表示しますが、昨日のログの場合は全部0円になります
        print(f"{t['Time']:<10} | {t['Code']:<6} | {t['Side']:<4} | {t['Reason']:<8} | {h_str:<8} | {mark} {t['PnL']:>10,.0f}")

    print("-" * 80)
    
    total_trades = w + l + d
    win_rate = (w / total_trades * 100) if total_trades > 0 else 0.0
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0
    
    print(f"💰 合計損益:  {total_pnl:+,.0f} 円 (※昨日のログには損益額が記録されていません)")
    print(f"📊 勝率    :  {win_rate:.1f}% ({w}勝 {l}敗 {d}分)")
    print(f"⏱️ 平均保有:  {avg_hold:.1f} 分")
    print("-" * 80)
    
    print("【銘柄別成績 (回数のみ)】")
    sorted_syms = sorted(s_cnt.items(), key=lambda x: x[1], reverse=True)
    for code, cnt in sorted_syms:
        print(f"  - {code:<6}: {cnt} 回")
    print("=" * 80)

if __name__ == "__main__":
    main()