# -*- coding: utf-8 -*-
"""
Log Analyzer v2.13 (Fix Percentage Calculation)
=========================================
【v2.13 修正点】
・「+1.54%」のようにプラス記号がついたパーセンテージを正しく読み取れるように修正しました。
・金額がログに記載されていない場合、利益率(%)とエントリー価格から損益を自動計算します。
"""

import os
import glob
import re
from datetime import datetime

# ==========================================
# 設定
# ==========================================
SEARCH_DIRS = ["logs", "runtime", ".", "output"]
REPORT_DIR = "reports"
DEFAULT_QTY = 100

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def load_latest_log():
    strategy_files = []
    
    # 1. まず strategy_*.log を最優先で探す
    for d in SEARCH_DIRS:
        found = glob.glob(os.path.join(d, "strategy_*.log"))
        strategy_files.extend(found)
    
    if strategy_files:
        return max(strategy_files, key=os.path.getmtime)

    # 2. 見つからない場合のみ、他の .log を探す
    all_files = []
    for d in SEARCH_DIRS:
        found = glob.glob(os.path.join(d, "*.log"))
        all_files.extend(found)
        
    if not all_files: return None
    return max(all_files, key=os.path.getmtime)

def read_file_safe(filepath):
    encodings = ['utf-8', 'cp932', 'shift_jis', 'utf-16']
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
    with open(filepath, "r", encoding='utf-8', errors='ignore') as f:
        return f.readlines()

def analyze(log_file):
    trades = []
    open_positions = {} 
    daily_pnl = 0.0
    wins, losses, draws = 0, 0, 0
    symbol_pnl = {}
    symbol_count = {}

    print(f"📂 読み込み中: {log_file}")
    
    lines = read_file_safe(log_file)
    print(f"   ℹ️ 行数: {len(lines)}行")

    debug_entries = 0
    debug_exits = 0

    for line in lines:
        line = line.strip()
        
        # --- 1. 時刻抽出 ---
        current_dt = None
        try:
            ts_match = re.search(r"(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})", line)
            if ts_match:
                current_dt = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
        except: pass
        
        if current_dt is None: continue
        time_str = current_dt.strftime("%H:%M:%S")

        # --- 2. ENTRY (買い/売り) ---
        code, side, price = None, None, 0.0
        
        # パターンA (AI Entry)
        if "AI Entry:" in line:
            match = re.search(r"\[([0-9A-Z]{4})\].*?AI Entry:\s+(BUY|SHORT).*?@\s+([\d\.]+)", line)
            if match:
                code, side, price = match.group(1), match.group(2), float(match.group(3))

        # パターンB (LIVE Entry)
        elif "LIVE" in line and ("BUY" in line or "SHORT" in line) and "EXIT" not in line:
            match = re.search(r"(?:LIVE|PAPER)\s+(BUY|SHORT)\s+([0-9A-Z]{4})\s+@\s+([\d\.]+)", line)
            if match:
                side, code, price = match.group(1), match.group(2), float(match.group(3))

        if code:
            open_positions[code] = {"time": current_dt, "side": side, "price": price}
            debug_entries += 1

        # --- 3. EXIT (決済) ---
        exit_code = None
        exit_side = None
        pnl = 0.0
        reason = "UNK"
        is_exit_line = False

        if "Exit" in line or "EXIT" in line:
            # パターンA: [CODE] Exit
            match_new = re.search(r"\[([0-9A-Z]{4})\]\s+Exit\s+(BUY|SHORT)", line)
            # パターンB: LIVE EXIT SHORT CODE
            match_old = re.search(r"EXIT\s+(BUY|SHORT)[:\s]+([0-9A-Z]{4})", line)

            if match_new:
                is_exit_line = True
                exit_code = match_new.group(1)
                exit_side = match_new.group(2)
            elif match_old:
                is_exit_line = True
                exit_side = match_old.group(1)
                exit_code = match_old.group(2)

            if is_exit_line and exit_code:
                debug_exits += 1
                
                # --- PnL抽出ロジック ---
                pnl_found = False
                
                # 1. 金額直接表記を探す (¥..., ...円, pnl=...)
                pnl_match = re.search(r"¥([+,-]?[\d,]+)", line)
                if not pnl_match: pnl_match = re.search(r"([+,-]?[\d,]+)円", line)
                if not pnl_match: pnl_match = re.search(r"pnl=([+,-]?[\d,]+)", line)

                if pnl_match:
                    try: 
                        pnl = float(pnl_match.group(1).replace(",", ""))
                        pnl_found = True
                    except: pass
                
                # 2. パーセント表記から計算 (TP(+1.54%) など)
                if not pnl_found:
                    # ここを修正！ +記号を許容するように [\+\-0-9\.]+ に変更
                    pct_match = re.search(r"\(([\+\-]?[\d\.]+)%\)", line)
                    if pct_match and exit_code in open_positions:
                        pct = float(pct_match.group(1))
                        entry_price = open_positions[exit_code]["price"]
                        # 利益率がプラスなら利益、マイナスなら損失として計算
                        # (空売りの場合も、ログが「利益率」で出ているならそのまま掛ければOK)
                        pnl = entry_price * DEFAULT_QTY * (pct / 100.0)
                        pnl_found = True

                # 理由抽出
                reason_match = re.search(r"\(([^)]+)\)", line)
                if reason_match: reason = reason_match.group(1)

                hold_min = 0.0
                if exit_code in open_positions:
                    start_dt = open_positions[exit_code]["time"]
                    hold_min = round((current_dt - start_dt).total_seconds() / 60, 1)
                    del open_positions[exit_code]
                
                daily_pnl += pnl
                if pnl > 0: wins += 1
                elif pnl < 0: losses += 1
                else: draws += 1
                
                if exit_code not in symbol_pnl:
                    symbol_pnl[exit_code] = 0.0; symbol_count[exit_code] = 0
                symbol_pnl[exit_code] += pnl
                symbol_count[exit_code] += 1

                trades.append({
                    "Time": time_str, "Code": exit_code, "Side": exit_side, 
                    "Reason": reason, "PnL": pnl, "HoldMin": hold_min
                })

    print(f"   ℹ️ 検出数: ENTRY={debug_entries}, EXIT={debug_exits}")
    return trades, daily_pnl, wins, losses, draws, symbol_pnl, symbol_count

def main():
    ensure_dir(REPORT_DIR)
    print("🔍 最新のログファイルを探しています...")
    log_file = load_latest_log()
    if not log_file: print("\n❌ ログファイルが見つかりません。"); return

    trades, total_pnl, w, l, d, s_pnl, s_cnt = analyze(log_file)
    
    if not trades:
        print(f"⚠️ 決済記録なし: {os.path.basename(log_file)}")
        return

    lines = []
    lines.append("="*80)
    lines.append(f"📊 分析レポート (v2.13 Fix Percent)")
    lines.append(f"   対象: {os.path.basename(log_file)}")
    lines.append("="*80)
    lines.append(f"{'時刻':<10} | {'CODE':<6} | {'売買':<5} | {'理由':<20} | {'保有':<6} | {'損益 (円)':>10}")
    lines.append("-" * 80)
    
    hold_times = [t['HoldMin'] for t in trades if t['HoldMin'] > 0]
    for t in trades:
        mark = "○" if t['PnL'] > 0 else "●" if t['PnL'] < 0 else " "
        h_str = f"{t['HoldMin']}m" if t['HoldMin'] > 0 else "-"
        reason = t['Reason'][:20]
        # 見やすいように桁区切り
        lines.append(f"{t['Time']:<10} | {t['Code']:<6} | {t['Side']:<5} | {reason:<20} | {h_str:<6} | {mark} {t['PnL']:>10,.0f}")

    lines.append("-" * 80)
    total = w + l + d
    win_rate = (w / total * 100) if total > 0 else 0.0
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0
    
    lines.append(f"💰 合計損益:  {total_pnl:+,.0f} 円")
    lines.append(f"📊 勝率       :  {win_rate:.1f}% ({w}勝 {l}敗 {d}分)")
    lines.append(f"⏱️ 平均保有:  {avg_hold:.1f} 分")
    lines.append("-" * 80)
    
    lines.append("【銘柄別成績】")
    for code, pnl in sorted(s_pnl.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  - {code}: {pnl:+,.0f} 円 ({s_cnt[code]}回)")
    lines.append("=" * 80)

    report_text = "\n".join(lines)
    print(report_text)

    # ファイル名生成
    safe_name = re.sub(r'[\\/*?:"<>|]', "", os.path.basename(log_file))
    base_name = safe_name.replace("strategy_", "").replace(".log", "")
    ts = datetime.now().strftime("%H%M%S")
    out_file = os.path.join(REPORT_DIR, f"report_{base_name}_{ts}.txt")
    
    with open(out_file, "w", encoding="utf-8") as f: f.write(report_text)
    print(f"\n💾 保存: {out_file}")

if __name__ == "__main__":
    main()