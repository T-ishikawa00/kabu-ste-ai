import glob
import os

# 最新のログを探す
files = glob.glob("logs/strategy_*.log")
if not files:
    print("❌ ログがありません")
    exit()

latest_log = max(files, key=os.path.getmtime)
print(f"📂 調査対象: {latest_log}")
print("-" * 60)

# 7011のEXITを見つけたら、その後の5行も一緒に表示する
with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()
    
    for i, line in enumerate(lines):
        if "7011" in line and ("EXIT" in line or "Exit" in line):
            print(f"▼ 発見した決済行 ({i+1}行目):")
            print(line.strip())
            
            print("▼ その後の5行:")
            for j in range(1, 6):
                if i + j < len(lines):
                    print(f"   {lines[i+j].strip()}")
            
            print("-" * 60)
            # 最初の1回だけ表示して終了（全部出すと長いので）
            break