# check_files.py
import os
import glob

print("="*40)
print("🔍 フォルダの中身調査")
print("="*40)

# 1. 今どこにいるか？
current_dir = os.getcwd()
print(f"📍 現在地: {current_dir}")

# 2. そこに何があるか？
files = os.listdir(current_dir)
print(f"📦 ファイル数: {len(files)}個")

# 3. 5020.csv はあるか？
if "5020.csv" in files:
    print("✅ 発見！: 5020.csv はここにあります！")
else:
    print("❌ 未発見: 5020.csv がこのフォルダにありません！")

# 4. 似たファイルはないか？
csvs = glob.glob("*.csv")
print(f"📂 CSV一覧: {csvs}")
print("="*40)