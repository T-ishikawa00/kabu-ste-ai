import os.path
import pickle
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# 設定
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.pickle'

def main():
    print("🔍 Botが見ている受信トレイを確認します...")

    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)
    
    # 認証切れなら更新
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)

    service = build('gmail', 'v1', credentials=creds)

    # ★検索条件なしで、最新5件を強制取得
    results = service.users().messages().list(userId='me', maxResults=5).execute()
    messages = results.get('messages', [])

    if not messages:
        print("❌ メールが1件も見つかりません！")
        print("可能性：受信トレイが空か、全く使っていない別のアカウントを見ています。")
    else:
        print("👇 Botが見ている最新メール一覧 👇")
        print("------------------------------------------------")
        for msg in messages:
            txt = service.users().messages().get(userId='me', id=msg['id']).execute()
            payload = txt['payload']
            headers = payload.get('headers')
            
            subject = "（件名なし）"
            for h in headers:
                if h['name'] == 'Subject':
                    subject = h['value']
            
            snippet = txt.get('snippet', '')
            
            print(f"📧 件名: {subject}")
            print(f"   中身: {snippet[:30]}...") 
            print("------------------------------------------------")

if __name__ == '__main__':
    main()