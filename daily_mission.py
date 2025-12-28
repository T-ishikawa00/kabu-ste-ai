import time
import subprocess
import sys
from datetime import datetime, timedelta

def wait_until_tomorrow_830():
    # 目標時間：明日の8時30分00秒
    now = datetime.now()
    target_time = now.replace(hour=8, minute=30, second=0, microsecond=0)
    
    # もし今の時間がすでに8:30を過ぎていたら、明日の8:30にセット
    if now > target_time:
        target_time = target_time + timedelta(days=1)
    
    print(f"🕒 現在時刻: {now.strftime('%H:%M:%S')}")
    print(f"🎯 目標時刻: {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("💤 8:30になるまで待機モードに入ります...（PCを閉じないでください）")

    while True:
        now = datetime.now()
        # 時間になったらループを抜ける
        if now >= target_time:
            print("\n⏰ 8:30になりました！作戦を開始します！")
            break
        
        # 残り時間を計算して表示（1分おきに更新）
        remaining = target_time - now
        # 見やすいように整形
        rem_str = str(remaining).split('.')[0]
        print(f"\r   ⏳ あと {rem_str} ...", end="")
        
        time.sleep(1) # 1秒待機

def main():
    print("🚀 デイリーミッション待機プログラム起動")
    
    # 1. 8:30までひたすら待つ
    wait_until_tomorrow_830()

    # 2. 自動ログイン (auto_login.py) を実行
    print("\n-------------------------------------------")
    print("▶️ Step 1: 自動ログインを開始します")
    print("-------------------------------------------")
    # python auto_login.py を外部コマンドとして実行
    subprocess.run([sys.executable, "auto_login.py"])

    # 3. ログイン完了待ち (20秒)
    print("\n⏳ ログイン処理の完了を待っています (20秒)...")
    time.sleep(20)

    # 4. Wakeup処理 (wakeup.py) を実行
    print("\n-------------------------------------------")
    print("▶️ Step 2: wakeup処理を開始します")
    print("-------------------------------------------")
    subprocess.run([sys.executable, "wakeup.py"])

    print("\n🎉 本日の全ミッション完了！")

if __name__ == "__main__":
    main()