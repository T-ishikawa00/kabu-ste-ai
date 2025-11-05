# -*- coding: utf-8 -*-
"""
safety_monitor.py
- trade_bot.py の補助モジュール
- エラー検知・自動ブレーカー
- kill.switch ファイルで停止命令を出す
"""

import os, json, time, logging
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
def now(): return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

STATE_PATH = "runtime/state.json"
KILL_PATH = "kill.switch"
LOG_PATH = "logs/safety.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

class SafetyMonitor:
    def __init__(self):
        self.last_ts = None
        self.fail_count = 0
        self.latency_limit_ms = 800
        self.max_failures = 3

    def check_state(self):
        if not os.path.exists(STATE_PATH):
            logging.warning("state.json not found")
            return False

        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        latency = data.get("avg_latency_ms", 0)
        symbols = data.get("symbols", [])
        ts = data.get("ts", "?")

        # 状態更新チェック
        if self.last_ts == ts:
            logging.warning("⚠ state.jsonが更新されていません（停止中の可能性）")
            return False
        self.last_ts = ts

        # 遅延チェック
        if latency > self.latency_limit_ms:
            self.fail_count += 1
            logging.warning(f"⚠ API遅延 {latency:.1f}ms（{self.fail_count}/{self.max_failures}）")
        else:
            self.fail_count = max(self.fail_count - 1, 0)

        # エラー判定
        if self.fail_count >= self.max_failures:
            self.trigger_kill("API応答遅延が連続発生")
            return False

        # 含み損チェック
        total_unreal = sum([s.get("unrealized", 0) for s in symbols])
        if total_unreal < -10000:  # 最大損失許容
            self.trigger_kill("日次損失上限超過")
            return False

        return True

    def trigger_kill(self, reason):
        with open(KILL_PATH, "w", encoding="utf-8") as f:
            f.write(f"KILLED {now()} REASON: {reason}\n")
        logging.error(f"🚨 ブレーカー作動: {reason}")

def main():
    mon = SafetyMonitor()
    logging.info("🛡 Safety Monitor 起動中...")
    while True:
        try:
            if not mon.check_state():
                logging.warning("取引監視を一時停止中...")
            time.sleep(5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Monitor error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
