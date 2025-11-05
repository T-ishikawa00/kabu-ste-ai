# -*- coding: utf-8 -*-
"""
telemetry.py — 軽量テレメトリ出力（原子的保存・スロットリング）
- stateをJSONで定期保存（.tmp→rename で原子的）
- 最小1秒間隔（デフォルト）。必要なら引数で変更可
- 例外時はwarningを残しつつ処理継続
"""
import os
import json
import time
import logging
from typing import Any, Dict

from utils_common import ensure_dir, jst_iso


class Telemetry:
    def __init__(self, path: str, min_interval_sec: float = 1.0):
        self.path = path
        ensure_dir(os.path.dirname(path) or ".")
        self.state: Dict[str, Any] = {
            "ts": jst_iso(),
            "mode": "init",
            "positions": {},
            "latency_ms": 0,
        }
        self._last_write = 0.0
        self._min_interval = float(min_interval_sec)

    def update(self, kv: Dict[str, Any]):
        """辞書をまとめて更新"""
        if not isinstance(kv, dict):
            return
        self.state.update(kv)

    def set(self, key: str, value: Any):
        """単一キー更新"""
        self.state[key] = value

    def snapshot(self) -> Dict[str, Any]:
        """現在のstateを辞書で返す（参照ではなくコピー）"""
        return dict(self.state)

    def write(self, force: bool = False):
        """状態をJSONへ書き出し。連続書き込みを最小間隔で抑制"""
        nowt = time.time()
        if not force and (nowt - self._last_write) < self._min_interval:
            return

        self.state["ts"] = jst_iso()
        tmp = self.path + ".tmp"
        try:
            # テキストJSON → flush → fsync → 原子的rename
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            self._last_write = nowt
        except Exception as e:
            logging.warning("Telemetry write failed: %s", e)
            # tmpが残っていたら掃除（ベストエフォート）
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def save_atomic(self, data: Dict[str, Any]):
        """controller 互換の安全保存ラッパ"""
        self.update(data)
        self.write(force=True)


# ===== ここから学習補助ヘルパ（controller から呼ぶ） =====

import collections

_SEQ_RING: Dict[str, "collections.deque"] = {}  # code -> deque of [ts_ms, snapshot(list)]


def seq_tick(ctx):
    """
    板更新ごとに呼ぶと、直近2秒分の50msスナップショットを保持する
    tradeログに埋め込む前提のリングバッファ
    """
    code = getattr(ctx, "code", "UNKNOWN")
    dq = _SEQ_RING.get(code)
    if dq is None:
        dq = collections.deque(maxlen=40)  # 50ms × 40 ≒ 2秒
        _SEQ_RING[code] = dq
    snap = [
        float(getattr(ctx, "mid_px", getattr(ctx, "cur_px", 0.0))),
        float(getattr(ctx, "spread_ticks", 1.0)),
        float(getattr(ctx, "best_bid_qty", 0.0)),
        float(getattr(ctx, "best_ask_qty", 0.0)),
        float(getattr(ctx, "upticks", 0.0)),
        float(getattr(ctx, "delta_ticks_2s", 0.0)),
        float(getattr(ctx, "vol5", 0.0)),
        float(getattr(ctx, "board_rtt_ms", 0.0)),
    ]
    dq.append([int(time.time() * 1000), snap])


def attach_trade_fields(trade: Dict[str, Any], pos_or_ctx: Any):
    """学習用に Entry/Exit の行動と特徴量、50msリングを trade に付与"""
    try:
        trade["entry_action"] = getattr(
            pos_or_ctx,
            "_entry_action",
            pos_or_ctx.get("_entry_action", "SKIP") if isinstance(pos_or_ctx, dict) else "SKIP",
        )
    except Exception:
        trade["entry_action"] = "SKIP"
    try:
        trade["exit_action"] = getattr(
            pos_or_ctx,
            "_exit_action",
            pos_or_ctx.get("_exit_action", "HOLD") if isinstance(pos_or_ctx, dict) else "HOLD",
        )
    except Exception:
        trade["exit_action"] = "HOLD"
    try:
        trade["feat_entry"] = getattr(
            pos_or_ctx,
            "_feat_entry",
            pos_or_ctx.get("_feat_entry") if isinstance(pos_or_ctx, dict) else None,
        )
        trade["feat_exit"] = getattr(
            pos_or_ctx,
            "_feat_exit",
            pos_or_ctx.get("_feat_exit") if isinstance(pos_or_ctx, dict) else None,
        )
    except Exception:
        pass
    code = trade.get("code", "UNKNOWN")
    trade["seq_50ms"] = list(_SEQ_RING.get(code, []))


def compute_rewards(trade: Dict[str, Any]):
    """
    報酬の算出（摩擦の一部控除＋決済理由に応じた軽微補正）
    trade["pnl"]["ticks"] と entry/exitのスリップ・支払スプレッドが存在すれば補正
    """
    e = trade.get("entry", {})
    x = trade.get("exit", {})
    pnl_ticks = float(trade.get("pnl", {}).get("ticks", 0.0))
    fric = float(e.get("slip_ticks", 0.0)) + float(x.get("slip_ticks", 0.0)) + float(
        e.get("paid_spread_ticks", 0.0)
    )
    base = pnl_ticks - 0.5 * fric
    reason = str(x.get("reason", ""))
    adj = 0.0
    if reason == "TP":
        adj += 0.2
    elif reason == "TP_MKT_FALLBACK":
        adj -= 0.3
    elif reason.startswith("SL") or "TIMEOUT" in reason:
        adj -= 0.5
    elif reason.startswith("EMERGENCY") or reason == "EXIT_NOW":
        adj -= 1.0
    elif reason.startswith("BE"):
        adj -= 0.1
    r = base + adj
    trade["reward_bandit_exit"] = r
    trade["reward_bandit_entry"] = r


def attach_meta_fields(trade: Dict[str, Any], ctx_or_pos: Any):
    """メタ層（株数サイジング / 5円ゲート）の判断を trade に付与"""
    meta = trade.setdefault("meta", {})
    try:
        meta["size_action"] = getattr(ctx_or_pos, "_decided_qty", None)
        meta["size_reason"] = getattr(ctx_or_pos, "_size_reason", None)
        meta["gate_action"] = (
            "ALLOW_5YEN" if getattr(ctx_or_pos, "_gate_5yen", True) else "BLOCK_5YEN"
        )
        if hasattr(ctx_or_pos, "_gate_reason"):
            meta["gate_reason"] = getattr(ctx_or_pos, "_gate_reason")
    except Exception:
        pass


