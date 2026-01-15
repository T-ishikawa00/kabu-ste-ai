# -*- coding: utf-8 -*-
"""
Log Analyzer v2.1 (Alpha-Numeric Code Support)
==============================================
変更点:
- 正規表現を修正し、数字4桁だけでなく
  英数字を含むコード (例: 285A) も正しく抽出できるように対応
"""

import os
import glob
import re
import pandas as pd
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
        patterns = [
            os.path.join(d, "strategy_*.log"),
            os.path.join(d, "*.log"),
        ]
        for p in patterns:
            found = glob.glob(p)
            all_files.extend(found)
    
    all_files = list(set(all_files))
    if not all_files: return None
    return max(all_files, key=os.path.getctime)

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
            # LIVE または PAPER の行のみ対象
            if "LIVE" not in line and "PAPER" not in line: continue

            # --- 1. 時刻抽出 ---
            current_dt = None
            try:
                # ログの先頭にある日時 (YYYY-MM-DD HH:MM:SS) を抽出
                ts_match = re.search(r"(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})", line)
                if ts_match:
                    current_dt = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
            except: pass
            
            if current_dt is None: continue
            time_str = current_dt.strftime("%H:%M:%S")

            # --- 2. ENTRY (BUY/SHORT) ---
            # ★修正: 数字4桁だけでなく、数字3桁+英字1文字 (例: 285A) にも対応
            # パターン: "BUY 285A" または "SHORT 7011" など
            entry_match = re.search(r"(?:PAPER|LIVE)\s+(?:BUY|SHORT)\s+([0-9]{3}[0-9A-Z])", line)
            
            if entry_match:
                code = entry_match.group(1)
                open_positions[code] = current_dt

            # --- 3. EXIT (決済) ---
            # "EXIT" と "pnl=" が含まれる行を決済ログとみなす
            if "EXIT" in line and "pnl=" in line:
                # ★修正: アルファベットコード対応
                code_match = re.search(r"EXIT(?:_EXEC)?\s+(?:BUY|SHORT|SELL|COVER)?\s*([0-9]{3}[0-9A-Z])", line)
                if not code_match: continue
                
                code = code_match.group(1)

                # 損益 (カンマが入っている場合もあるので除去して変換)
                pnl = 0.0
                pnl_match = re.search(r"pnl=([-\d\.,]+)", line)
                if pnl_match:
                    try:
                        pnl_str = pnl_match.group(1).replace(",", "")
                        pnl = float(pnl_str)
                    except: pass

                # 理由
                reason = "-"
                reason_match = re.search(r"reason=(\w+)", line)
                if reason_match: reason = reason_match.group(1)

                # 保有時間
                hold_min = 0.0
                if code in open_positions:
                    start_dt = open_positions.pop(code)
                    duration = (current_dt - start_dt).total_seconds()
                    hold_min = round(duration / 60, 1)
                else:
                    # エントリーログが見つからなかった場合
                    hold_min = 999.9

                # 集計
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
                    "Time": time_str,
                    "Code": code,
                    "Reason": reason,
                    "PnL": pnl,
                    "HoldMin": hold_min
                })

    return trades, daily_pnl, wins, losses, draws, symbol_pnl, symbol_count

def main():
    ensure_dir(REPORT_DIR)
    
    print("🔍 最新のログファイルを探しています...")
    log_file = load_latest_log()
    
    if not log_file:
        print("\n❌ ログファイルが見つかりません。")
        return

    trades, total_pnl, w, l, d, s_pnl, s_cnt = analyze(log_file)
    
    if not trades:
        print(f"⚠️ {os.path.basename(log_file)} には、まだ決済(EXIT)の記録がありません。")
        return

    # --- レポート作成 ---
    lines = []
    lines.append("="*70)
    lines.append(f"📊 分析レポート (対象: {os.path.basename(log_file)})")
    lines.append("="*70)
    lines.append(f"{'時刻':<10} | {'CODE':<6} | {'理由':<10} | {'保有(分)':<8} | {'損益 (円)':>10}")
    lines.append("-" * 70)
    
    hold_times = []
    for t in trades:
        mark = "○" if t['PnL'] > 0 else "●" if t['PnL'] < 0 else " "
        h_str = f"{t['HoldMin']}m" if t['HoldMin'] != 999.9 else "-"
        if t['HoldMin'] != 999.9: hold_times.append(t['HoldMin'])
            
        lines.append(f"{t['Time']:<10} | {t['Code']:<6} | {t['Reason']:<10} | {h_str:<8} | {mark} {t['PnL']:>10,.0f}")

    lines.append("-" * 70)
    
    total_trades = w + l + d
    win_rate = (w / total_trades * 100) if total_trades > 0 else 0.0
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0
    
    lines.append(f"💰 本日合計:  {total_pnl:+,.0f} 円")
    lines.append(f"📊 勝率      :  {win_rate:.1f}% ({w}勝 {l}敗 {d}分)")
    lines.append(f"⏱️ 平均保有:  {avg_hold:.1f} 分")
    lines.append("-" * 70)
    
    lines.append("【銘柄別成績】")
    sorted_syms = sorted(s_pnl.items(), key=lambda x: x[1], reverse=True)
    for code, pnl in sorted_syms:
        cnt = s_cnt[code]
        lines.append(f"  - {code}: {pnl:+,.0f} 円 ({cnt}回)")
    lines.append("=" * 70)

    # --- 画面表示 ---
    report_text = "\n".join(lines)
    print(report_text)

    # --- ファイル保存 ---
    # ファイル名: report_YYYY-MM-DD.txt の形式にする
    # ファイル名に使えない文字が含まれている場合の対策
    safe_name = re.sub(r'[\\/*?:"<>|]', "", os.path.basename(log_file))
    base_name = safe_name.replace("strategy_", "").replace(".log", "")
    
    # タイムスタンプをつけて重複回避 (同日に何度も実行する場合のため)
    ts = datetime.now().strftime("%H%M%S")
    out_file = os.path.join(REPORT_DIR, f"report_{base_name}_{ts}.txt")
    
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report_text)
    
    print(f"\n💾 レポートを保存しました: {out_file}")

if __name__ == "__main__":
    main()