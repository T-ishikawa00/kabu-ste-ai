import time
from datetime import datetime

def wait_until_target_time(target_hour, target_minute):
    print(f"🕒 現在時刻を確認中... {target_hour}:{target_minute:02d} になるまで待機します。")
    
    while True:
        now = datetime.now()
        
        # 指定時間を過ぎていればループを抜ける（＝スタート！）
        if (now.hour > target_hour) or (now.hour == target_hour and now.minute >= target_minute):
            print(f"\n⏰ {target_hour}:{target_minute:02d} になりました！処理を開始します！")
            break
        
        # まだなら現在の時間を表示して待つ
        print(f"\r   💤 待機中... 現在 {now.hour}:{now.minute:02d}:{now.second:02d}", end="")
        time.sleep(1) # 1秒ごとにチェック

if __name__ == "__main__":
    # ここで時間を指定（8時30分）
    wait_until_target_time(8, 30)