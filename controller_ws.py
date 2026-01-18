# -*- coding: utf-8 -*-
"""
WebsocketController v16.1 (Fix Config Load)
===========================================
修正点:
1. config_loader から load_cfg をインポート
2. __init__ で config.yaml を読み込み、オブジェクト化してから親クラスに渡すように修正
   → これで 'str' object has no attribute 'api' エラーが消えます。
"""

import sys
import os
import json
import threading
import time
import logging
import websocket
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Set, List, Optional

# ★設定読み込みモジュールを追加
try:
    from config_loader import load_cfg
except ImportError:
    # パスが通っていない場合の対策
    sys.path.append(os.getcwd())
    from config_loader import load_cfg

from controller import Controller


class WebsocketController(Controller):
    """
    Phase 2: Websocket (PUSH API) 対応コントローラー
    v16.1: Config読み込み修正 & 分身モード対応
    """

    def __init__(self, config_path="config.yaml", symbols: Optional[List[str]] = None):
        # =========================================================
        # ★修正: 設定ファイルを「文字列」ではなく「オブジェクト」として読み込む
        # =========================================================
        try:
            cfg = load_cfg() # config.yaml をロード
        except Exception as e:
            # 読み込み失敗時のフォールバック（最低限のオブジェクトを作るかエラーにする）
            logging.error(f"Config load failed: {e}")
            raise e

        # 親クラスの初期化（ロード済みの cfg オブジェクトを渡す）
        super().__init__(cfg)

        # =========================================================
        # ★分身モード: 担当銘柄のフィルタリング
        # =========================================================
        if symbols is not None and len(symbols) > 0:
            logging.info(f"🤖 [分身モード] 指定された {len(symbols)} 銘柄のみを監視します")
            logging.debug(f"   Target: {symbols}")
            
            # config.yamlに定義されている設定を引き継ぎつつ、監視対象を絞る
            subset_syms = [s for s in self.syms if str(s.code) in symbols]
            
            # 警告: Configにない銘柄が指定された場合
            if len(subset_syms) < len(symbols):
                missing = set(symbols) - set([str(s.code) for s in subset_syms])
                logging.warning(f"⚠️ 指定銘柄の一部がConfigに見つかりません: {missing}")

            # 監視対象を上書き
            self.syms = subset_syms
            
            # マッピング情報も更新
            self.target_codes = [str(s.code) for s in self.syms]
            self.sym_map = {str(s.code): s for s in self.syms}
            
            logging.info(f"   -> フィルタ完了: {len(self.syms)} 銘柄をロードしました")
            
        else:
            logging.info(f"🤖 [本体モード] Configの全 {len(self.syms)} 銘柄を監視します")
        # =========================================================

        self.ws_url = getattr(self.cfg, "websocket_url", "ws://localhost:18080/kabusapi/websocket")
        self.ws = None
        self.is_running = False

        self._last_process_time: Dict[str, float] = {}
        self._process_interval = 1.0
        self._processing: Set[str] = set()
        self._processing_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="SymbolWorker")

        self._heartbeat_count = 0
        self._last_heartbeat_log = 0

        logging.info("🔧 WebsocketController v16.1 initialized")
        logging.info(f"  ⏱️ Process interval: {self._process_interval}s")
        logging.info(f"  🔢 Max workers: 3")

    def run(self):
        """メインループ"""
        self.is_running = True
        logging.info(f"🚀 Phase 2: Websocket Controller Starting... (Target: {len(self.syms)} syms)")

        try:
            self.api.auth()
            logging.info("🔑 API Auth Success")
        except Exception as e:
            logging.error(f"❌ API Auth Failed: {e}")
            return

        self._initialize_buffer()
        self._register_push()

        logging.info("🚫 Ghost-Buster thread DISABLED (v9.1+)")

        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="HeartbeatWorker",
            daemon=True
        )
        self.heartbeat_thread.start()

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
        self.is_running = False
        if self.ws:
            self.ws.close()
        self._executor.shutdown(wait=False)

    def _initialize_buffer(self):
        logging.info("⏳ Initializing history buffer...")
        for s in self.syms:
            code = str(s.code)
            try:
                self.process_symbol(code)
                time.sleep(0.2)
            except Exception as e:
                logging.warning(f"  Buffer init failed for {code}: {e}")

    def _register_push(self):
        logging.info("📡 Registering symbols for PUSH API...")
        try:
            self.api.register_symbols(self.syms)
            logging.info("✅ PUSH Registration completed.")
        except Exception as e:
            logging.error(f"❌ PUSH Registration failed: {e}")

    def _heartbeat_loop(self):
        while self.is_running:
            try:
                time.sleep(1.0)
                self._heartbeat_count += 1
                now = time.time()
                if now - self._last_heartbeat_log >= 10.0:
                    self._last_heartbeat_log = now
                    pos_count = len(self.brk.pos)
                    pending = len(self.brk._symbol_lock._pending) if hasattr(self.brk, '_symbol_lock') else 0
                    latency = getattr(self.api, 'last_latency_ms', 0)
                    logging.info(
                        f"HB i={self._heartbeat_count} pos={pos_count} latency={latency}ms syms={len(self.syms)}"
                    )
            except Exception as e:
                logging.error(f"Heartbeat Error: {e}")

    def _on_open(self, ws):
        logging.info("🔗 Websocket Connected.")

    def _on_error(self, ws, error):
        logging.error(f"❌ Websocket Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logging.info("🔒 Websocket Closed.")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            code = str(data.get("Symbol"))
            if code not in self.target_codes:
                return
            
            if code not in self.boards:
                self.boards[code] = {}
            self.boards[code].update(data)

            now = time.time()
            last_process = self._last_process_time.get(code, 0)
            has_position = code in self.brk.pos
            interval = 0.5 if has_position else self._process_interval

            if now - last_process < interval:
                return

            with self._processing_lock:
                if code in self._processing:
                    return
                self._processing.add(code)

            self._last_process_time[code] = now
            try:
                self._executor.submit(self._safe_process_symbol, code, self.boards[code])
            except Exception as e:
                logging.error(f"Executor submit error: {e}")
                with self._processing_lock:
                    self._processing.discard(code)

        except Exception as e:
            logging.error(f"Message Process Error: {e}")

    def _safe_process_symbol(self, code: str, board: dict):
        try:
            self.process_symbol(code, board)
        except Exception as e:
            logging.error(f"Process symbol error [{code}]: {e}")
        finally:
            with self._processing_lock:
                self._processing.discard(code)

    def process_symbol(self, code_s: str, board: dict = None):
        import datetime
        now = datetime.datetime.now()
        
        if code_s not in self.brk.pos:
            if now.hour == 9 and now.minute < 15:
                return
            if hasattr(self.brk, 'can_order'):
                can, reason = self.brk.can_order(code_s)
                if not can and reason in ["cooldown", "already_pending", "order_in_progress", "max_positions_reached"]:
                    return
        super().process_symbol(code_s, board)


if __name__ == "__main__":
    target_codes = None
    if len(sys.argv) > 1:
        raw_arg = sys.argv[1]
        target_codes = [x.strip() for x in raw_arg.split(",") if x.strip()]
    
    ctrl = WebsocketController(symbols=target_codes)
    ctrl.run()