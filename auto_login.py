import os.path
import pickle
import re
import time
import subprocess
import pyautogui
import socket  # 通信制御用
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ==========================================
# ★【最終奥義】IPv4 強制化パッチ
# ==========================================
orig_getaddrinfo = socket.getaddrinfo

def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

socket.getaddrinfo = getaddrinfo_ipv4
# ------------------------------------------

# ==========================================
# 設定エリア
# ==========================================
KABU_STATION_PATH = r"C:\Users\<LOCAL_USER>\AppData\Local\kabuStation\KabuS.exe"

# 座標設定
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
    
    # ★変更点: 「1分以内に受信したメール」だけを探す条件を追加
    # newer_than:1m をつけることで、古いメールを絶対に拾わなくなります
    query = 'from:kabu.com newer_than:1m'
    
    try:
        # 最新の1件を取得
        results = service.users().messages().list(userId='me', q=query, maxResults=1).execute()
        messages = results.get('messages', [])
        
        if not messages:
            # まだメールが届いていない場合はここに来る
            return None

        # メールの詳細情報を取得
        msg = service.users().messages().get(userId='me', id=messages[0]['id']).execute()
        
        snippet = msg.get('snippet', '')
        # print(f"   (メール受信データ: {snippet[:20]}...)") # デバッグ用(うるさいのでオフ)

        # 6桁の数字を抽出
        match = re.search(r'(\d{6})', snippet)
        return match.group(1) if match else None

    except Exception as e:
        print(f"   (通信エラー詳細: {e})")
        return None

# ==========================================
# メイン処理
# ==========================================
def main():
    print("🚀 自動ログイン（IPv4強制版・最新メール限定）を開始")

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
    
    print("   🖱️ クリック完了。最新のメールを待機します...")

    # 3. メール認証コードを待機
    otp_code = None
    
    max_retries = 30
    
    for i in range(max_retries):
        # 進行状況を見やすく表示
        print(f"\r   ⏳ メール確認中... ({i+1}/{max_retries}) ", end="")
        time.sleep(5) 
        otp_code = find_latest_otp()
        
        if otp_code:
            print(f"\n   ✅ 最新コード発見！: {otp_code}")
            time.sleep(2) 
            break
        
        # 次のチェックまで待つ
        time.sleep(3)
    
    if not otp_code:
        print("\n❌ タイムアウト: 5分以内に新しいメールが見つかりませんでした。")
        return

    # 4. コード入力
    print(f"4. 入力欄をクリックして入力")
    pyautogui.click(x=INPUT_BOX_X, y=INPUT_BOX_Y)
    time.sleep(0.5)
    
    pyautogui.typewrite(otp_code)
    time.sleep(0.5)
    
    # 決定ボタンをクリック
    print(f"5. 決定ボタンをクリック")
    pyautogui.click(x=OK_BTN_X, y=OK_BTN_Y)
    
    print("🎉 完了")

if __name__ == "__main__":
    main()