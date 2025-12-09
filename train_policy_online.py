# -*- coding: utf-8 -*-
"""
train_policy_online.py — トレード終了ごとに1ステップ学習を行う (Lv8.0想定)

役割：
- controller から trade_result を受け取って、銘柄ごとのモデル JSON を軽くチューニングする。
- 「オンライン学習」はあくまで微調整。大きな最適化は日次・週次のオフライン側でやる前提。

想定する trade_result 例：
{
    "code": "7013",          # 銘柄コード（必須）
    "side": "BUY",           # "BUY" or "SELL"
    "pnl": 1200.0,           # 損益（円）
    "slippage": -0.2,        # 任意
    "hold_sec": 45,          # 任意
    "meta": {...}           # 任意
}
"""

import os
import json
import time
from typing import Dict, Any

MODELS_DIR = "models"

# 学習率（かなり小さくしておく：秒スキャ～ミニスイングの安定重視）
W_LR = 0.01  # w_buy / w_sell の更新係数
W_MIN = 0.5
W_MAX = 1.5


# ===================== 基本入出力 =====================

def _load_model(code: str) -> Dict[str, Any]:
    """models/{code}.json を読み込む。なければデフォルトを返す。"""
    os.makedirs(MODELS_DIR, exist_ok=True)
    path = os.path.join(MODELS_DIR, f"{code}.json")
    if not os.path.exists(path):
        # 初期値は仕様書の推奨にあわせる
        return {
            "theta": 0.62,
            "w_buy": 1.0,
            "w_sell": 1.0,
            "online": {}
        }

    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        # 壊れた場合は安全側で初期化（ログは controller 側で拾う想定）
        return {
            "theta": 0.62,
            "w_buy": 1.0,
            "w_sell": 1.0,
            "online": {"load_error": True}
        }

    if "online" not in obj or not isinstance(obj["online"], dict):
        obj["online"] = {}
    if "theta" not in obj:
        obj["theta"] = 0.62
    if "w_buy" not in obj:
        obj["w_buy"] = 1.0
    if "w_sell" not in obj:
        obj["w_sell"] = 1.0

    return obj


def _save_model(code: str, model: Dict[str, Any]) -> None:
    """models/{code}.json に保存する。"""
    os.makedirs(MODELS_DIR, exist_ok=True)
    path = os.path.join(MODELS_DIR, f"{code}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)


def _clip(v: float, vmin: float, vmax: float) -> float:
    return max(vmin, min(vmax, v))


# ===================== オンライン更新ロジック =====================

def _update_online_stats(online: Dict[str, Any], pnl: float, side: str) -> Dict[str, Any]:
    """オンライン統計情報（trade_count / win_count / loss_count / EV など）を更新。"""
    trade_count = int(online.get("trade_count", 0)) + 1
    win_count = int(online.get("win_count", 0))
    loss_count = int(online.get("loss_count", 0))
    cum_pnl = float(online.get("cum_pnl", 0.0)) + pnl

    if pnl > 0:
        win_count += 1
    elif pnl < 0:
        loss_count += 1

    ev_per_trade = cum_pnl / trade_count if trade_count > 0 else 0.0

    online.update(
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        cum_pnl=cum_pnl,
        ev_per_trade=ev_per_trade,
        last_pnl=pnl,
        last_side=side,
        last_updated=time.time()
    )
    return online


def _update_weights(model: Dict[str, Any], pnl: float, side: str) -> None:
    """
    w_buy / w_sell を損益に応じて微調整。
    - 勝ちトレード：該当サイドの重みを少し上げる
    - 負けトレード：該当サイドの重みを少し下げる
    - 引き分け：何もしない

    theta はここでは触らない（非悪化保証を崩しやすいので、日次オフラインで調整する想定）。
    """
    if side not in ("BUY", "SELL"):
        return
    if pnl == 0:
        return

    direction = 1.0 if pnl > 0 else -1.0
    delta = W_LR * direction

    if side == "BUY":
        w = float(model.get("w_buy", 1.0))
        w = _clip(w + delta, W_MIN, W_MAX)
        model["w_buy"] = w
    else:  # "SELL"
        w = float(model.get("w_sell", 1.0))
        w = _clip(w + delta, W_MIN, W_MAX)
        model["w_sell"] = w


# ===================== 公開API =====================

def update_after_trade(trade_result: Dict[str, Any]) -> None:
    """
    トレード終了ごとに controller から呼び出す想定のエントリポイント。

    期待する主なキー:
        - code: 銘柄コード (str)   ※必須
        - pnl: 損益 (float, 円)   ※必須
        - side: "BUY" or "SELL"  ※推奨（なければ重み更新はスキップ）
    """
    # 銘柄コードを取得（code / symbol / ticker のどれかに入っていれば拾う）
    code = trade_result.get("code") or trade_result.get("symbol") or trade_result.get("ticker")
    if not code:
        # コードが無いなら何もできない
        return

    code = str(code)

    try:
        pnl = float(trade_result.get("pnl", 0.0))
    except Exception:
        pnl = 0.0

    side_raw = trade_result.get("side") or trade_result.get("direction") or ""
    side = str(side_raw).upper()

    # モデル読み込み
    model = _load_model(code)

    # 統計更新
    online = model.get("online", {})
    online = _update_online_stats(online, pnl, side)
    model["online"] = online

    # パラメータ微調整（w_buy / w_sell のみ）
    _update_weights(model, pnl, side)

    # 保存
    _save_model(code, model)


if __name__ == "__main__":
    # 単体テスト用の簡易実行
    dummy_trade = {
        "code": "9999",
        "side": "BUY",
        "pnl": 500.0,
    }
    update_after_trade(dummy_trade)
    print("updated:", _load_model("9999"))
