# wakeup.py
import time
import datetime
import subprocess
import sys

# ★設定: 何時何分に起動するか
TARGET_TIME = "08:50"

print(f"⏰ 現在待機中... {TARGET_TIME} になったら main.py を起動します。")
print("⚠️ 注意: PCがスリープしないように電源設定を確認してください！")

while True:
    # 現在時刻を取得 (HH:MM)
    now_str = datetime.datetime.now().strftime("%H:%M")

    # 時間になったら起動
    if now_str == TARGET_TIME:
        print(f"🚀 時間になりました！ {now_str} - 自動売買システムを起動します！")
        
        # main.py を起動する
        # ※同じフォルダにある main.py を実行します
        try:
            subprocess.run([sys.executable, "main.py"], check=True)
        except KeyboardInterrupt:
            print("停止しました。")
        except Exception as e:
            print(f"エラーが発生しました: {e}")
        
        break # main.py が終わったらこっちも終わる

    # まだ時間じゃなければ、10秒寝て待つ
    time.sleep(10)