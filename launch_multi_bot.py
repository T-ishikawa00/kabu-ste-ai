# -*- coding: utf-8 -*-
"""
launch_multi_bot.py
===================
50銘柄を自動で「分身」に分け、並列起動して高速化するランチャー。
メモリ32GBなら、1ボットあたり10銘柄（計5体）がベストバランスです。

使い方:
  py -3.12 launch_multi_bot.py
"""

import yaml
import subprocess
import sys
import time
import math
import os

# ==========================================
# 設定
# ==========================================
CONFIG_FILE = "config.yaml"
# あなたの環境に合わせてファイル名を指定
# ※ main.py と同じ場所に controller_ws.py がある場合はこれ
CONTROLLER_SCRIPT = "controller_ws.py" 
# もし core フォルダの中にあるなら "core/controller_ws.py" に書き換えてください

# ★ 1つのボットに担当させる最大銘柄数
# 12銘柄で100msなら、安全を見て「10銘柄」くらいで区切るのがベスト
STOCKS_PER_BOT = 10 

def main():
    print(f"{'='*60}")
    print(f"🚀 マルチ・ボット・ランチャー (Shadow Clone Commander)")
    print(f"   50銘柄あっても、分身させれば遅延ゼロ。")
    print(f"{'='*60}")

    # 1. 監視リストを読み込む
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            symbols = config.get("symbols", [])
            
        # コードのリストを作る
        codes = [str(s["code"]) for s in symbols]
        total_stocks = len(codes)
        
        if total_stocks == 0:
            print("❌ 監視銘柄がありません。step6を実行しましたか？")
            return

    except Exception as e:
        print(f"❌ 設定読み込みエラー: {e}")
        return

    # 2. 銘柄を分割する（チャンク分け）
    # 例: 50銘柄 ÷ 10 = 5グループ
    num_bots = math.ceil(total_stocks / STOCKS_PER_BOT)
    chunks = [codes[i:i + STOCKS_PER_BOT] for i in range(0, total_stocks, STOCKS_PER_BOT)]

    print(f"📊 監視総数: {total_stocks} 銘柄")
    print(f"🤖 起動ボット数: {len(chunks)} 体 (1体あたり約 {STOCKS_PER_BOT} 銘柄)")
    
    # スクリプトの存在確認
    if not os.path.exists(CONTROLLER_SCRIPT):
        # coreフォルダにあるか確認
        if os.path.exists(f"core/{CONTROLLER_SCRIPT}"):
            script_path = f"core/{CONTROLLER_SCRIPT}"
        elif os.path.exists("controller_ws.py"):
             script_path = "controller_ws.py"
        else:
            print(f"❌ エラー: {CONTROLLER_SCRIPT} が見つかりません！")
            return
    else:
        script_path = CONTROLLER_SCRIPT

    print(f"📂 実行スクリプト: {script_path}")
    print(f"{'-'*60}")

    processes = []

    # 3. 分身を次々と起動する
    for i, chunk in enumerate(chunks):
        # 銘柄リストをカンマ区切りの文字列にする "9984,7011,..."
        arg_list = ",".join(chunk)
        
        print(f"   [Bot {i+1}] 起動... 担当: {len(chunk)} 銘柄 ({chunk[0]} ... {chunk[-1]})")
        
        # Windowsなら 'start' コマンド経由で別ウィンドウを開くとかっこいいが、
        # ここではシンプルにバックグラウンドプロセスとして起動
        # (ログは混ざりますが、動作確認はできます)
        cmd = [sys.executable, script_path, arg_list]
        
        # 別ウィンドウで開きたい場合は shell=True (Windows)
        p = subprocess.Popen(cmd, shell=True)
        processes.append(p)
        
        # API制限回避のため、起動を少しずらす
        time.sleep(3)

    print(f"{'='*60}")
    print(f"✅ 全ボット出撃完了！")
    print(f"   終了するには、開いた各ウィンドウを閉じるか、")
    print(f"   タスクマネージャで python プロセスを終了してください。")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()