from __future__ import annotations
# -*- coding: utf-8 -*-

import os
import json
import time
import numpy as np
from typing import Any, Dict

from bandit_linucb import LinUCB
from features import entry_feature_vector

# エントリー時に選ぶアクション（既存）
ACTIONS_ENTRY = ["PULLBACK_1T", "PULLBACK_2T", "ALLOW_BREAK", "SKIP"]


class EntryDecider:
    """
    EntryDecider — Lv8.0 対応版
    - 既存: LinUCB によるアクション選択（コンテキスト次元 d=9）
    - 追加: 寄り分岐・市場温度・薄商い・スプレッドによる「入場禁止ゲート」
      * spread が広すぎる → SKIP
      * market_heat が低すぎる → SKIP
      * vol_per_sec が低すぎる（出来高薄） → SKIP
    - すべて ctx に追加情報があれば使う。なければ安全寄りのデフォルト。
      * ctx.regime: "OPEN" / "AM" / "PM"
      * ctx.market_heat: 0〜1
      * ctx.vol_per_sec: float
      * ctx.spread_ticks: int
    """

    def __init__(
        self,
        model_path: str = "model_registry.json",
        alpha: float = 0.5,
        min_gap_ms: int = 10_000,
        # Lv8.0 用 追加パラメータ（デフォルト値は仕様の推奨から素直に設定）
        spread_limit_open: int = 2,
        spread_limit_normal: int = 3,
        min_market_heat: float = 0.40,
        min_market_heat_open: float = 0.50,
        min_vol_per_sec: float = 0.0,
    ):
        self.model_path = model_path
        self.alpha = alpha
        self.min_gap_ms = min_gap_ms

        # 寄り＆通常のスプレッド制限
        self.spread_limit_open = spread_limit_open
        self.spread_limit_normal = spread_limit_normal

        # 市場温度の下限
        self.min_market_heat = min_market_heat
        self.min_market_heat_open = min_market_heat_open

        # 極端に出来高が薄いときの下限（0 なら無効）
        self.min_vol_per_sec = min_vol_per_sec

        self._load()

    # ===== モデルのロード／セーブ =====

    def _load(self) -> None:
        """
        model_registry.json から LinUCB 状態を読み込む。
        なければ新規に d=9 の LinUCB を作成。
        """
        self.state: Dict[str, Any] = {
            "entry_bandit": None,
            "last_decide_ms": {},  # code -> last_decide_ms
        }

        if os.path.exists(self.model_path):
            try:
                with open(self.model_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                b = obj.get("_entry_bandit_")
                if b:
                    self.state["entry_bandit"] = LinUCB.from_json(b)
            except Exception:
                # 壊れていても新規作成すればよい
                pass

        if self.state["entry_bandit"] is None:
            # d=9 は既存 feature_vector に合わせる（増やさない）
            self.state["entry_bandit"] = LinUCB(ACTIONS_ENTRY, d=9, alpha=self.alpha)

    def _save(self) -> None:
        """
        LinUCB 状態だけを model_registry.json に書き戻す。
        """
        try:
            obj: Dict[str, Any] = {}
            if os.path.exists(self.model_path):
                with open(self.model_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
            obj["_entry_bandit_"] = self.state["entry_bandit"].to_json()
            tmp = self.model_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.model_path)
        except Exception:
            # セーブ失敗しても次回学習でまた試行するだけなので致命的ではない
            pass

    # ===== メイン判定 =====

    def _should_skip_by_cooldown(self, code: str, now_ms: int) -> bool:
        last = self.state["last_decide_ms"].get(code, 0)
        if now_ms - last < self.min_gap_ms:
            return True
        return False

    def _apply_entry_gates(self, ctx) -> str | None:
        """
        Lv8.0 の「入場禁止条件」をチェックする。
        SKIP したい場合は "SKIP" を返し、続行なら None を返す。
        """
        # regime: "OPEN" / "AM" / "PM"（なければ AM 扱い）
        regime = getattr(ctx, "regime", "AM")

        # spread チェック
        spread_ticks = getattr(ctx, "spread_ticks", None)
        if spread_ticks is not None:
            max_spread = (
                self.spread_limit_open if regime == "OPEN" else self.spread_limit_normal
            )
            if spread_ticks > max_spread:
                # ログ用に理由を埋めておく（あくまでオプション）
                setattr(ctx, "_entry_block_reason", "spread_too_wide")
                return "SKIP"

        # 市場温度チェック（0〜1）
        market_heat = float(getattr(ctx, "market_heat", 1.0))
        heat_threshold = (
            self.min_market_heat_open if regime == "OPEN" else self.min_market_heat
        )
        if market_heat < heat_threshold:
            setattr(ctx, "_entry_block_reason", "market_cold")
            return "SKIP"

        # 薄商いチェック（直近5分の出来高ペースなどを想定）
        vol_per_sec = getattr(ctx, "vol_per_sec", None)
        if (
            self.min_vol_per_sec > 0.0
            and vol_per_sec is not None
            and vol_per_sec < self.min_vol_per_sec
        ):
            setattr(ctx, "_entry_block_reason", "liquidity_too_low")
            return "SKIP"

        return None

    def decide(self, ctx) -> str:
        """
        エントリーアクションを決定する。
        - クールダウン未経過 / 入場禁止条件に該当 → "SKIP"
        - それ以外 → LinUCB で PULLBACK / ALLOW_BREAK / SKIP のいずれかを選択
        """
        now_ms = getattr(ctx, "now_ms", int(time.time() * 1000))
        code = getattr(ctx, "code", "")

        # クールダウン（エントリー連打防止）
        if self._should_skip_by_cooldown(code, now_ms):
            setattr(ctx, "_entry_block_reason", "cooldown")
            return "SKIP"

        # Lv8.0 の入場ゲート（寄り・市場温度・薄商い・スプレッド）
        gate_result = self._apply_entry_gates(ctx)
        if gate_result is not None:
            # gate が SKIP を返した場合、そのまま終了
            self.state["last_decide_ms"][code] = now_ms
            return gate_result

        # ここまで来たらバンディットでアクションを選ぶ
        x = entry_feature_vector(ctx)  # shape: (d, 1) 想定
        a = self.state["entry_bandit"].select(x)

        # 最終決定とログ用メタ
        self.state["last_decide_ms"][code] = now_ms
        ctx._feat_entry = x.reshape(-1).tolist()
        ctx._entry_action = a
        setattr(ctx, "_entry_block_reason", "bandit_ok")

        return a

    # ===== 学習（トレード結果からのアップデート）=====

    def update_from_trade(self, trade: Dict[str, Any]) -> None:
        """
        trade 例:
        {
          "entry_action": "PULLBACK_1T",
          "reward_bandit_entry": 0.12,
          "feat_entry": [...],
          ...
        }
        """
        a = trade.get("entry_action")
        r = trade.get("reward_bandit_entry")
        fx = trade.get("feat_entry")

        if a and fx is not None and isinstance(r, (int, float)):
            x = np.array(fx, dtype=float).reshape((-1, 1))
            self.state["entry_bandit"].update(a, x, float(r))
            self._save()
