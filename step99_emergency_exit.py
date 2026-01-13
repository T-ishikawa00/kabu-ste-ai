import sys
import yaml
import time
from kabus_api import KabusAPI

class ConfigWrapper:
    def __init__(self, data):
        for key, value in data.items():
            if isinstance(value, dict):
                setattr(self, key, ConfigWrapper(value))
            else:
                setattr(self, key, value)
    
    def __getattr__(self, name):
        return None

def main():
    print("🚨 緊急脱出シーケンス (パラメータ修正版) を起動します...")

    # 1. Config読み込み
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
            cfg = ConfigWrapper(raw_config)
    except Exception as e:
        print(f"❌ Config読み込み失敗: {e}")
        return

    # 2. API接続
    try:
        api = KabusAPI(cfg)
        api.auth()
        if not api.token:
            print("❌ トークン取得失敗")
            return
        print("✅ API認証成功")
    except Exception as e:
        print(f"❌ API接続エラー: {e}")
        return

    # 3. 建玉取得
    try:
        print("🔍 保有建玉を検索中...")
        positions = api.positions()
        if positions is None:
            print("❌ 建玉取得失敗 (API応答なし)")
            return
            
        valid_positions = [p for p in positions if p['LeavesQty'] > 0]
        
    except Exception as e:
        print(f"❌ エラー: {e}")
        return

    if not valid_positions:
        print("🎉 保有建玉なし。完了です。")
        return

    print(f"⚠️ {len(valid_positions)} 個の建玉を決済します...")
    time.sleep(1)

    # 4. 決済ループ
    for pos in valid_positions:
        try:
            code = pos.get('Symbol')
            side = str(pos.get('Side'))
            # ★修正1: 小数点(100.0)が来ても強制的に整数(100)にする
            qty = int(float(pos.get('LeavesQty'))) 
            trade_type = str(pos.get('TradeType')) # 2:現物, 4:信用
            hold_id = pos.get('HoldID')
            exchange = pos.get('Exchange', 1)

            exit_side = "2" if side == "1" else "1"
            side_str = "買戻" if exit_side == "2" else "売却"

            print(f"   🚀 {code}: {qty}株 を【{side_str}】します...")
            
            # 注文パスワード
            pwd = cfg.api.trade_password
            
            # --- ペイロード作成 ---
            payload = {
                "Password": pwd,
                "Symbol": code,
                "Exchange": int(exchange),
                "SecurityType": 1,
                "Side": exit_side,
                "AccountType": 4, # 特定口座
                "Qty": qty,
                "Price": 0,
                "FrontOrderType": 10, # 成行
                "ExpireDay": 0,
                "FundType": "11"  # ★修正2: これがないと400エラーになる
            }

            # 信用返済
            if trade_type == "4": 
                payload["CashMargin"] = 3
                payload["MarginTradeType"] = 3
                payload["DelivType"] = 2
                payload["ClosePositionOrder"] = 1
                payload["ClosePositions"] = [{'HoldID': hold_id, 'Qty': qty}]
            
            # 現物売却
            elif trade_type == "2":
                payload["CashMargin"] = 1
                payload["MarginTradeType"] = 0
                payload["DelivType"] = 0
                payload["ClosePositionOrder"] = 0

            # 送信
            res = api.sendorder(payload)

            if res and 'Result' in res and res['Result'] == 0:
                print("      ✅ 注文成功")
            else:
                print(f"      ❌ 注文失敗: {res}")
            
        except Exception as e:
            print(f"      ❌ エラー: {e}")

    print("🏁 処理終了。残高を確認してください。")

if __name__ == "__main__":
    main()