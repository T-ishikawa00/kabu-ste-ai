# -*- coding: utf-8 -*-
"""
KabusAPI v9.1 (Error Detail Edition)
==============================================
変更点:
- sendorderメソッドがエラー時に None ではなく
  {"Result": -1, "Message": "エラー詳細"} を返すように修正。
  これにより broker.py でエラー原因を特定可能にする。
"""

import os
import time
import json
import logging
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, List


class KabusAPI:
    """
    KabusAPI v9.1
    - レート制限の厳格化
    - エラーハンドリングの改善
    - sendorderの詳細エラー返却対応
    """

    def __init__(self, cfg):
        self.cfg = cfg

        # ベースURL
        base = getattr(cfg.api, "base", "http://localhost:18080/kabusapi").rstrip("/")
        self.host = base if base.endswith("/kabusapi") else f"{base}/kabusapi"

        # セッション設定（リトライ戦略）
        self.s = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
        self.s.mount("http://", adapter)
        self.s.mount("https://", adapter)
        self.s.headers.update({"Content-Type": "application/json"})

        self.token: Optional[str] = None
        self._req_ts: List[float] = []
        self._board_cache: dict = {}
        self.last_latency_ms: int = 0

        # タイムアウト設定
        cfg_get = getattr(getattr(cfg, "api", object()), "timeout_get_sec", 10.0)
        cfg_post = getattr(getattr(cfg, "api", object()), "timeout_post_sec", 15.0)
        self._to_get = max(10.0, float(cfg_get))
        self._to_post = max(15.0, float(cfg_post))

        # レイテンシ設定
        safety = getattr(cfg, "safety", object())
        self._latency_limit_ms = int(getattr(safety, "api_latency_ms_limit", 1500))
        self._pause_on_latency = bool(getattr(safety, "pause_on_latency", True))

        # sendorder専用のレート制限
        self._sendorder_lock = threading.Lock()
        self._last_sendorder_time = 0.0
        self._sendorder_min_interval = 1.0 

        # エラー時のバックオフ
        self._error_backoff_until = 0.0
        self._consecutive_errors = 0

        logging.info(f"🔌 KabusAPI v9.1 initialized")
        logging.info(f"  📡 Host: {self.host}")

    def _qps_gate(self):
        """QPS制限ゲート"""
        lim = max(1, int(getattr(self.cfg.safety, "api_qps_limit", 2)))
        now = time.perf_counter()
        self._req_ts = [t for t in self._req_ts if now - t < 1.0]

        if len(self._req_ts) >= lim:
            sleep_sec = max(0.0, 1.0 - (now - self._req_ts[0]))
            if sleep_sec > 0:
                time.sleep(sleep_sec)

        self._req_ts.append(time.perf_counter())

    def _url(self, path: str) -> str:
        return f"{self.host}/{path.lstrip('/')}"

    def _check_backoff(self) -> bool:
        if time.time() < self._error_backoff_until:
            return True
        return False

    def _record_error(self, error_type: str):
        self._consecutive_errors += 1
        backoff_sec = min(30, 2 ** self._consecutive_errors)
        self._error_backoff_until = time.time() + backoff_sec
        logging.warning(f"[API] エラー記録: {error_type} (Wait {backoff_sec}s)")

    def _record_success(self):
        if self._consecutive_errors > 0:
            logging.debug(f"[API] 復旧: 連続エラー{self._consecutive_errors}回からリセット")
        self._consecutive_errors = 0
        self._error_backoff_until = 0.0

    def _safe_call(self, fn, *a, **kw):
        """安全なAPI呼び出しラッパー"""
        if self._check_backoff():
            return None

        try:
            result = fn(*a, **kw)
            self._record_success()
            return result

        except requests.exceptions.Timeout:
            logging.warning("[API] Timeout発生")
            self._record_error("timeout")

        except requests.exceptions.ConnectionError as e:
            logging.warning(f"[API] ConnectionError: {e}")
            self._record_error("connection")

        except Exception as e:
            logging.error(f"[API] Unexpected error: {e}")
            self._record_error("unknown")

        return None

    def _latency_guard(self):
        if self._pause_on_latency and self.last_latency_ms > self._latency_limit_ms:
            time.sleep(0.5)

    # ==========================================
    # 認証
    # ==========================================
    def auth(self):
        env_name = getattr(self.cfg.api, "api_password_env", None)
        if env_name:
            api_pw = os.getenv(env_name)
        else:
            api_pw = os.getenv("KABUS_API_PASSWORD")
        if not api_pw:
            api_pw = getattr(self.cfg.api, "api_password", None)
        if not api_pw:
            raise RuntimeError("APIパスワード設定なし")

        def _do():
            r = self.s.post(
                self._url("token"),
                json={"APIPassword": api_pw},
                timeout=self._to_post
            )
            r.raise_for_status()
            return r.json()

        res = self._safe_call(_do)
        if not res or not res.get("Token"):
            raise RuntimeError(f"token取得失敗: {res}")

        self.token = res["Token"]
        self.s.headers.update({"X-API-KEY": self.token})
        logging.info("🔑 API Token OK")

    # ==========================================
    # 銘柄登録
    # ==========================================
    def register_symbols(self, symbols):
        def _do():
            items = []
            for s in symbols:
                if hasattr(s, "code") and hasattr(s, "exchange"):
                    items.append({"Symbol": str(s.code), "Exchange": int(s.exchange)})
                else:
                    items.append({"Symbol": str(s), "Exchange": 1})

            self._qps_gate()
            r = self.s.put(self._url("register"), json={"Symbols": items}, timeout=self._to_post)
            if r.status_code != 200:
                r.raise_for_status()
            return r.json()

        return self._safe_call(_do)

    def clear_register(self):
        def _do():
            self._qps_gate()
            r = self.s.put(self._url("register"), json={"Symbols": []}, timeout=self._to_post)
            return r.json()
        return self._safe_call(_do)

    # ==========================================
    # 板情報
    # ==========================================
    def board(self, code: str, ex: int):
        key = (str(code), int(ex))
        ttl = float(getattr(self.cfg.safety, "board_min_interval_ms", 500)) / 1000.0
        nowp = time.perf_counter()

        if key in self._board_cache and nowp - self._board_cache[key]["ts"] < ttl:
            return self._board_cache[key]["data"]

        def _do():
            self._qps_gate()
            t0 = time.perf_counter()
            r = self.s.get(self._url(f"board/{code}@{ex}"), timeout=self._to_get)
            self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
            self._latency_guard()

            if r.status_code == 401:
                self.auth()
                r = self.s.get(self._url(f"board/{code}@{ex}"), timeout=self._to_get)

            if r.status_code != 200:
                r.raise_for_status()
            return r.json()

        data = self._safe_call(_do)
        if isinstance(data, dict):
            self._board_cache[key] = {"ts": nowp, "data": data}
            return data
        return {}

    # ==========================================
    # 建玉照会
    # ==========================================
    def positions(self, symbol: Optional[str] = None):
        def _do():
            self._qps_gate()
            t0 = time.perf_counter()
            r = self.s.get(self._url("positions"), timeout=self._to_get)
            self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
            self._latency_guard()
            if r.status_code != 200: r.raise_for_status()
            return r.json()

        res = self._safe_call(_do)
        if res is None: return None
        if not isinstance(res, list): return []
        if symbol is None: return res
        s = str(symbol)
        return [p for p in res if str(p.get("Symbol")) == s]

    # ==========================================
    # 発注 (sendorder) - 詳細エラー対応版
    # ==========================================
    def sendorder(self, payload: dict):
        """
        発注（エラー時に詳細メッセージを返す）
        """
        # 型変換
        ints = [
            "Exchange", "Side", "Qty", "FrontOrderType", "Price",
            "ExpireDay", "MarginTradeType", "DelivType", "AccountType",
            "FundType", "CashMargin", "SecurityType"
        ]
        for k in ints:
            if k in payload and isinstance(payload[k], str) and payload[k].isdigit():
                payload[k] = int(payload[k])

        with self._sendorder_lock:
            now = time.time()
            elapsed = now - self._last_sendorder_time
            if elapsed < self._sendorder_min_interval:
                time.sleep(self._sendorder_min_interval - elapsed)
            self._last_sendorder_time = time.time()

            if self._check_backoff():
                return {"Result": -1, "Message": "API Backoff Mode (Too many errors)"}

            try:
                self._qps_gate()
                t0 = time.perf_counter()
                r = self.s.post(self._url("sendorder"), json=payload, timeout=20.0)
                self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
                self._latency_guard()

                # HTTPエラーのハンドリング
                if r.status_code != 200:
                    if r.status_code == 429:
                        self._record_error("rate_limit")
                        return {"Result": -1, "Message": "HTTP 429: API Rate Limit"}
                    
                    self._record_error(f"http_{r.status_code}")
                    return {"Result": -1, "Message": f"HTTP {r.status_code}: {r.text}"}

                # 成功時
                self._record_success()
                return r.json()

            except Exception as e:
                self._record_error("exception")
                logging.error(f"[API] sendorder exception: {e}")
                return {"Result": -1, "Message": str(e)}

    # ==========================================
    # 注文キャンセル
    # ==========================================
    def cancelorder(self, payload: dict):
        def _do():
            self._qps_gate()
            r = self.s.put(self._url("cancelorder"), json=payload, timeout=self._to_post)
            return r.json()
        return self._safe_call(_do)

    # ==========================================
    # 注文一覧
    # ==========================================
    def orders(self):
        def _do():
            self._qps_gate()
            t0 = time.perf_counter()
            r = self.s.get(self._url("orders"), timeout=self._to_get)
            self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
            return r.json()
        res = self._safe_call(_do)
        return res if isinstance(res, list) else []

    # ==========================================
    # ステータス
    # ==========================================
    def get_status(self) -> dict:
        return {
            "host": self.host,
            "authenticated": bool(self.token),
            "last_latency_ms": self.last_latency_ms,
            "consecutive_errors": self._consecutive_errors,
            "in_backoff": time.time() < self._error_backoff_until
        }