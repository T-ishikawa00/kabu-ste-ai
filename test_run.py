import subprocess
import time
import pyautogui
from auth_login import auto_input_otp

# パスはあなたの環境に合わせてください
KABU_STATION_PATH = r"C:\Users\PUBLIC_USER\AppData\Local\kabuStation\KabuS.exe"

def test_sequence():
    print("🧪 深夜の動作テストを開始します...")
    
    # 1. Kabuステーション起動
    print(f"🚀 Kabuステーションを起動します...")
    subprocess.Popen(KABU_STATION_PATH)
    
    # 2. 起動待ち（夜は少し早めに設定）
    print("⏳ 起動を待っています (20秒)... その間にスマホのメール準備をして！")
    print("   ※スマホで件名「認証」、本文「123456」と打ったメールを下書きしておいてください")
    time.sleep(20)

    # 3. ログインボタンを押す（フリ）
    print("👉 ログインボタン（Enter）を押します")
    # 画面を一番手前にするために一度クリックを入れる
    try:
        # 画面中央あたりをクリックしてフォーカスを当てる（解像度に合わせて調整不要なへそをクリック）
        pyautogui.click(x=960, y=540) 
    except:
        pass
    pyautogui.press('enter')

    # 4. メール受信テスト開始
    print("\n⚡ 【重要】今すぐスマホから自分宛てにメールを送信してください！")
    print("   件名: 認証")
    print("   本文: テストコードは 777777 です")
    print("   (30秒以内に送って！)\n")

    # 5. メールを探しに行って入力する
    if auto_input_otp():
        print("\n🎉 テスト成功！")
        print("   PCが勝手に起動し、勝手にメールを読み、勝手に入力しました。")
        print("   明日の朝は、これを本番メールに対応させるだけでOKです。")
    else:
        print("\n❌ テスト失敗...")
        print("   メールが見つからなかったか、入力できませんでした。")

if __name__ == "__main__":
    test_sequence()