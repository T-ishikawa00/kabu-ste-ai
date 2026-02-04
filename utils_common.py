# -*- coding: utf-8 -*-
"""
utils_common.py v22.6 (Tick Logic Fix)
======================================
システムの基礎となる共通関数群。

【v22.6 修正点】
1. ティック判定ロジックの優先順位を修正
   - 誤: 0.1で割り切れたら即0.1認定 (100.5 を 0.1認定してしまうバグ)
   - 正: まず「0.1でないと説明つかない値(100.1等)」を探し、なければ「0.5」を採用
   -> これにより、誤った細かい指値による注文エラーを防止
"""
import os
import sys
import time
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

# ==========================================
# 1. 時刻・ログ設定
# ==========================================
JST = timezone(timedelta(hours=9))

def now() -> datetime:
    """現在のJST時刻を返す"""
    return datetime.now(JST)

def jst_iso() -> str:
    """現在時刻をISO形式の文字列で返す"""
    return now().isoformat(timespec="seconds")

def ensure_dir(p: str):
    """フォルダがなければ作成"""
    if p and not os.path.exists(p):
        os.makedirs(p, exist_ok=True)

def setup_logging(level: str):
    """
    ログ設定の初期化
    force=True により、呼び出すたびに設定をリセットして多重出力を防ぐ
    """
    ensure_dir("logs")
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    # 秒まで含めることで再起動時のファイル重複を回避
    logfile = f"logs/run_{now().strftime('%Y%m%d_%H%M%S')}.log"
    
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.FileHandler(logfile, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True, 
    )
    # うるさいライブラリのログを黙らせる
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    
    logging.info(f"==== System Session Start: {now().strftime('%Y-%m-%d %H:%M:%S')} ====")

# ==========================================
# 2. ティック(呼値) 計算ロジック
# ==========================================

# 緊急時用：特定の銘柄のティックを強制指定するリスト
FORCE_TICK: Dict[str, float] = {}

def jpx_tick_by_price(px: float) -> float:
    """
    価格帯に応じた標準的なJPX呼値テーブル (TOPIX100以外)
    ※あくまで「板情報がない場合」のバックアップとして使用
    """
    if px <= 3000: return 1.0
    if px <= 5000: return 5.0
    if px <= 30000: return 10.0
    if px <= 50000: return 50.0
    if px <= 300000: return 100.0
    return 500.0

def infer_tick_from_board(b: Dict[str, Any]) -> Optional[float]:
    """
    ★重要: 板情報から「実際のティックサイズ」を推測する
    """
    if not b:
        return None

    # チェックする価格リスト (現値, 売り気配, 買い気配)
    vals = []
    
    v = b.get("CurrentPrice")
    if isinstance(v, (int, float)) and v > 0:
        vals.append(float(v))
        
    for side in ("BestAsk", "BestBid"):
        x = b.get(side)
        if isinstance(x, dict):
            p = x.get("Price")
            if isinstance(p, (int, float)) and p > 0:
                vals.append(float(p))

    # --- 判定ロジック (v22.6 修正版) ---
    
    # 1. まず「0.1円単位でないとありえない値」を探す (例: 100.1, 100.3)
    # これが見つかれば、問答無用で0.1円刻みと確定
    for v in vals:
        # 10倍すると整数だが、2倍しただけでは整数にならないもの
        if abs(v * 10 - round(v * 10)) < 1e-9:
            if abs(v * 2 - round(v * 2)) > 1e-9:
                return 0.1

    # 2. 次に「0.5円単位の値」を探す (例: 100.5)
    # 100.1のような値がなく、100.5があるなら、それは0.5円刻みとみなすのが安全
    for v in vals:
        if abs(v * 2 - round(v * 2)) < 1e-9 and abs(v - round(v)) > 1e-9:
            return 0.5

    return None

def tick_size(code: str, px: float, board: Dict[str, Any] = None) -> float:
    """
    最終的なティックサイズを決定する
    """
    # 1. 強制指定
    if str(code) in FORCE_TICK:
        return FORCE_TICK[str(code)]
    
    # 2. 板情報からの推測
    if board:
        t = infer_tick_from_board(board)
        if t is not None:
            return t
            
    # 3. 標準テーブル
    return jpx_tick_by_price(px)

def round_to_tick(px: float, tick: float, mode: str = "nearest") -> float:
    """
    価格をティック単位に丸める (浮動小数点の誤差対策済み)
    mode: "down"(切り捨て), "up"(切り上げ), "nearest"(四捨五入)
    """
    if tick <= 0:
        return px
        
    # EPSで浮動小数点の微小誤差を吸収
    eps = 1e-12
    
    # 基本の計算: 価格 ÷ ティック
    val = (px + (eps if px >= 0 else -eps)) / tick
    
    if mode == "down":
        q = math.floor(val)
    elif mode == "up":
        q = math.ceil(val)
    else:
        q = round(val)
        
    res = q * tick
    
    # -0.0 を 0.0 に直す
    if abs(res) < 1e-12:
        res = 0.0
        
    # 結果が整数の場合は int型、小数の場合は float型で返す
    return int(res) if float(tick).is_integer() else round(res, 1)

# テスト実行用
if __name__ == "__main__":
    print("Running utils_common check v22.6...")
    
    # ケース1: 1234.5 (0.5円刻みでも0.1円刻みでもありうる)
    # 安全側に倒して 0.5 を返すべき
    t1 = infer_tick_from_board({"CurrentPrice": 1234.5})
    print(f"Price: 1234.5 -> Inferred: {t1} (Expected: 0.5)")
    
    # ケース2: 1234.1 (0.5円刻みではありえない)
    # 0.1 を返すべき
    t2 = infer_tick_from_board({"CurrentPrice": 1234.1})
    print(f"Price: 1234.1 -> Inferred: {t2} (Expected: 0.1)")
    
    # ケース3: 混在 (1234.5 と 1234.1 がある)
    # 1234.1 がある以上、0.1円刻みでないと説明がつかない -> 0.1
    t3 = infer_tick_from_board({"CurrentPrice": 1234.5, "BestAsk": {"Price": 1234.1}})
    print(f"Price: 1234.5 & 1234.1 -> Inferred: {t3} (Expected: 0.1)")