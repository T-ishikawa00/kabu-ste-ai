# -*- coding: utf-8 -*-
"""
main.py v22.5 (Unified Launcher)
================================
役割:
1. 最新の Controller (v22系) を正しく起動する
2. 15:40 に自動停止する
3. 必須ファイルの存在チェックを行う
"""

import os
import sys
import time
import logging
import subprocess
from datetime import datetime as dt_datetime, timedelta, timezone

# タイムゾーン設定
JST = timezone(timedelta(hours=9))

def setup_logging():
    if not os.path.exists("logs"):
        os.makedirs("logs")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

def main():
    setup_logging()
    logging.info("=" * 60)
    logging.info("🚀 Kabu-Ste-AI v22.5 System Start")
    logging.info("=" * 60)

    # 1. 必須ファイルの存在確認 (これがないと始まらない)
    required_files = [
        "controller_ws.py",   # 現場監督
        "config.yaml",        # 設定
        "strategy_router.py", # 通訳 (重要!)
        "broker.py"           # 手足
    ]
    
    missing = []
    for f in required_files:
        if not os.path.exists(f):
            missing.append(f)
            
    if missing:
        logging.error("❌ 起動エラー: 以下の必須ファイルが見つかりません。")
        logging.error(f"   欠損: {missing}")
        logging.error("   '_archive' に移動しすぎていませんか？ 元に戻してください。")
        return

    # 2. Controllerをサブプロセスで起動
    # (メモリリーク防止のため、別プロセスで動かすのが鉄則)
    cmd = [sys.executable, "controller_ws.py"]
    
    logging.info(f"👉 司令塔を起動します: {' '.join(cmd)}")
    process = subprocess.Popen(cmd)

    try:
        while True:
            # プロセスの生存確認
            if process.poll() is not None:
                logging.warning("⚠️ Controllerが終了しました。")
                break

            # 時刻チェック (15:40 強制終了)
            now = dt_datetime.now(JST)
            if now.hour == 15 and now.minute >= 40:
                logging.warning("🛑 15:40 定時退社時間です。システムを停止します。")
                process.terminate()
                break

            # 10秒おきに監視
            time.sleep(10)

    except KeyboardInterrupt:
        logging.info("🛑 ユーザー割り込みにより停止します。")
        process.terminate()
    except Exception as e:
        logging.error(f"❌ Main Error: {e}")
        process.terminate()
    finally:
        try:
            process.wait(timeout=5)
        except:
            pass
        logging.info("👋 System Shutdown Complete.")

if __name__ == "__main__":
    main()