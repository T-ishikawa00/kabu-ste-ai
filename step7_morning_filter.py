# -*- coding: utf-8 -*-
"""
step7_morning_filter.py v1.1 (Quota Guarantee)
==============================================
朝の板情報チェックで最終銘柄を絞り込む

【変更点 v1.1】
- 「定員確保モード」を追加
- 厳しい条件で5枠埋まらなかった場合、元のリスト(target_list)の上位から
  強制的に補充して、必ず5銘柄揃えるように変更。
"""

import os
import sys
import yaml
import pandas as pd
import requests
import time
import logging
from datetime import datetime

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ==========================================
# 設定
# ==========================================
TARGET_FILE = "target_list.csv"
CONFIG_FILE = "config.yaml"
FINAL_COUNT = 7  # 最終選定数

# kabuステーションAPI
API_BASE = "http://localhost:18080/kabusapi"
API_PASSWORD = "REDACTED"

# 判定基準 (1次審査)
MIN_OBI = 0.45           # 最低買い板比率
MAX_SPREAD_PCT = 0.5     # 最大スプレッド（%）


class KabuAPI:
    def __init__(self):
        self.token = None
        self.session = requests.Session()
    
    def login(self):
        try:
            res = self.session.post(
                f"{API_BASE}/token",
                json={"APIPassword": API_PASSWORD}
            )
            if res.status_code == 200:
                self.token = res.json().get("Token")
                self.session.headers.update({"X-API-KEY": self.token})
                logging.info("🔑 API認証成功")
                return True
            else:
                logging.error(f"❌ API認証失敗: {res.status_code}")
                return False
        except Exception as e:
            logging.error(f"❌ API接続エラー: {e}")
            return False
    
    def get_board(self, code, exchange=1):
        try:
            symbol = f"{code}@{exchange}"
            res = self.session.get(f"{API_BASE}/board/{symbol}")
            if res.status_code == 200:
                return res.json()
            return None
        except Exception:
            return None

def analyze_board(board_data):
    """板情報を分析（不合格でもスコアを計算する仕様に変更）"""
    if not board_data:
        return {"pass": False, "reason": "データなし", "score": -999}
    
    try:
        current_price = board_data.get("CurrentPrice", 0)
        
        sell1 = board_data.get("Sell1", {})
        buy1 = board_data.get("Buy1", {})
        ask_price = sell1.get("Price", 0)
        bid_price = buy1.get("Price", 0)
        
        if ask_price == 0 or bid_price == 0:
            return {"pass": False, "reason": "気配なし", "score": -999}
        
        # 基準価格（現在値がなければ気配値の仲値）
        base_price = current_price if current_price > 0 else (ask_price + bid_price) / 2
        if base_price == 0: base_price = 1 # ゼロ除算回避
        
        spread = ask_price - bid_price
        spread_pct = spread / base_price * 100
        
        # OBI計算
        total_bid = 0
        total_ask = 0
        for i in range(1, 6):
            total_bid += board_data.get(f"Buy{i}", {}).get("Qty", 0)
            total_ask += board_data.get(f"Sell{i}", {}).get("Qty", 0)
        
        if total_bid + total_ask == 0:
            return {"pass": False, "reason": "板薄", "score": -999}
        
        obi = total_bid / (total_bid + total_ask)
        score = obi * 100 - spread_pct * 10
        
        # 審査判定
        is_pass = True
        fail_reason = ""
        
        if spread_pct > MAX_SPREAD_PCT:
            is_pass = False
            fail_reason = f"Spread過大({spread_pct:.1f}%)"
        elif obi < MIN_OBI:
            is_pass = False
            fail_reason = f"売圧強({obi:.2f})"
            
        return {
            "pass": is_pass,
            "reason": fail_reason,
            "obi": round(obi, 3),
            "spread_pct": round(spread_pct, 2),
            "score": round(score, 2)
        }
    except Exception as e:
        return {"pass": False, "reason": str(e), "score": -999}

def load_candidates():
    if not os.path.exists(TARGET_FILE): return []
    df = pd.read_csv(TARGET_FILE)
    if df.empty: return []
    
    # 5桁補正してリスト化
    codes = []
    for c in df['code'].astype(str).tolist():
        codes.append(c[:-1] if (len(c)==5 and c.endswith('0')) else c)
    return codes

def update_config(selected_codes):
    if not os.path.exists(CONFIG_FILE): return False
    # CSV更新
    df = pd.DataFrame({"code": selected_codes})
    df.to_csv(TARGET_FILE, index=False)
    # 注入実行
    import subprocess
    subprocess.run([sys.executable, "step6_inject_config.py"], capture_output=True)
    return True

def main():
    print("=" * 60)
    print(f"🌅 朝の板チェック v1.1 (定員確保モード)")
    print("=" * 60)
    
    candidates = load_candidates()
    if not candidates:
        print("❌ 候補なし。night_batchを実行してください。")
        return

    api = KabuAPI()
    if not api.login():
        print("❌ API接続失敗")
        return
    
    # 1. 全候補をチェック
    passed_list = []   # 合格リスト
    failed_list = []   # 不合格リスト（予備軍）
    
    print(f"🔍 {len(candidates)}銘柄の気配値をスキャン中...")
    
    for code in candidates:
        board = api.get_board(code)
        res = analyze_board(board)
        
        data = {
            "code": code,
            "obi": res.get("obi", 0),
            "spread": res.get("spread_pct", 0),
            "score": res.get("score", -999),
            "reason": res.get("reason", "")
        }
        
        if res["pass"]:
            passed_list.append(data)
            print(f"   ✅ 合格: {code} (OBI={data['obi']})")
        else:
            failed_list.append(data)
            print(f"   ⚠️ 保留: {code} -> {data['reason']}")
            
        time.sleep(0.2)
    
    # 2. 選抜ロジック（定員確保）
    # まず合格組をスコア順に
    passed_list.sort(key=lambda x: x["score"], reverse=True)
    
    final_selection = []
    
    # ステップA: 合格組を採用
    final_selection.extend(passed_list[:FINAL_COUNT])
    
    # ステップB: 定員割れなら、不合格組から「マシなやつ」を補充
    if len(final_selection) < FINAL_COUNT:
        shortage = FINAL_COUNT - len(final_selection)
        print(f"\n📢 定員割れ発生: 残り{shortage}枠を予備軍から補充します...")
        
        # 不合格組もスコア順（OBIが高い順など）に並べる
        failed_list.sort(key=lambda x: x["score"], reverse=True)
        
        for fail_item in failed_list:
            if len(final_selection) >= FINAL_COUNT:
                break
            # スコアが-999（データなし）はさすがに除外
            if fail_item["score"] == -999: continue
            
            final_selection.append(fail_item)
            print(f"     ➕ 復活当選: {fail_item['code']} (理由: {fail_item['reason']} だが採用)")
            
    # 3. 最終結果
    print("-" * 60)
    print(f"🎯 本日の出場選手 ({len(final_selection)}銘柄)")
    for i, r in enumerate(final_selection):
        status = "✅" if r in passed_list else "➕"
        print(f"{i+1:<2} {status} {r['code']}  OBI:{r['obi']:.2f}  Spread:{r['spread']:.1f}%")

    # Config更新
    codes_only = [x["code"] for x in final_selection]
    if codes_only:
        update_config(codes_only)
    else:
        print("❌ 全銘柄データなしのため更新スキップ")
        
    print("=" * 60)

if __name__ == "__main__":
    main()