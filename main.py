# -*- coding: utf-8 -*-
"""
main.py — Lv6.7 fixed（取引＋16:30自動学習）
- 市場時間帯：Controller を起動して売買
- 稼働中でも 16:30(JST) になればバックグラウンドで train_policy.py を実行
- 16:30 以降に起動した場合はワンショットで学習して終了（必要なら変更可）
"""

import os
import sys
import time
import logging
import threading
import subprocess
from datetime import datetime as dt_datetime, timedelta, timezone

from utils_common import ensure_dir, now
from config_loader import load_cfg
from controller import Controller
from model_registry import ModelRegistry

JST = timezone(timedelta(hours=9))

# ===== ログ設定 =====
def setup_logging(level: str):
    ensure_dir("logs")
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(f"logs/run_{now().strftime('%Y%m%d')}.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.info(f"==== Session Start {now().strftime('%Y-%m-%d %H:%M:%S')} ====")

# ===== Heartbeat（1秒ごと）=====
def start_heartbeat(cfg, api_info_func=None):
    stop = threading.Event()
    def beat():
        i = 0
        while not stop.is_set():
            i += 1
            try:
                kill_exists = os.path.exists(cfg.safety.kill_switch_file)
            except Exception:
                kill_exists = False
            msg = f"HB i={i} paper={getattr(cfg,'paper_trading', False)} kill_exists={kill_exists}"
            if api_info_func:
                extra = api_info_func()
                if extra:
                    msg += " " + extra
            logging.info(msg)
            stop.wait(1.0)
    th = threading.Thread(target=beat, name="heartbeat", daemon=True)
    th.start()
    return stop

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
                    logging.info(f"[auto-train] start lookback_days={lookback}")
                    try:
                        subprocess.run(
                            ["python", "train_policy.py"],
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

# ===== 通常取引 =====
def run_trading(cfg):
    logging.info(
        "market_now=%s, no_new_entry_after=%s, force_close_time=%s",
        True,
        getattr(getattr(cfg, "risk", object()), "no_new_entry_after", None),
        getattr(getattr(cfg, "risk", object()), "force_close_time", None),
    )

    ctrl = Controller(cfg)

    # Heartbeat開始（ControllerがKabusAPIを持つならレイテンシも表示）
    def api_extra():
        try:
            api = getattr(ctrl, "api", None)
            if api is None:
                return ""
            return f"latency_ms={getattr(api,'last_latency_ms', -1)}"
        except Exception:
            return ""

    hb_stop = start_heartbeat(cfg, api_info_func=api_extra)

    try:
        ctrl.start()  # 通常はブロッキング
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt: stopping controller...")
        if hasattr(ctrl, "stop"):
            try:
                ctrl.stop()
            except Exception:
                pass
    except Exception:
        logging.exception("FATAL: Controller 内で未処理例外")
        raise
    finally:
        hb_stop.set()

# ===== メイン =====
def main():
    cfg = load_cfg()
    setup_logging(getattr(cfg, "log_level", "INFO"))
    ensure_dir("runtime")

    # 監視スレッドは起動直後に開始（cfg 読み込み後）
    start_auto_train_watcher(cfg)

    now_jst = dt_datetime.now(JST)
    hhmm = now_jst.strftime("%H:%M")

    # 16:30 以降に起動したら即学習だけ実行して終了（常駐させたい場合はここを変えてください）
    learning = getattr(cfg, "learning", None)
    if learning and getattr(learning, "enable_auto_train", False) and (hhmm >= str(getattr(learning, "auto_train_time", "16:30"))):
        lookback = str(getattr(learning, "lookback_days", 7))
        logging.info("=== 夜間学習モード開始 ===")
        try:
            subprocess.run(
                ["python", "train_policy.py"],
                env={**os.environ, "LOOKBACK_DAYS": lookback},
                check=False,
            )
            logging.info("=== 学習完了 ===")
        except Exception as e:
            logging.error(f"夜間学習エラー: {e}")
        return

    # 市場時間帯は取引モード
    logging.info("=== 通常取引モード起動 ===")
    run_trading(cfg)

if __name__ == "__main__":
    main()
