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

# 1. Kabuステーションの場所 (さっき調べたパスに書き換えてください！)
# 例: r"C:\Users\あなたの名前\AppData\...\KabuStation.exe"
KABU_STATION_PATH = r"C:\Users\wawaw\AppData\Local\kabuStation\KabuS.exe"

# 2. Gmail設定 (ファイル名は変えないでOK)
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.pickle'

# ==========================================
# Google API 関連の関数
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
    """kabu.comからのメールを探してコードを返す"""
    service = get_gmail_service()
    
    # ★本番用検索条件: kabu.comからで、件名に「認証」が含まれるメール
    query = 'from:kabu.com subject:認証' 
    
    results = service.users().messages().list(userId='me', q=query, maxResults=1).execute()
    messages = results.get('messages', [])

    if not messages:
        return None

    msg = service.users().messages().get(userId='me', id=messages[0]['id']).execute()
    snippet = msg.get('snippet', '')
    
    # 6桁の数字を抽出
    match = re.search(r'(\d{6})', snippet)
    if match:
        return match.group(1)
    return None

# ==========================================
# メイン処理 (あなたの指定した4手順)
# ==========================================
def main():
    print("🚀 自動ログインを開始します")

    # 手順1：Kabuステを開く
    print("1. Kabuステーションを起動")
    try:
        subprocess.Popen(KABU_STATION_PATH)
    except FileNotFoundError:
        print("❌ パスが間違っています。コードの17行目を直してください。")
        return

    # 起動待ち (PCの性能に合わせて調整。長めに30秒)
    time.sleep(30)

    # 手順2：ボタンを一回クリックする (ログインボタン)
    print("2. ログインボタンを押下 (Enter)")
    # 念のためウィンドウをアクティブにするクリック
    pyautogui.click(x=960, y=540) 
    time.sleep(1)
    pyautogui.press('enter')

    # 手順3：GoogleAPIを使ってコードを検知する
    print("3. メール認証コードを待機中...")
    
    otp_code = None
    # 3秒おきに20回確認（計60秒待つ）
    for i in range(20):
        otp_code = find_latest_otp()
        if otp_code:
            print(f"   ✅ コード検知: {otp_code}")
            break
        print(f"   ...メール受信待ち ({i+1}/20)")
        time.sleep(3)
    
    if not otp_code:
        print("❌ タイムアウト: メールが来ませんでした")
        return

    # 手順4：検知したコードを入力してクリックをする
    print("4. コード入力＆送信")
    # 入力欄にフォーカスを合わせるため一回クリック
    pyautogui.click(x=960, y=540)
    time.sleep(1)
    
    # 数字を入力
    pyautogui.typewrite(otp_code)
    time.sleep(1)
    
    # 決定ボタンをクリック (Enter)
    pyautogui.press('enter')
    
    print("🎉 完了。ログイン処理を見守ってください")

if __name__ == "__main__":
    main()