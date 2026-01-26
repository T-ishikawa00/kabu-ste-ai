# -*- coding: utf-8 -*-
"""
Log Analyzer v2.6 (The Highlander)
==================================
【最終完全版】
「買い(ENTRY)が見当たらないのに売り(EXIT)がある」場合、
その銘柄については【最初の1回だけ】を有効とし、
それ以降の孤立した決済は、時間に関係なく全て無視します。

これにより、無限に続く決済エラー連打を確実に「1回」に絞り込みます。
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

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def load_latest_log():
    all_files = []
    for d in SEARCH_DIRS:
        patterns = [os.path.join(d, "strategy_*.log"), os.path.join(d, "*.log")]
        for p in patterns:
            found = glob.glob(p)
            all_files.extend(found)
    
    all_files = list(set(all_files))
    if not all_files: return None
    return max(all_files, key=os.path.getctime)

def analyze(log_file):
    trades = []
    
    # 進行中の建玉 { "6954": datetime(entry_time) }
    open_positions = {} 
    
    # ★ハイランダー・フィルタ: 既に「孤立した決済」を処理した銘柄を記録
    # ここに入っている銘柄が、再びENTRYなしでEXITしようとしたら無視する
    processed_orphans = set()

    daily_pnl = 0.0
    wins, losses, draws = 0, 0, 0
    symbol_pnl = {}
    symbol_count = {}

    print(f"📂 読み込み成功: {log_file}")

    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if "LIVE" not in line and "PAPER" not in line: continue

            # --- 1. 時刻抽出 ---
            current_dt = None
            try:
                ts_match = re.search(r"(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})", line)
                if ts_match:
                    current_dt = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
            except: pass
            if current_dt is None: continue
            time_str = current_dt.strftime("%H:%M:%S")

            # --- 2. ENTRY ---
            entry_match = re.search(r"(?:PAPER|LIVE)\s+(?:BUY|SHORT)\s+([0-9]{3}[0-9A-Z])", line)
            if entry_match:
                code = entry_match.group(1)
                open_positions[code] = current_dt
                # 正式なエントリーがあったので、孤立フラグを解除（また取引できる）
                if code in processed_orphans:
                    processed_orphans.discard(code)

            # --- 3. EXIT ---
            if ("EXIT" in line) and ("pnl=" in line):
                code_match = re.search(r"(?:EXIT|EXIT_EXEC)\s+(?:BUY|SHORT|SELL|COVER)?\s*([0-9]{3}[0-9A-Z])", line)
                if not code_match: continue
                code = code_match.group(1)

                is_valid_trade = False
                hold_min = 0.0

                # A. エントリー記録がある (正常な決済)
                if code in open_positions:
                    is_valid_trade = True
                    start_dt = open_positions.pop(code)
                    hold_min = round((current_dt - start_dt).total_seconds() / 60, 1)
                
                # B. エントリー記録がない (ゾンビの可能性大)
                else:
                    # ★ここで判定: すでに一度でも「孤立決済」を許したか？
                    if code in processed_orphans:
                        # 2回目以降のゾンビは問答無用で無視！
                        continue 
                    
                    # 最初の1回だけは許す（途中起動の決済かもしれないので）
                    is_valid_trade = True
                    hold_min = 999.9
                    processed_orphans.add(code) # フラグを立てる

                if is_valid_trade:
                    # 損益抽出
                    pnl = 0.0
                    pnl_match = re.search(r"pnl=([-\d\.,]+)", line)
                    if pnl_match:
                        try: pnl = float(pnl_match.group(1).replace(",", ""))
                        except: pass

                    reason = "-"
                    rm = re.search(r"reason=(\w+)", line)
                    if rm: reason = rm.group(1)

                    # 集計
                    daily_pnl += pnl
                    if pnl > 0: wins += 1
                    elif pnl < 0: losses += 1
                    else: draws += 1
                    
                    if code not in symbol_pnl:
                        symbol_pnl[code] = 0.0; symbol_count[code] = 0
                    symbol_pnl[code] += pnl
                    symbol_count[code] += 1

                    trades.append({
                        "Time": time_str, "Code": code, "Reason": reason,
                        "PnL": pnl, "HoldMin": hold_min
                    })

    return trades, daily_pnl, wins, losses, draws, symbol_pnl, symbol_count

def main():
    ensure_dir(REPORT_DIR)
    print("🔍 最新のログファイルを探しています...")
    log_file = load_latest_log()
    if not log_file: print("\n❌ ログファイルが見つかりません。"); return

    trades, total_pnl, w, l, d, s_pnl, s_cnt = analyze(log_file)
    if not trades: print(f"⚠️ 決済記録なし: {os.path.basename(log_file)}"); return

    lines = []
    lines.append("="*70)
    lines.append(f"📊 分析レポート (v2.6 Highlander)")
    lines.append(f"   対象: {os.path.basename(log_file)}")
    lines.append("="*70)
    lines.append(f"{'時刻':<10} | {'CODE':<6} | {'理由':<10} | {'保有(分)':<8} | {'損益 (円)':>10}")
    lines.append("-" * 70)
    
    hold_times = [t['HoldMin'] for t in trades if t['HoldMin'] != 999.9]
    for t in trades:
        mark = "○" if t['PnL'] > 0 else "●" if t['PnL'] < 0 else " "
        h_str = f"{t['HoldMin']}m" if t['HoldMin'] != 999.9 else "-"
        lines.append(f"{t['Time']:<10} | {t['Code']:<6} | {t['Reason']:<10} | {h_str:<8} | {mark} {t['PnL']:>10,.0f}")

    lines.append("-" * 70)
    total = w + l + d
    win_rate = (w / total * 100) if total > 0 else 0.0
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0
    
    lines.append(f"💰 合計損益:  {total_pnl:+,.0f} 円")
    lines.append(f"📊 勝率      :  {win_rate:.1f}% ({w}勝 {l}敗 {d}分)")
    lines.append(f"⏱️ 平均保有:  {avg_hold:.1f} 分")
    lines.append("-" * 70)
    
    lines.append("【銘柄別成績】")
    for code, pnl in sorted(s_pnl.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  - {code}: {pnl:+,.0f} 円 ({s_cnt[code]}回)")
    lines.append("=" * 70)

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