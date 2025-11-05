# -*- coding: utf-8 -*-
import os
import io
import json
import pickle
import threading
from datetime import datetime
from typing import Dict, Any, Iterable

import numpy as np
from utils_common import ensure_dir

class ModelRegistry:
    """
    軽量モデル保管庫
    - API互換: load_all(syms) / predict(code, diff_ratio) / update_reward(code, profit) / save(code)
    - 追加: save_all(), get(code), set(code, model), to_json(code)
    - 安全化: 原子的保存（tmp→rename）, 破損pickleの自動復旧, デフォルト初期化
    """

    def __init__(self, model_dir: str = "models"):
        self.dir = model_dir
        ensure_dir(self.dir)
        self.models: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._default_model = {"w_buy": 0.0, "w_sell": 0.0, "reward_sum": 0.0, "updated": "-"}

    # -------------------- 基本I/O --------------------

    def _path(self, code: str) -> str:
        return os.path.join(self.dir, f"stock_{code}.pkl")

    def _tmp_path(self, code: str) -> str:
        return os.path.join(self.dir, f"stock_{code}.pkl.tmp")

    def _load_pickle(self, path: str) -> Dict[str, Any]:
        with open(path, "rb") as f:
            return pickle.load(f)

    def _safe_write_pickle(self, path: str, obj: Dict[str, Any]):
        """tmp→renameで原子的保存（Windowsでも安全側）"""
        tmp = path + ".tmp"
        # バイナリにダンプ
        with open(tmp, "wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    # -------------------- パブリックAPI --------------------

    def load_all(self, syms: Iterable):
        loaded = 0
        with self._lock:
            for s in syms:
                code = str(getattr(s, "code", s))
                path = self._path(code)
                m = None
                if os.path.exists(path):
                    try:
                        m = self._load_pickle(path)
                        if not isinstance(m, dict):
                            raise ValueError("model is not dict")
                        # 足りないキーを補完（後方互換）
                        for k, v in self._default_model.items():
                            m.setdefault(k, v)
                    except Exception:
                        # 破損時はバックアップjsonがあれば救出、それも無理ならデフォルト
                        try:
                            js = path.replace(".pkl", ".json")
                            if os.path.exists(js):
                                with open(js, "r", encoding="utf-8") as f:
                                    m = json.load(f)
                        except Exception:
                            m = None
                if m is None:
                    m = dict(self._default_model)
                self.models[code] = m
                loaded += 1
        print(f"[ModelRegistry] loaded {loaded} models")

    def get(self, code: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self.models.get(code, self._default_model))

    def set(self, code: str, model: Dict[str, Any]):
        with self._lock:
            m = dict(self._default_model)
            m.update(model or {})
            m["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            self.models[code] = m

    def predict(self, code: str, diff_ratio: float) -> float:
        with self._lock:
            m = self.models.get(code, self._default_model)
            w_buy = float(m.get("w_buy", 0.0))
            w_sell = float(m.get("w_sell", 0.0))
        score = np.tanh(diff_ratio * 50 + (w_buy - w_sell))
        return float(score)

    def update_reward(self, code: str, profit: float):
        with self._lock:
            m = self.models.get(code)
            if not m:
                m = dict(self._default_model)
                self.models[code] = m
            reward = float(np.tanh(float(profit) / 1000.0))
            if reward >= 0:
                m["w_buy"] = float(m.get("w_buy", 0.0)) + reward * 0.05
            else:
                m["w_sell"] = float(m.get("w_sell", 0.0)) + abs(reward) * 0.05
            m["reward_sum"] = float(m.get("reward_sum", 0.0)) + reward
            m["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.save(code)

    def to_json(self, code: str) -> str:
        with self._lock:
            m = self.models.get(code, self._default_model)
            return json.dumps(m, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def save(self, code: str):
        """単一モデルを安全に保存（pickle + 併せてjsonの軽バックアップを出力）"""
        with self._lock:
            path = self._path(code)
            m = self.models.get(code, self._default_model)
            ensure_dir(os.path.dirname(path))
            # pickle（原子的保存）
            self._safe_write_pickle(path, m)
            # JSONバックアップ（人間可読・復旧用）
            try:
                with open(path.replace(".pkl", ".json"), "w", encoding="utf-8") as f:
                    json.dump(m, f, ensure_ascii=False, indent=2)
            except Exception:
                # JSONは補助なので失敗しても致命ではない
                pass

    def save_all(self):
        with self._lock:
            for code in list(self.models.keys()):
                self.save(code)

