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

def analyze(log_file):
    trades = []
    # ★復活: エントリー時間を覚えておくための辞書
    open_positions = {} 
    
    total_pnl = 0.0
    wins = 0
    losses = 0
    draws = 0
    symbol_pnl = {}
    symbol_count = {}

    print(f"📂 解析対象: {log_file}")
    
    # ログ読み込み
    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            
            # --- 共通: 時刻の抽出 ---
            # 行頭の時刻 (例: 2026-01-05 09:00:00,123 または 09:00:00) を取得
            current_dt = None
            try:
                # 日付+時刻のパターン
                ts_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if ts_match:
                    current_dt = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                else:
                    # 時刻のみのパターン (昨日の日付を補完)
                    t_match = re.search(r"(\d{2}:\d{2}:\d{2})", line)
                    if t_match:
                        # ファイル名などから日付が特定できればベストだが、ここでは「昨日」と仮定
                        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                        current_dt = datetime.strptime(f"{yesterday_str} {t_match.group(1)}", "%Y-%m-%d %H:%M:%S")
            except:
                pass

            if current_dt is None: continue # 時刻が取れない行は無視
            time_str = current_dt.strftime("%H:%M:%S")

            # --- A. エントリー (LIVE BUY / LIVE SHORT) を検知して時間を記録 ---
            # "LIVE BUY 7203" などを探す
            if ("LIVE BUY" in line or "LIVE SHORT" in line) and "EXIT" not in line:
                code_match = re.search(r"LIVE (?:BUY|SHORT) (\w+)", line)
                if code_match:
                    code = code_match.group(1)
                    # 辞書に「開始時刻」を保存 (上書き)
                    open_positions[code] = current_dt

            # --- B. イグジット (LIVE EXIT ... pnl=...) を検知して計算 ---
            if "LIVE EXIT" in line and "pnl=" in line:
                
                # 1. 銘柄コード
                code = "UNKNOWN"
                code_match = re.search(r"LIVE EXIT \w+ (\w+)", line)
                if code_match: code = code_match.group(1)
                
                # 2. 売買方向
                side = "BUY"
                if "SELL" in line: side = "SELL" # 実際には決済方向だが、表記用

                # 3. 理由
                reason = "UNKNOWN"
                reason_match = re.search(r"reason=(\w+)", line)
                if reason_match: reason = reason_match.group(1)

                # 4. 損益
                pnl = 0.0
                pnl_match = re.search(r"pnl=([-\d\.]+)", line)
                if pnl_match: pnl = float(pnl_match.group(1))

                # ★復活: 5. 保有時間の計算
                hold_min = 999.9 # 不明時の値
                if code in open_positions:
                    start_dt = open_positions.pop(code) # 時間を取り出して削除
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
                    "HoldMin": hold_min # 追加
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
        print(f"⚠️ ログファイル ({log_file}) はありましたが、トレード記録が読めませんでした。")
        return

    # === レポート出力 ===
    print("\n" + "="*80)
    print(f"📊 昨日の成績レポート (ファイル: {os.path.basename(log_file)})")
    print("="*80)
    # ★「保有(分)」のカラムを復活
    print(f"{'時刻':<10} | {'CODE':<6} | {'売買':<4} | {'理由':<8} | {'保有(分)':<8} | {'      損益 (円)'}")
    print("-" * 80)
    
    hold_times = []
    for t in trades:
        mark = "○" if t['PnL'] > 0 else "●" if t['PnL'] < 0 else " "
        
        # 保有時間の表示整形
        h_str = f"{t['HoldMin']}m" if t['HoldMin'] != 999.9 else "-"
        if t['HoldMin'] != 999.9:
            hold_times.append(t['HoldMin'])

        print(f"{t['Time']:<10} | {t['Code']:<6} | {t['Side']:<4} | {t['Reason']:<8} | {h_str:<8} | {mark} {t['PnL']:>10,.0f}")

    print("-" * 80)
    
    total_trades = w + l + d
    win_rate = (w / total_trades * 100) if total_trades > 0 else 0.0
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0
    
    print(f"💰 合計損益:  {total_pnl:+,.0f} 円")
    print(f"📊 勝率    :  {win_rate:.1f}% ({w}勝 {l}敗 {d}分)")
    print(f"⏱️ 平均保有:  {avg_hold:.1f} 分")  # 平均保有時間も表示
    print("-" * 80)
    
    print("【銘柄別成績】")
    sorted_syms = sorted(s_pnl.items(), key=lambda x: x[1], reverse=True)
    for code, pnl in sorted_syms:
        cnt = s_cnt[code]
        print(f"  - {code:<6}: {pnl:+,.0f} 円 ({cnt}回)")
    print("=" * 80)

if __name__ == "__main__":
    main()