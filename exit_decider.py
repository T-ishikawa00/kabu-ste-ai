from __future__ import annotations
import os, json, time, numpy as np
from bandit_linucb import LinUCB
from features import exit_feature_vector

ACTIONS_EXIT = ["HOLD","EXIT_NOW","TIGHTEN_BE","ARM_TP3"]

class ExitDecider:
    """
    Lv7.0 統合版 ExitDecider
    条件崩壊による強制EXIT + LinUCBによる方策選択を両立
    """
    def __init__(self, model_path="model_registry.json", alpha=0.5, min_gap_ms=30_000):
        self.model_path = model_path
        self.alpha = alpha
        self.min_gap_ms = min_gap_ms
        self.theta_drop = 0.40        # heat低下しきい値
        self.obi_flip_thr = 0.10      # OBI反転差
        self.vol_cool_ratio = 0.5     # 出来高冷え比率
        self.max_hold_sec = 120       # 最大保有時間
        self._load()

    # === モデルロード ===
    def _load(self):
        self.state = {"exit_bandit": None, "last_decide_ms": {}}
        if os.path.exists(self.model_path):
            try:
                with open(self.model_path,"r",encoding="utf-8") as f:
                    obj = json.load(f)
                b = obj.get("_exit_bandit_")
                if b:
                    self.state["exit_bandit"] = LinUCB.from_json(b)
            except Exception:
                pass
        if self.state["exit_bandit"] is None:
            self.state["exit_bandit"] = LinUCB(ACTIONS_EXIT, d=10, alpha=self.alpha)

    # === 保存 ===
    def _save(self):
        try:
            obj = {}
            if os.path.exists(self.model_path):
                with open(self.model_path,"r",encoding="utf-8") as f:
                    obj = json.load(f)
            obj["_exit_bandit_"] = self.state["exit_bandit"].to_json()
            with open(self.model_path+".tmp","w",encoding="utf-8") as f:
                json.dump(obj,f,ensure_ascii=False,indent=2)
            os.replace(self.model_path+".tmp", self.model_path)
        except Exception:
            pass

    # === 条件崩壊チェック ===
    def _should_force_exit(self, ctx, pos) -> bool:
        s = getattr(ctx, "state", {})
        heat = s.get("heat", 0.0)
        obi_buy = s.get("obi_buy", 0.0)
        obi_sell = s.get("obi_sell", 0.0)
        vol_recent = s.get("vol_recent", 1.0)
        vol_avg = s.get("vol_avg", 1.0)
        hold_sec = s.get("hold_sec", 0)
        side = pos.get("side", "BUY")

        cond_heat = heat < self.theta_drop
        cond_vol = vol_recent < vol_avg * self.vol_cool_ratio
        cond_time = hold_sec > self.max_hold_sec
        cond_obi = False
        if side == "BUY":
            cond_obi = (obi_sell - obi_buy > self.obi_flip_thr)
        elif side == "SELL":
            cond_obi = (obi_buy - obi_sell > self.obi_flip_thr)

        if cond_heat or cond_vol or cond_obi or cond_time:
            reasons = []
            if cond_heat: reasons.append("heat低下")
            if cond_vol: reasons.append("出来高冷え")
            if cond_obi: reasons.append("OBI反転")
            if cond_time: reasons.append("時間切れ")
            print(f"[ExitDecider] 条件崩壊: {','.join(reasons)} → EXIT_NOW")
            return True
        return False

    # === 通常判断 ===
    def decide(self, ctx, pos) -> str:
        # 条件崩壊チェック（先に適用）
        if self._should_force_exit(ctx, pos):
            return "EXIT_NOW"

        now = getattr(ctx, "now_ms", int(time.time()*1000))
        last = self.state["last_decide_ms"].get(ctx.code, 0)
        if now - last < self.min_gap_ms:
            return "HOLD"

        # 強化学習による方策選択
        x = exit_feature_vector(ctx, pos)
        a = self.state["exit_bandit"].select(x)
        self.state["last_decide_ms"][ctx.code] = now
        pos["_feat_exit"] = x.reshape(-1).tolist()
        pos["_exit_action"] = a
        return a

    # === トレード終了後に報酬更新 ===
    def update_from_trade(self, trade):
        a = trade.get("exit_action")
        r = trade.get("reward_bandit_exit")
        fx = trade.get("feat_exit")
        if a and fx is not None and isinstance(r, (int, float)):
            x = np.array(fx, dtype=float).reshape((-1,1))
            self.state["exit_bandit"].update(a, x, float(r))
            self._save()
