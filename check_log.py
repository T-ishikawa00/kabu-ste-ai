import glob
import os

# 最新のログファイルを探す
files = glob.glob("logs/strategy_*.log")
if not files:
    print("❌ ログファイルが見つかりません")
    exit()

latest_log = max(files, key=os.path.getmtime)
print(f"📂 チェック対象: {latest_log}")
print("-" * 60)

# 7011の決済行をすべて表示する
with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        # 7011を含み、かつ EXIT（決済）の行を探す
        if "7011" in line and ("EXIT" in line or "Exit" in line):
            print(line.strip())

print("-" * 60)