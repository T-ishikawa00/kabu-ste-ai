import os.path
import pickle
import re
import time
import subprocess
import pyautogui
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ==========================================
# 設定エリア
# ==========================================
KABU_STATION_PATH = r"C:\Users\PUBLIC_USER\AppData\Local\kabuStation\KabuS.exe"

# 座標設定 (あなたの環境に合わせています)
LOGIN_BTN_X = 1218
LOGIN_BTN_Y = 656
INPUT_BOX_X = 1078
INPUT_BOX_Y = 656
OK_BTN_X = 1217
OK_BTN_Y = 722

# Gmail設定
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.pickle'

# ==========================================
# Google API
# ==========================================
def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)
    return build('gmail', 'v1', credentials=creds)

def find_latest_otp():
    service = get_gmail_service()
    
    # ★変更点: 「is:unread」を削除。件名だけで探す。
    query = 'subject:ワンタイム認証コード'
    
    try:
        # 最新の1件を取得
        results = service.users().messages().list(userId='me', q=query, maxResults=1).execute()
        messages = results.get('messages', [])
        
        if not messages:
            return None

        # メールの詳細情報を取得
        msg = service.users().messages().get(userId='me', id=messages[0]['id']).execute()
        
        # ★ここが新ロジック: 「メールの受信時刻」をチェックする
        internal_date = int(msg['internalDate']) / 1000 # ミリ秒を秒に変換
        current_time = time.time()
        
        # 「現在時刻」と「メール受信時刻」の差を計算
        diff_seconds = current_time - internal_date
        
        # デバッグ表示（今見つけたメールが何秒前のものか表示）
        # print(f"   (発見: {int(diff_seconds)}秒前のメール)")

        # 180秒（3分）以内に届いたメールじゃなければ「古い」とみなして無視する
        if diff_seconds > 180:
            return None

        # ここまで来たら「3分以内の新しいメール」なので採用
        snippet = msg.get('snippet', '')
        match = re.search(r'(\d{6})', snippet)
        return match.group(1) if match else None

    except Exception:
        return None

# ==========================================
# メイン処理
# ==========================================
def main():
    print("🚀 自動ログイン（時間判定ロジック版）を開始")

    # 1. Kabuステーションを起動
    print("1. Kabuステーションを起動")
    try:
        subprocess.Popen(KABU_STATION_PATH)
    except FileNotFoundError:
        print("❌ パスエラー")
        return

    print("⏳ 起動待機中 (30秒)...")
    time.sleep(30)

    # 2. ログインボタンをクリック
    print(f"2. ログインボタンをクリック ({LOGIN_BTN_X}, {LOGIN_BTN_Y})")
    pyautogui.click(x=LOGIN_BTN_X, y=LOGIN_BTN_Y)
    time.sleep(0.5)
    pyautogui.click(x=LOGIN_BTN_X, y=LOGIN_BTN_Y)
    
    print("   🖱️ クリック完了。メールを待機します...")

    # 3. メール認証コードを待機
    otp_code = None
    
    # 待機時間を少し延長 (3分間待つように変更)
    for i in range(60):
        # 進捗を1行で表示
        print(f"\r   ⏳ 新着メール監視中... ({i+1}/60) ", end="")
        
        otp_code = find_latest_otp()
        
        if otp_code:
            print(f"\n   ✅ 新着コード検知！: {otp_code}")
            time.sleep(3) # 画面が出るのを待つ
            break
        
        time.sleep(3)
    
    if not otp_code:
        print("\n❌ タイムアウト: 3分以内に新しいメールが見つかりませんでした。")
        return

    # 4. コード入力
    print(f"4. 入力欄({INPUT_BOX_X}, {INPUT_BOX_Y})をクリックして入力")
    
    # 入力欄を確実にクリック
    pyautogui.click(x=INPUT_BOX_X, y=INPUT_BOX_Y)
    time.sleep(0.5)
    
    # 数字を入力
    pyautogui.typewrite(otp_code)
    time.sleep(0.5)
    
    # 決定ボタンをクリック
    print(f"5. 決定ボタン({OK_BTN_X}, {OK_BTN_Y})をクリック")
    pyautogui.click(x=OK_BTN_X, y=OK_BTN_Y)
    
    print("🎉 完了。ログインを確認してください。")

if __name__ == "__main__":
    main()