# -*- coding: utf-8 -*-
"""
main.py — v20.8 (Self-Learning Trend Commander)
===============================================
【v20.8 変更点】
1. controller_ws.py v20.8 を起動（21次元AI、トレンドフィルター対応）
2. 1ボット体制維持（シングルプロセスで安定稼働）
"""

import os
import sys
import time
import logging
import threading
import subprocess
import yaml
import math
import json
import urllib.request
from datetime import datetime as dt_datetime, timedelta, timezone

from utils_common import ensure_dir, now
from config_loader import load_cfg

# サブプロセスとして起動するコントローラー
CONTROLLER_SCRIPT_NAME = "controller_ws.py"

JST = timezone(timedelta(hours=9))

# ★1ボットあたりの担当銘柄数 (シングルボット運用)
STOCKS_PER_BOT = 100

# ===== ログ設定 =====
def setup_logging(level: str):
    ensure_dir("logs")
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(
                f"logs/run_{now().strftime('%Y%m%d')}.log",
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.info(f"==== Session Start {now().strftime('%Y-%m-%d %H:%M:%S')} (v20.8 Self-Learning Trend) ====")

# ==========================================
# ★Discord通知テスト
# ==========================================
def send_discord_test(webhook_url):
    if not webhook_url:
        logging.warning("🔕 Discord URLが設定されていません")
        return

    logging.info("📨 Discord通知テスト送信中...")
    try:
        payload = {
            "content": f"📢 **Commander起動 (v20.8)**: 汎用モデル強制モードで開始します\n⏰ {now().strftime('%H:%M:%S')}"
        }
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=5) as res:
            if 200 <= res.status < 300:
                logging.info("✅ Discord通知テスト成功！")
    except Exception as e:
        logging.error(f"❌ Discord通知送信失敗: {e}")

# ===== 取引実行ロジック (Commander) =====

def run_trading(cfg):
    logging.info("🚀 トレードモード開始 (v20.8 Self-Learning Trend)")
    
    # 1. 銘柄リストの取得
    symbols = getattr(cfg, "symbols", [])
    if not symbols:
        logging.error("❌ 監視銘柄が config.yaml にありません。")
        return

    codes = [str(s.code) for s in symbols]
    total_stocks = len(codes)
    logging.info(f"📊 監視総数: {total_stocks} 銘柄")

    # 2. 実行ファイルの場所特定
    script_path = CONTROLLER_SCRIPT_NAME
    if not os.path.exists(script_path):
        if os.path.exists(f"core/{CONTROLLER_SCRIPT_NAME}"):
            script_path = f"core/{CONTROLLER_SCRIPT_NAME}"
        else:
            logging.error(f"❌ {CONTROLLER_SCRIPT_NAME} が見つかりません。")
            return
    
    logging.info(f"📂 Controller Path: {script_path}")

    # 3. 通知設定の取得
    discord_url = ""
    notif = getattr(cfg, "notification", None)
    if notif:
        discord_url = getattr(notif, "discord_webhook_url", "")
    
    if discord_url:
        logging.info(f"🔧 Discord URL検出: ...{discord_url[-5:]}")
        send_discord_test(discord_url)

    # 4. サブプロセス起動
    chunks = [codes[i:i + STOCKS_PER_BOT] for i in range(0, total_stocks, STOCKS_PER_BOT)]
    processes = []
    try:
        env = os.environ.copy()
        if discord_url:
            env["DISCORD_WEBHOOK_URL"] = discord_url

        for i, chunk in enumerate(chunks):
            arg_list = ",".join(chunk)
            logging.info(f"🤖 [Bot {i+1}] 起動 (担当: {len(chunk)}銘柄)")
            
            # controller_ws.py を起動
            cmd = [sys.executable, script_path, arg_list]
            p = subprocess.Popen(cmd, env=env) 
            processes.append(p)
            time.sleep(3)

        logging.info("✅ ボット出撃完了。司令塔は監視に入ります。")
        
        # 5. 親プロセスは待機
        while True:
            time.sleep(10)
            
            # ★15:40自動終了
            now_jst = dt_datetime.now(JST)
            if now_jst.hour == 15 and now_jst.minute >= 40:
                logging.warning("🛑 15:40 自動終了します")
                for p in processes:
                    p.terminate()
                logging.info("👋 全プロセス停止完了")
                sys.exit(0)
            
            all_dead = True
            for p in processes:
                if p.poll() is None:
                    all_dead = False
                    break
            if all_dead:
                logging.warning("⚠️ 全ボットが終了しました。")
                break

    except KeyboardInterrupt:
        logging.info("🛑 停止シグナル受信。ボットを停止します...")
        for p in processes:
            p.terminate()
        logging.info("👋 全プロセス停止完了")
    except Exception as e:
        logging.error(f"Commander Error: {e}")
        for p in processes:
            p.terminate()

# ===== メイン =====

def main():
    # コンフィグ読み込み
    cfg = load_cfg()
    setup_logging(getattr(cfg, "log_level", "INFO"))
    ensure_dir("runtime")

    # ※自動学習ウォッチャ(v17時代の古いロジック)は無効化しています
    # 理由: v19.3では train_universal.py による全体学習が推奨されるため
    # start_auto_train_watcher(cfg)

    # 通常取引モードのみ実行
    run_trading(cfg)

if __name__ == "__main__":
    main()