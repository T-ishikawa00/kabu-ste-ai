# -*- coding: utf-8 -*-
"""
utils_common.py — time/log + tick helpers
- JST時刻ヘルパ
- ログ設定（force=Trueで多重ハンドラ防止）
- JPX風ティック推定（0.5円刻み推定 + 強制上書き）
- ティック丸めは浮動小数誤差に強い実装
"""
import os
import sys
import time
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

# ===== 時刻系 =====
JST = timezone(timedelta(hours=9))
def now() -> datetime: return datetime.now(JST)
def jst_iso() -> str:  return now().isoformat(timespec="seconds")

# ===== ファイル/ログ =====
def ensure_dir(p: str):
    if p and not os.path.exists(p):
        os.makedirs(p, exist_ok=True)

def setup_logging(level: str):
    ensure_dir("logs")
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logfile = f"logs/run_{now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.FileHandler(logfile, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,  # 既存ハンドラを完全リセット（多重出力防止）
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.info("==== Session Start %s ====", now().strftime("%Y-%m-%d %H:%M:%S"))

# ===== ティック計算 =====

# 強制ティック設定（例：7013, 3853 は 1円刻み）
FORCE_TICK: Dict[str, float] = {"7013": 1.0, "3853": 1.0}

def jpx_tick_by_price(px: float) -> float:
    """
    価格帯に応じた標準ティックを返す（簡易版）
    - < 3,000円: 1円
    - < 5,000円: 5円
    - 以上     : 10円
    ※必要なら本格テーブルに差し替え可
    """
    if px < 3000:
        return 1.0
    if px < 5000:
        return 5.0
    return 10.0

def infer_half_tick_from_board(b: Dict[str, Any]) -> Optional[float]:
    """
    0.5円刻みを検出（現値/最良気配から推測）
    """
    vals = []
    v = b.get("CurrentPrice")
    if isinstance(v, (int, float)):
        vals.append(float(v))
    for side in ("BestAsk", "BestBid"):
        x = b.get(side)
        if isinstance(x, dict) and isinstance(x.get("Price"), (int, float)):
            vals.append(float(x["Price"]))
    for v in vals:
        # v*2が整数で、v自体が整数でない → 0.5刻み
        if abs(v * 2 - round(v * 2)) < 1e-9 and abs(v - round(v)) > 1e-9:
            return 0.5
    return None

def tick_size(code: str, px: float, board: Dict[str, Any]) -> float:
    """
    ティックサイズを決定
    優先順位: FORCE_TICK > 0.5刻み推定 > 価格帯ルール
    """
    if str(code) in FORCE_TICK:
        return FORCE_TICK[str(code)]
    t = infer_half_tick_from_board(board)
    if t is not None:
        return t
    return jpx_tick_by_price(px)

def round_to_tick(px: float, tick: float, mode: str) -> float:
    """
    ティック単位で丸める（浮動小数誤差に強い）
      mode: "down" | "up" | "nearest"
    - 整数ティックはintで返却、それ以外（0.5等）は1桁小数で返却
    """
    if tick <= 0:
        return px
    # EPSで誤差を吸収
    eps = 1e-12
    q = (px + (eps if px >= 0 else -eps)) / tick
    if mode == "down":
        q = math.floor(q)
    elif mode == "up":
        q = math.ceil(q)
    else:
        q = round(q)
    res = q * tick
    # -0.0対策
    if abs(res) < 1e-12:
        res = 0.0
    return int(res) if float(tick).is_integer() else round(res, 1)

