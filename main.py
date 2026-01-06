
# -*- coding: utf-8 -*-
"""
main.py — Lv8.1（取引＋16:30自動学習＋通知機能対応）
- 市場時間帯：Controller を起動して売買
- 稼働中でも 16:30(JST) になればバックグラウンドで train_policy.py を実行
- 16:30 以降に起動した場合はワンショットで学習して終了
  （常駐させたい場合はこの条件を変更）
"""

import os
import sys
import time
import logging
import threading
import subprocess
import yaml  # ★追加: 設定ファイルを直接読むために必要
from datetime import datetime as dt_datetime, timedelta, timezone

from utils_common import ensure_dir, now
from config_loader import load_cfg
from controller import Controller
from model_registry import ModelRegistry  # 使う場合に備えて残す

JST = timezone(timedelta(hours=9))


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

            msg = (
                f"HB i={i} paper={getattr(cfg, 'paper_trading', False)} "
                f"kill_exists={kill_exists}"
            )

            if api_info_func:
                try:
                    extra = api_info_func()
                    if extra:
                        msg += " " + extra
                except Exception:
                    # API情報取得で死なないようにする
                    pass

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


# ===== 通常取引 =====

def run_trading(cfg):
    risk_cfg = getattr(cfg, "risk", object())
    logging.info(
        "market_now=%s, no_new_entry_after=%s, force_close_time=%s",
        True,
        getattr(risk_cfg, "no_new_entry_after", None),
        getattr(risk_cfg, "force_close_time", None),
    )

    ctrl = Controller(cfg)

    # Heartbeat開始（ControllerがKabusAPIを持つならレイテンシも表示）
    def api_extra():
        try:
            api = getattr(ctrl, "api", None)
            if api is None:
                return ""
            return f"latency_ms={getattr(api, 'last_latency_ms', -1)}"
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

    # =================================================================
    # ★★★ ここで強制的に config.yaml を読み直して通知設定を注入する ★★★
    # =================================================================
    try:
        with open("config.yaml", "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
            if "notification" in raw_data:
                # cfg がどんな型でも対応できるように無理やり属性セット
                setattr(cfg, "notification", raw_data["notification"])
                # 念のため辞書としてもアクセスできるようにする
                if isinstance(cfg, dict):
                    cfg["notification"] = raw_data["notification"]
                
                url = raw_data['notification'].get('discord_webhook_url', '')
                logging.info(f"🔧 通知設定を強制ロードしました (URL末尾: ...{url[-5:]})")
            else:
                logging.warning("⚠️ config.yaml に notification セクションが見つかりません")
    except Exception as e:
        logging.warning(f"⚠️ 通知設定の強制ロードに失敗: {e}")
    # =================================================================

    # 16:30自動学習ウォッチャ起動（学習設定が無効なら中で勝手に待機する）
    start_auto_train_watcher(cfg)

    now_jst = dt_datetime.now(JST)
    hhmm = now_jst.strftime("%H:%M")

    # 16:30 以降に起動したら即学習だけ実行して終了
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

    # 市場時間帯は取引モード
    logging.info("=== 通常取引モード起動 ===")
    run_trading(cfg)


if __name__ == "__main__":
    main()
