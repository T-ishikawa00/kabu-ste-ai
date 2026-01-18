# -*- coding: utf-8 -*-
"""
main.py — Lv16.0 (Commander Edition)
====================================
機能:
- launch_multi_bot.py の機能を統合
- 起動すると config.yaml を読み込み、銘柄リストを分割して
  複数の controller_ws.py (分身) をサブプロセスとして並列起動します。
- 既存の「自動学習」や「通知設定ロード」機能はそのまま維持。
"""

import os
import sys
import time
import logging
import threading
import subprocess
import yaml
import math
from datetime import datetime as dt_datetime, timedelta, timezone

from utils_common import ensure_dir, now
from config_loader import load_cfg

# ※ここではクラスとして使わず、サブプロセス起動用のファイルパスとして使う
CONTROLLER_SCRIPT_NAME = "controller_ws.py"

JST = timezone(timedelta(hours=9))

# ★1ボットあたりの担当銘柄数
STOCKS_PER_BOT = 10


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
    logging.info(f"==== Session Start {now().strftime('%Y-%m-%d %H:%M:%S')} (Commander Mode) ====")


# ===== 16:30 自動学習ウォッチャ =====
def start_auto_train_watcher(cfg):
    def _watch():
        ran_date = None
        while True:
            try:
                learning = getattr(cfg, "learning", None)
                if not learning or not getattr(learning, "enable_auto_train", False):
                    time.sleep(30)
                    continue

                target = str(getattr(learning, "auto_train_time", "16:30"))
                now_jst = dt_datetime.now(JST)
                hhmm = now_jst.strftime("%H:%M")
                today = now_jst.date()

                if (hhmm >= target) and (ran_date != today):
                    lookback = str(getattr(learning, "lookback_days", 7))
                    python_cmd = getattr(learning, "python_cmd", sys.executable)
                    logging.info(f"[auto-train] start lookback_days={lookback}")
                    try:
                        subprocess.run(
                            [python_cmd, "train_policy.py"],
                            env={**os.environ, "LOOKBACK_DAYS": lookback},
                            check=False,
                        )
                        logging.info("[auto-train] done")
                    except Exception as e:
                        logging.error(f"[auto-train] failed: {e}")
                    ran_date = today

                time.sleep(20)

            except Exception as e:
                logging.warning(f"[auto-train] watcher error: {e}")
                time.sleep(30)

    th = threading.Thread(target=_watch, name="auto_train_watcher", daemon=True)
    th.start()
    return th


# ===== 取引実行ロジック (Commander) =====

def run_trading(cfg):
    """
    ここで controller_ws.py を複数体起動する
    """
    logging.info("🚀 マルチプロセス・トレードモード開始")
    
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

    # 3. 銘柄を分割 (Chunking)
    num_bots = math.ceil(total_stocks / STOCKS_PER_BOT)
    chunks = [codes[i:i + STOCKS_PER_BOT] for i in range(0, total_stocks, STOCKS_PER_BOT)]
    
    processes = []

    # 4. サブプロセス起動
    try:
        for i, chunk in enumerate(chunks):
            # 銘柄リストをカンマ区切りの文字列にする "9984,7011,..."
            arg_list = ",".join(chunk)
            
            logging.info(f"🤖 [Bot {i+1}] 起動 (担当: {len(chunk)}銘柄: {chunk[0]}...)")
            
            # 別プロセスとして起動
            cmd = [sys.executable, script_path, arg_list]
            p = subprocess.Popen(cmd)
            processes.append(p)
            
            # API制限回避のため、起動を少しずらす
            time.sleep(3)

        logging.info("✅ 全ボット出撃完了。司令塔は監視モードに入ります。")
        
        # 5. 親プロセスは待機 (死なないようにループ)
        while True:
            time.sleep(10)
            # 必要であればここで子プロセスの生存確認などを行う
            all_dead = True
            for p in processes:
                if p.poll() is None:
                    all_dead = False
                    break
            if all_dead:
                logging.warning("⚠️ 全てのボットが終了しました。")
                break

    except KeyboardInterrupt:
        logging.info("🛑 停止シグナル受信。全ボットを停止します...")
        for p in processes:
            p.terminate()
        logging.info("👋 全プロセス停止完了")
    except Exception as e:
        logging.error(f"Commander Error: {e}")
        for p in processes:
            p.terminate()


# ===== メイン =====

def main():
    cfg = load_cfg()
    setup_logging(getattr(cfg, "log_level", "INFO"))
    ensure_dir("runtime")

    # =================================================================
    # 通知設定の強制ロード (維持)
    # =================================================================
    try:
        with open("config.yaml", "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
            if "notification" in raw_data:
                setattr(cfg, "notification", raw_data["notification"])
                if isinstance(cfg, dict):
                    cfg["notification"] = raw_data["notification"]
                url = raw_data['notification'].get('discord_webhook_url', '')
                logging.info(f"🔧 通知設定ロード (URL末尾: ...{url[-5:]})")
    except Exception:
        pass
    # =================================================================

    # 自動学習ウォッチャ起動 (維持)
    start_auto_train_watcher(cfg)

    now_jst = dt_datetime.now(JST)
    hhmm = now_jst.strftime("%H:%M")

    # 夜間学習モード判定 (維持)
    learning = getattr(cfg, "learning", None)
    if learning and getattr(learning, "enable_auto_train", False):
        target = str(getattr(learning, "auto_train_time", "16:30"))
        if hhmm >= target:
            lookback = str(getattr(learning, "lookback_days", 7))
            python_cmd = getattr(learning, "python_cmd", sys.executable)
            logging.info("=== 夜間学習モード開始 ===")
            try:
                subprocess.run(
                    [python_cmd, "train_policy.py"],
                    env={**os.environ, "LOOKBACK_DAYS": lookback},
                    check=False,
                )
                logging.info("=== 学習完了 ===")
            except Exception as e:
                logging.error(f"夜間学習エラー: {e}")
            return

    # 通常取引モード (並列起動ロジックへ)
    run_trading(cfg)


if __name__ == "__main__":
    main()