from __future__ import annotations
# -*- coding: utf-8 -*-

import os
import json
import time
import numpy as np
from typing import Any, Dict

from bandit_linucb import LinUCB
from features import exit_feature_vector

ACTIONS_EXIT = ["HOLD", "EXIT_NOW", "TIGHTEN_BE", "ARM_TP3"]


class ExitDecider:
    """
    ExitDecider — Lv8.0 対応版
    - 条件崩壊による強制 EXIT を最優先
      * heat 低下
      * OBI 反転
      * 出来高冷え
      * 保有時間オーバー
      * 市場温度 (market_heat) の急冷
    - その上で、LinUCB による EXIT/HOLD 系アクションを選択
    - 既存インターフェースは維持:
      * __init__(model_path, alpha, min_gap_ms, ...)
      * decide(ctx, pos) -> str
      * update_from_trade(trade)
    """

    def __init__(
        self,
        model_path: str = "model_registry.json",
        alpha: float = 0.5,
        min_gap_ms: int = 30_000,
        # ここから下はすべてデフォルト付きの新パラメータ（既存呼び出しは壊さない）
        theta_drop: float = 0.40,          # heat 低下しきい値
        obi_flip_thr: float = 0.10,        # OBI 反転差
        vol_cool_ratio: float = 0.1,       # 出来高冷え比率 (recent < avg * ratio)
        max_hold_sec: int = 120,           # 最大保有秒
        min_market_heat: float = 0.10,     # 市場温度の下限（通常）
        min_market_heat_open: float = 0.15 # 市場温度の下限（寄りは少し厳しめ）
    ):
        self.model_path = model_path
        self.alpha = alpha
        self.min_gap_ms = min_gap_ms

        self.theta_drop = theta_drop
        self.obi_flip_thr = obi_flip_thr
        self.vol_cool_ratio = vol_cool_ratio
        self.max_hold_sec = max_hold_sec
        self.min_market_heat = min_market_heat
        self.min_market_heat_open = min_market_heat_open

        self._load()

    # === モデルロード ===

    def _load(self) -> None:
        self.state: Dict[str, Any] = {
            "exit_bandit": None,
            "last_decide_ms": {},  # code -> last_decide_ms
        }

        if os.path.exists(self.model_path):
            try:
                with open(self.model_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                b = obj.get("_exit_bandit_")
                if b:
                    self.state["exit_bandit"] = LinUCB.from_json(b)
            except Exception:
                pass

        if self.state["exit_bandit"] is None:
            # d=10 は既存の exit_feature_vector に合わせて維持
            self.state["exit_bandit"] = LinUCB(ACTIONS_EXIT, d=10, alpha=self.alpha)

    # === 保存 ===

    def _save(self) -> None:
        try:
            obj: Dict[str, Any] = {}
            if os.path.exists(self.model_path):
                with open(self.model_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
            obj["_exit_bandit_"] = self.state["exit_bandit"].to_json()
            tmp = self.model_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.model_path)
        except Exception:
            pass

    # === 条件崩壊チェック（強制 EXIT 判定）===

    def _should_force_exit(self, ctx, pos) -> bool:
        """
        ctx.state に入っている特徴量＋ctx.regime / ctx.market_heat を使って
        条件崩壊を判定する。
        """
        s = getattr(ctx, "state", {})
        heat = s.get("heat", 0.0)
        obi_buy = s.get("obi_buy", 0.0)
        obi_sell = s.get("obi_sell", 0.0)
        vol_recent = s.get("vol_recent", 1.0)
        vol_avg = s.get("vol_avg", 1.0)
        hold_sec = s.get("hold_sec", 0)
        side = pos.get("side", "BUY")

        # regime と 市場温度
        regime = getattr(ctx, "regime", "AM")
        market_heat = float(getattr(ctx, "market_heat", 1.0))
        heat_thr_mkt = (
            self.min_market_heat_open if regime == "OPEN" else self.min_market_heat
        )

        # 各条件
        cond_heat = heat < self.theta_drop
        cond_vol = vol_recent < vol_avg * self.vol_cool_ratio
        cond_time = hold_sec > self.max_hold_sec
        cond_obi = False
        if side == "BUY":
            cond_obi = (obi_sell - obi_buy > self.obi_flip_thr)
        elif side == "SELL":
            cond_obi = (obi_buy - obi_sell > self.obi_flip_thr)

        cond_mkt = market_heat < heat_thr_mkt

        if cond_heat or cond_vol or cond_obi or cond_time or cond_mkt:
            reasons = []
            if cond_heat:
                reasons.append("heat低下")
            if cond_vol:
                reasons.append("出来高冷え")
            if cond_obi:
                reasons.append("OBI反転")
            if cond_time:
                reasons.append("時間切れ")
            if cond_mkt:
                reasons.append("市場温度低下")

            # ログ用にポジション側へ理由を埋めておく
            pos["_exit_force_reasons"] = reasons
            return True

        return False

    # === 通常判断 ===

    def decide(self, ctx, pos) -> str:
        """
        1. 条件崩壊なら即 "EXIT_NOW"
        2. 一定間隔以内なら LinUCB 呼ばず "HOLD"
        3. それ以外は LinUCB による方策選択
        """
        code = getattr(ctx, "code", "")

        # 1) 条件崩壊チェック（最優先）
        if self._should_force_exit(ctx, pos):
            pos["_exit_action"] = "EXIT_NOW"
            return "EXIT_NOW"

        # 2) クールダウン（exit 判断の連打を防ぐ）
        now_ms = getattr(ctx, "now_ms", int(time.time() * 1000))
        last = self.state["last_decide_ms"].get(code, 0)
        if now_ms - last < self.min_gap_ms:
            pos["_exit_action"] = "HOLD"
            return "HOLD"

        # 3) 強化学習による EXIT 方策選択
        x = exit_feature_vector(ctx, pos)
        a = self.state["exit_bandit"].select(x)
        self.state["last_decide_ms"][code] = now_ms

        pos["_feat_exit"] = x.reshape(-1).tolist()
        pos["_exit_action"] = a

        return a

    # === トレード終了後に報酬更新 ===

    def update_from_trade(self, trade: Dict[str, Any]) -> None:
        """
        trade 例:
        {
          "exit_action": "EXIT_NOW",
          "reward_bandit_exit": 0.15,
          "feat_exit": [...],
          ...
        }
        """
        a = trade.get("exit_action")
        r = trade.get("reward_bandit_exit")
        fx = trade.get("feat_exit")

        if a and fx is not None and isinstance(r, (int, float)):
            x = np.array(fx, dtype=float).reshape((-1, 1))
            self.state["exit_bandit"].update(a, x, float(r))
            self._save()
