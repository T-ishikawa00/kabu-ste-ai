# -*- coding: utf-8 -*-
"""
WebsocketController v9.1 (Ghost-Free Edition)
============================================================
v9.1 変更点:
- ゴースト監視スレッドを無効化（誤検出問題の回避）
- シンプルで安定した動作を優先
"""

import sys
import os
import json
import threading
import time
import logging
import websocket
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Set

from controller import Controller


class WebsocketController(Controller):
    """
    Phase 2: Websocket (PUSH API) 対応コントローラー
    v9.1: ゴースト監視を無効化してシンプル化
    """

    def __init__(self, config_path="config.yaml"):
        super().__init__(config_path)
        self.ws_url = getattr(self.cfg, "websocket_url", "ws://localhost:18080/kabusapi/websocket")
        self.ws = None
        self.is_running = False

        # 銘柄別の最終処理時刻
        self._last_process_time: Dict[str, float] = {}
        self._process_interval = 1.0  # 同一銘柄の処理間隔（秒）

        # 処理中フラグ（二重実行防止）
        self._processing: Set[str] = set()
        self._processing_lock = threading.Lock()

        # スレッドプールで並列処理を制限
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="SymbolWorker")

        # ハートビート用カウンター
        self._heartbeat_count = 0
        self._last_heartbeat_log = 0

        logging.info("🔧 WebsocketController v9.1 initialized (Ghost-Free)")
        logging.info(f"  ⏱️ Process interval: {self._process_interval}s")
        logging.info(f"  🔢 Max workers: 3")

    def run(self):
        """メインループ (Websocket + 監視スレッド起動)"""
        self.is_running = True
        logging.info(f"🚀 Phase 2: Websocket Controller Starting... (Target: {len(self.syms)} syms)")

        # 1. 認証 (HTTP 401対策)
        try:
            self.api.auth()
            logging.info("🔑 API Auth Success")
        except Exception as e:
            logging.error(f"❌ API Auth Failed: {e}")
            return

        # 2. 初期データ収集
        self._initialize_buffer()

        # 3. PUSH登録
        self._register_push()

        # 4. ★ゴースト回収監視スレッドは起動しない★
        # 理由: 指値注文の約定待ち中に誤検出し、正常な取引を妨害するため
        logging.info("🚫 Ghost-Buster thread DISABLED (v9.1)")

        # 5. ハートビートスレッド
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="HeartbeatWorker",
            daemon=True
        )
        self.heartbeat_thread.start()

        # 6. Websocket接続
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open
        )

        while self.is_running:
            try:
                self.ws.run_forever()
                if self.is_running:
                    logging.warning("🔄 Reconnecting to Websocket in 3 seconds...")
                    time.sleep(3)
            except KeyboardInterrupt:
                logging.info("🛑 User Interrupted")
                self.stop()
                break
            except Exception as e:
                logging.error(f"Websocket Fatal Error: {e}")
                time.sleep(5)

    def stop(self):
        """停止処理"""
        self.is_running = False
        if self.ws:
            self.ws.close()
        self._executor.shutdown(wait=False)

    def _initialize_buffer(self):
        """初期バッファの構築"""
        logging.info("⏳ Initializing history buffer...")
        for s in self.syms:
            code = str(s.code)
            try:
                self.process_symbol(code)
                time.sleep(0.2)  # API負荷軽減
            except Exception as e:
                logging.warning(f"  Buffer init failed for {code}: {e}")

    def _register_push(self):
        """監視対象銘柄をPUSH配信に登録する"""
        logging.info("📡 Registering symbols for PUSH API...")
        try:
            self.api.register_symbols(self.syms)
            logging.info("✅ PUSH Registration completed.")
        except Exception as e:
            logging.error(f"❌ PUSH Registration failed: {e}")

    # ==========================================
    # ハートビート
    # ==========================================
    def _heartbeat_loop(self):
        """定期的な状態確認とログ出力"""
        while self.is_running:
            try:
                time.sleep(1.0)
                self._heartbeat_count += 1

                # 10秒ごとに状態を出力
                now = time.time()
                if now - self._last_heartbeat_log >= 10.0:
                    self._last_heartbeat_log = now

                    # 状態サマリー
                    pos_count = len(self.brk.pos)
                    pending = len(self.brk._symbol_lock._pending) if hasattr(self.brk, '_symbol_lock') else 0
                    latency = getattr(self.api, 'last_latency_ms', 0)

                    logging.info(
                        f"HB i={self._heartbeat_count} "
                        f"paper={self.cfg.paper_trading} "
                        f"pos={pos_count} "
                        f"pending={pending} "
                        f"latency_ms={latency}"
                    )

            except Exception as e:
                logging.error(f"Heartbeat Error: {e}")

    # ==========================================
    # Websocket Callbacks
    # ==========================================
    def _on_open(self, ws):
        logging.info("🔗 Websocket Connected.")

    def _on_error(self, ws, error):
        logging.error(f"❌ Websocket Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logging.info("🔒 Websocket Closed.")

    def _on_message(self, ws, message):
        """
        Websocketメッセージ受信時の処理
        """
        try:
            data = json.loads(message)
            code = str(data.get("Symbol"))

            # 対象外の銘柄はスキップ
            if code not in self.target_codes:
                return

            # ボードキャッシュ更新
            if code not in self.boards:
                self.boards[code] = {}
            self.boards[code].update(data)

            # レート制限チェック
            now = time.time()
            last_process = self._last_process_time.get(code, 0)

            # ポジションがある場合は短い間隔で処理（EXIT判定のため）
            has_position = code in self.brk.pos
            interval = 0.5 if has_position else self._process_interval

            if now - last_process < interval:
                return

            # 処理中チェック（二重実行防止）
            with self._processing_lock:
                if code in self._processing:
                    return
                self._processing.add(code)

            # 最終処理時刻を更新
            self._last_process_time[code] = now

            # スレッドプールで処理
            try:
                self._executor.submit(self._safe_process_symbol, code, self.boards[code])
            except Exception as e:
                logging.error(f"Executor submit error: {e}")
                with self._processing_lock:
                    self._processing.discard(code)

        except Exception as e:
            logging.error(f"Message Process Error: {e}")

    def _safe_process_symbol(self, code: str, board: dict):
        """
        安全なシンボル処理ラッパー
        """
        try:
            self.process_symbol(code, board)
        except Exception as e:
            logging.error(f"Process symbol error [{code}]: {e}")
        finally:
            with self._processing_lock:
                self._processing.discard(code)

    # ==========================================
    # オーバーライド: process_symbol の前処理
    # ==========================================
    def process_symbol(self, code_s: str, board: dict = None):
        """
        1銘柄ぶんのトレード判断ロジック
        """
        # ポジションがない場合のみ発注可否チェック
        if code_s not in self.brk.pos:
            if hasattr(self.brk, 'can_order'):
                can, reason = self.brk.can_order(code_s)
                if not can and reason in ["cooldown", "already_pending", "order_in_progress", "max_positions_reached"]:
                    return

        # 親クラスのprocess_symbolを呼び出し
        super().process_symbol(code_s, board)