# -*- coding: utf-8 -*-
"""
telemetry.py v22.5 (Core State Logger)
======================================
システムの「現在の状態（State）」をJSONファイルとして保存する機能。
ダッシュボードや、再起動時の状態復元に使用されます。

【v22.5 変更点】
- 旧バンディット（LinUCB）用の学習補助関数を全削除
- シンプルかつ高速なI/Oに特化
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
        
        # システムの現在状態
        self.state: Dict[str, Any] = {
            "ts": jst_iso(),
            "mode": "init",
            "positions": {},
            "latency_ms": 0,
            "version": "v22.5"
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
        """
        状態をJSONへ書き出し。
        連続書き込みによる負荷を防ぐため、min_intervalの間隔を空ける。
        """
        nowt = time.time()
        if not force and (nowt - self._last_write) < self._min_interval:
            return

        # タイムスタンプ更新
        self.state["ts"] = jst_iso()
        
        tmp = self.path + ".tmp"
        try:
            # 1. 一時ファイルに書き込む (Atomic Write Pattern)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno()) # ディスク同期
            
            # 2. リネームしてアトミックに差し替え
            os.replace(tmp, self.path)
            self._last_write = nowt
            
        except Exception as e:
            logging.warning(f"Telemetry write failed: {e}")
            # ゴミ掃除
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except:
                pass

    def save_atomic(self, data: Dict[str, Any]):
        """Controllerから呼ばれるショートカット"""
        self.update(data)
        self.write(force=True)