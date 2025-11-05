from __future__ import annotations
import time
from collections import deque
from typing import Dict, Any, List, Tuple

class Roster:
    def __init__(self, max_active: int = 8, min_active: int = 5):
        self.max_active = int(max_active); self.min_active = int(min_active)
        self.pool: Dict[str, Dict[str, Any]] = {}
        self.active: List[str] = []
        self._last_rotate = 0.0

    def update(self, code: str, **kvs):
        d = self.pool.setdefault(str(code), {"score": 0.0, "add": 0, "drop": 0})
        d.update(kvs)
        spr = float(kvs.get("spread_ticks", 9e9))
        vol = float(kvs.get("vol5", 0.0))
        obi = float(kvs.get("upticks", 0.0))
        # ごく簡易のスコア
        sc = (max(0.0, 1.0 - min(spr, 5.0)/5.0) * 0.5) + (min(vol/12000.0,1.0) * 0.4) + (min(obi/5.0,1.0) * 0.1)
        d["score"] = sc

    def rotate_active(self) -> Tuple[List[str], List[str]]:
        now = time.time()
        if now - self._last_rotate < 15.0:
            return [], []
        self._last_rotate = now

        # 上位で補充
        want = self.max_active - len(self.active)
        if want > 0:
            cands = sorted([(c, v["score"]) for c, v in self.pool.items() if c not in self.active], key=lambda x: x[1], reverse=True)
            add = [c for c,_ in cands[:want]]
        else:
            add = []

        # 下位で間引き（多すぎる分だけ）
        drop = []
        if len(self.active) > self.min_active:
            ranked = sorted([(c, self.pool.get(c,{}).get("score",0.0)) for c in self.active], key=lambda x: x[1])
            over = max(0, len(self.active) - self.max_active)
            drop = [c for c,_ in ranked[:over]]

        # 更新
        if add or drop:
            s = set(self.active)
            for c in drop:
                s.discard(c)
            for c in add:
                s.add(c)
            self.active = list(s)

        return add, drop
