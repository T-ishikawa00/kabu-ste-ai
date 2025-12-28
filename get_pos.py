import pyautogui
import time

print("⚡ 座標取得ツール起動")
print("3秒以内に、マウスカーソルを「ログインボタンのど真ん中」に置いて動かさないでください！")

for i in range(3, 0, -1):
    print(f"{i}...")
    time.sleep(1)

x, y = pyautogui.position()
print("\n👇 この数字をメモしてください！")
print(f"X={x}, Y={y}")
print("----------------------------")