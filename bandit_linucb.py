from __future__ import annotations
import numpy as np, json

class LinUCB:
    def __init__(self, actions, d, alpha=0.5):
        self.actions = list(actions)
        self.d = int(d); self.alpha = float(alpha)
        self.A = {a: np.eye(d) for a in self.actions}
        self.b = {a: np.zeros((d,1)) for a in self.actions}

    def _theta(self, a):
        A_inv = np.linalg.inv(self.A[a])
        return A_inv @ self.b[a], A_inv

    def select(self, x):
        x = np.array(x, dtype=float).reshape((-1,1))
        best_a, best_s = None, -1e9
        for a in self.actions:
            theta, A_inv = self._theta(a)
            mu = float(theta.T @ x)
            var = float(np.sqrt(x.T @ A_inv @ x))
            s = mu + self.alpha * var
            if s > best_s: best_a, best_s = a, s
        return best_a

    def update(self, a, x, r):
        x = np.array(x, dtype=float).reshape((-1,1))
        self.A[a] += x @ x.T
        self.b[a] += float(r) * x

    def to_json(self): 
        return {"actions": self.actions, "d": self.d, "alpha": self.alpha,
                "A": {k: self.A[k].tolist() for k in self.actions},
                "b": {k: self.b[k].reshape(-1).tolist() for k in self.actions}}

    @classmethod
    def from_json(cls, obj):
        import numpy as np
        inst = cls(obj["actions"], obj["d"], obj.get("alpha",0.5))
        inst.A = {k: np.array(obj["A"][k]) for k in inst.actions}
        inst.b = {k: np.array(obj["b"][k]).reshape((-1,1)) for k in inst.actions}
        return inst
