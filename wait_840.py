# wait_840.py - 8:40まで待機
import time
import datetime

TARGET = "08:40"

print(f"⏰ {TARGET} まで待機中...")

while True:
    now = datetime.datetime.now().strftime("%H:%M")
    if now >= TARGET:
        print(f"✅ {now} - 板チェック開始します")
        break
    time.sleep(5)