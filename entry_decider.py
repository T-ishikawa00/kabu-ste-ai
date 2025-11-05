from __future__ import annotations
import os, json, time, numpy as np
from bandit_linucb import LinUCB
from features import entry_feature_vector

ACTIONS_ENTRY = ["PULLBACK_1T","PULLBACK_2T","ALLOW_BREAK","SKIP"]

class EntryDecider:
    def __init__(self, model_path="model_registry.json", alpha=0.5, min_gap_ms=10_000):
        self.model_path = model_path; self.alpha = alpha; self.min_gap_ms = min_gap_ms
        self._load()

    def _load(self):
        self.state = {"entry_bandit": None, "last_decide_ms": {}}
        if os.path.exists(self.model_path):
            try:
                with open(self.model_path,"r",encoding="utf-8") as f: obj = json.load(f)
                b = obj.get("_entry_bandit_")
                if b: self.state["entry_bandit"] = LinUCB.from_json(b)
            except Exception: pass
        if self.state["entry_bandit"] is None:
            self.state["entry_bandit"] = LinUCB(ACTIONS_ENTRY, d=9, alpha=self.alpha)

    def _save(self):
        try:
            obj = {}
            if os.path.exists(self.model_path):
                with open(self.model_path,"r",encoding="utf-8") as f: obj = json.load(f)
            obj["_entry_bandit_"] = self.state["entry_bandit"].to_json()
            with open(self.model_path+".tmp","w",encoding="utf-8") as f:
                json.dump(obj,f,ensure_ascii=False,indent=2)
            os.replace(self.model_path+".tmp", self.model_path)
        except Exception: pass

    def decide(self, ctx) -> str:
        now = getattr(ctx, "now_ms", int(time.time()*1000))
        last = self.state["last_decide_ms"].get(ctx.code, 0)
        if now - last < self.min_gap_ms: return "SKIP"
        x = entry_feature_vector(ctx)
        a = self.state["entry_bandit"].select(x)
        self.state["last_decide_ms"][ctx.code] = now
        ctx._feat_entry = x.reshape(-1).tolist()
        ctx._entry_action = a
        return a

    def update_from_trade(self, trade):
        a = trade.get("entry_action"); r = trade.get("reward_bandit_entry"); fx = trade.get("feat_entry")
        if a and fx is not None and isinstance(r,(int,float)):
            x = np.array(fx,dtype=float).reshape((-1,1))
            self.state["entry_bandit"].update(a, x, float(r)); self._save()
