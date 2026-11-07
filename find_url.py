import requests

# ★API Key
MY_API_KEY = ""

def find_correct_url():
    print("🔍 J-Quants 1分足の場所を探せ！")
    print("-" * 40)
    
    headers = {"x-api-key": MY_API_KEY}
    
    # 試すURL候補リスト
    candidates = [
        # 候補1: V2の標準的な書き方
        ("https://api.jquants.com/v2/equities/bars/1minute", "72030"),
        # 候補2: V1と同じ名前のV2版
        ("https://api.jquants.com/v2/prices/prices_am_one_minute_quotes", "7203"),
        # 候補3: 別の可能性 (intraday)
        ("https://api.jquants.com/v2/equities/prices/intraday", "72030"),
    ]

    for url, code in candidates:
        print(f"📡 試行: {url}")
        print(f"   Code: {code}")
        try:
            params = {"code": code, "from": "20240104", "to": "20240105"}
            res = requests.get(url, headers=headers, params=params)
            
            print(f"   👉 Status: {res.status_code}")
            
            if res.status_code == 200:
                print("   🎉 ビンゴ！ここです！")
                print(f"   📦 中身: {res.text[:100]}...")
                return # 見つかったら終了
            elif res.status_code == 404:
                print("   ❌ 404 Not Found (場所が違う)")
            elif res.status_code == 403:
                print("   ⛔ 403 Forbidden (権限がない/使えない)")
            else:
                print(f"   ⚠️ {res.status_code}")
                
        except Exception as e:
            print(f"   Error: {e}")
        print("-" * 20)

    print("\n💡 もし全部ダメなら、Standardプランでも『API Key』では1分足が取れない仕様の可能性があります。")
    print("   その場合は『リフレッシュトークン』を使うしかありません。")

if __name__ == "__main__":
    find_correct_url()