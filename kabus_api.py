# -*- coding: utf-8 -*-
import os
import time
import json
import logging
import requests
from typing import Optional, Iterable

class KabusAPI:
    """
    kabuステーションAPI v7想定
    - すべて /kabusapi/ 配下に統一
    - board は {symbol}@{exchange} 形式
    - token / register / positions / sendorder / orders を提供
    - QPSゲート/指数バックオフ/レイテンシ監視（cfg.safety）対応
    """

    def orders(self):
        """直近注文一覧を取得（Details内に HoldID が載る場合がある）"""
        def _do():
            self._qps_gate()
            t0 = time.perf_counter()
            r = self.s.get(self._url("orders"), timeout=self._to_get)
            self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
            self._latency_guard()
            text = r.text
            if r.status_code != 200:
                logging.error(f"orders HTTP{r.status_code}: {text}")
                r.raise_for_status()
            return r.json()

        res = self._safe_call(_do)
        return res if isinstance(res, list) else []


    def __init__(self, cfg):
        self.cfg = cfg

        # ---- ベースURL（/kabusapi付きに正規化）
        base = getattr(cfg.api, "base", "http://localhost:18080/kabusapi").rstrip("/")
        self.host = base if base.endswith("/kabusapi") else f"{base}/kabusapi"

        # ---- セッション（リトライは指数バックオフ側で面倒を見る）
        self.s = requests.Session()
        self.s.headers.update({"Content-Type": "application/json"})

        # ---- 認証トークン
        self.token: Optional[str] = None

        # ---- レート/計測
        self._req_ts: list[float] = []
        self._board_cache: dict = {}
        self.last_latency_ms: int = 0

        # ---- タイムアウト（configに無ければ既定）
        self._to_get = getattr(getattr(cfg, "api", object()), "timeout_get_sec", 5)
        self._to_post = getattr(getattr(cfg, "api", object()), "timeout_post_sec", 7)

        # ---- 安全設定
        safety = getattr(cfg, "safety", object())
        self._latency_limit_ms = int(getattr(safety, "api_latency_ms_limit", 1500))
        self._pause_on_latency = bool(getattr(safety, "pause_on_latency", True))

    # ======================================================
    # 内部ユーティリティ
    # ======================================================
    def _qps_gate(self):
        """1秒あたりのAPI呼び出し数を safety.api_qps_limit で制御"""
        lim = max(1, int(getattr(self.cfg.safety, "api_qps_limit", 5)))
        now = time.perf_counter()
        self._req_ts = [t for t in self._req_ts if now - t < 1.0]
        if len(self._req_ts) >= lim:
            sleep_sec = max(0.0, 1.0 - (now - self._req_ts[0]))
            if sleep_sec > 0:
                time.sleep(sleep_sec)
        self._req_ts.append(time.perf_counter())

    def _url(self, path: str) -> str:
        return f"{self.host}/{path.lstrip('/')}"

    def _safe_call(self, fn, *a, **kw):
        """requests系の一時的失敗を3回まで指数バックオフで再試行"""
        for i in range(3):
            try:
                return fn(*a, **kw)
            except requests.exceptions.RequestException as e:
                logging.warning(f"[API] {type(e).__name__}: {e}")
            except ValueError as e:
                # JSON decode error等
                logging.warning(f"[API] decode: {e}")
            time.sleep(0.5 * (i + 1))
        logging.error("[API] permanent failure")
        return None

    def _latency_guard(self):
        """直近APIレイテンシが閾値超なら一時停止（safety.pause_on_latency）"""
        if self._pause_on_latency and self.last_latency_ms > self._latency_limit_ms:
            # “スパイク直後に少し待って落ち着かせる”だけの軽い制御
            time.sleep(0.25)

    # ======================================================
    # 認証
    # ======================================================
    def auth(self):
        """
        トークン取得。
        パスワードは環境変数優先:
          - KABUS_API_PASSWORD を優先
          - それが無ければ cfg.api.api_password_env (例: "KABUS_API_PASSWORD") を参照
          - それも無ければ cfg.api.api_password（後方互換）
        """
        # ---- APIパスワードの解決
        env_name = getattr(self.cfg.api, "api_password_env", None)
        if env_name:
            api_pw = os.getenv(env_name)
        else:
            api_pw = os.getenv("KABUS_API_PASSWORD")

        if not api_pw:
            api_pw = getattr(self.cfg.api, "api_password", None)  # 後方互換

        if not api_pw:
            raise RuntimeError("APIパスワードが見つかりません（環境変数 or config）")

        def _do():
            r = self.s.post(self._url("token"),
                            json={"APIPassword": api_pw},
                            timeout=self._to_post)
            # kabuSのtokenは200以外で詳細がbodyに出ることがあるためraise前に確保
            text = r.text
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"_raw": text}

        res = self._safe_call(_do)
        if not res or not isinstance(res, dict) or not res.get("Token"):
            raise RuntimeError(f"token取得失敗: {res}")
        self.token = res["Token"]
        self.s.headers.update({"X-API-KEY": self.token})
        logging.info("token OK")

    ...
    # ======================================================
    # 銘柄登録
    # ======================================================
    def register_symbols(self, symbols):
        """
        symbols: 反復可能。
          - SymbolCfg 等: .code と .exchange を使用
          - (code, exchange) のタプル/リスト
          - 文字列/数値: code として扱い、exchange=1
        """
        def _do():
            items = []
            for s in symbols:
                if hasattr(s, "code") and hasattr(s, "exchange"):
                    items.append({"Symbol": str(s.code), "Exchange": int(s.exchange)})
                elif isinstance(s, (list, tuple)) and len(s) >= 2:
                    items.append({"Symbol": str(s[0]), "Exchange": int(s[1])})
                else:
                    items.append({"Symbol": str(s), "Exchange": 1})

            payload = {"Symbols": items}
            self._qps_gate()
            r = self.s.put(self._url("register"), json=payload, timeout=self._to_post)
            text = r.text
            if r.status_code != 200:
                logging.error(f"register HTTP{r.status_code}: {text}")
                r.raise_for_status()
            return r.json()

        res = self._safe_call(_do)
        if res is None:
            logging.error("register失敗: レスポンスなし")
            return None
        cnt = len(res.get("RegistList", []) or [])
        logging.info(f"register OK ({cnt}件)")
        return res

    def clear_register(self):
        """登録クリア（空配列PUTで解除）"""
        def _do():
            self._qps_gate()
            r = self.s.put(self._url("register"), json={"Symbols": []}, timeout=self._to_post)
            text = r.text
            if r.status_code != 200:
                logging.error(f"clear_register HTTP{r.status_code}: {text}")
                r.raise_for_status()
            return r.json()
        return self._safe_call(_do)


    # ======================================================
    # 板情報
    # ======================================================
    def board(self, code: str, ex: int):
        key = (str(code), int(ex))
        ttl = float(getattr(self.cfg.safety, "board_min_interval_ms", 200)) / 1000.0
        nowp = time.perf_counter()

        # キャッシュ（板は短TTLで再利用）
        if key in self._board_cache and nowp - self._board_cache[key]["ts"] < ttl:
            return self._board_cache[key]["data"]

        def _do():
            self._qps_gate()
            t0 = time.perf_counter()
            r = self.s.get(self._url(f"board/{code}@{ex}"), timeout=self._to_get)
            self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
            # レイテンシガードは“成功時のみ”かける
            self._latency_guard()
            text = r.text
            if r.status_code != 200:
                logging.error(f"board HTTP{r.status_code}: {text}")
                r.raise_for_status()
            return r.json()

        data = self._safe_call(_do)
        if isinstance(data, dict):
            self._board_cache[key] = {"ts": nowp, "data": data}
            return data
        logging.warning("board取得失敗: レスポンス不正")
        return {}

    # ======================================================
    # 建玉照会
    # ======================================================
    def positions(self, symbol: Optional[str] = None):
        def _do():
            self._qps_gate()
            t0 = time.perf_counter()
            r = self.s.get(self._url("positions"), timeout=self._to_get)
            self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
            self._latency_guard()
            text = r.text
            if r.status_code != 200:
                logging.error(f"positions HTTP{r.status_code}: {text}")
                r.raise_for_status()
            return r.json()

        res = self._safe_call(_do)
        if not isinstance(res, list):
            return []
        if symbol is None:
            return res
        s = str(symbol)
        return [p for p in res if str(p.get("Symbol")) == s]

    # ======================================================
    # 発注（/sendorder）
    # ======================================================
    def sendorder(self, payload: dict):
        """
        payload は /sendorder の仕様に適合していること（最小キー原則）
        例: 現物買い
            {"Password":"...","Symbol":"7203","Exchange":1,
             "Side":2,"CashMargin":1,"Qty":100,"FrontOrderType":10,"Price":0,"ExpireDay":0}
        """
        # 最低限の型チェック（数字が文字列で来た場合は事前補正）
        ints = ["Exchange", "Side", "Qty", "FrontOrderType", "Price", "ExpireDay"]
        for k in ints:
            if k in payload and isinstance(payload[k], str) and payload[k].isdigit():
                payload[k] = int(payload[k])

        def _do():
            self._qps_gate()
            t0 = time.perf_counter()
            r = self.s.post(self._url("sendorder"), json=payload, timeout=self._to_post)
            self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
            self._latency_guard()

            # kabuSは200以外でも本文に詳細が載るのでログに残してraise
            text = r.text
            if r.status_code != 200:
                logging.error(f"sendorder HTTP{r.status_code}: {text}")
                r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"_raw": text}

        res = self._safe_call(_do)
        if res is None:
            logging.error("sendorder失敗: レスポンスなし")
            return None
        logging.info(f"sendorder OK: {res}")
        return res



