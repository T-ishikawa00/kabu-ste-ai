# -*- coding: utf-8 -*-
"""
WebsocketController v19.3 (Force Universal Optimized)
=====================================================
【v19.3 変更点】
1. AI入力を 19次元 (VWAP乖離率含む) に対応。
2. フォルダの個別モデルを無視し、強制的に model_universal_v19 を全員に適用。
3. v19親クラス(Controller)のロジックと完全に同期。
"""

import sys
import os
import json
import threading
import time
import logging
import websocket
import random
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Set, List, Optional

try:
    from config_loader import load_cfg
except ImportError:
    sys.path.append(os.getcwd())
    from config_loader import load_cfg

# v19.3 の親クラスをインポート
from controller import Controller

JST = timezone(timedelta(hours=9))


class WebsocketController(Controller):
    def __init__(self, config_path="config.yaml", symbols: Optional[List[str]] = None):
        try:
            cfg = load_cfg()
        except Exception as e:
            logging.error(f"Config load failed: {e}")
            raise e

        # 親クラスの初期化（ここで v19 基準の初期設定が行われる）
        super().__init__(cfg)

        if symbols is not None and len(symbols) > 0:
            logging.warning(f"🤖 [分身モード] {len(symbols)} 銘柄監視")
            subset_syms = [s for s in self.syms if str(s.code) in symbols]
            self.syms = subset_syms
            self.target_codes = [str(s.code) for s in self.syms]
            self.sym_map = {str(s.code): s for s in self.syms}
        else:
            logging.warning(f"🤖 [本体モード] {len(self.syms)} 銘柄監視")

        # ==========================================
        # ★修正: WS側でも強制汎用モデルモードを適用
        # ==========================================
        logging.warning("🤖 [WS] AIモデル再配置 (Force Universal v19)...")
        if self.universal_model:
            for code in self.target_codes:
                self.ai_models[code] = self.universal_model
            logging.warning(f"   ✅ 全 {len(self.target_codes)} 銘柄に汎用モデルを強制適用しました")
        else:
            logging.error("   ❌ 汎用モデル(v19)が親クラスでロードされていません！")

        self.ws_url = getattr(self.cfg, "websocket_url", "ws://localhost:18080/kabusapi/websocket")
        self.ws = None
        self.is_running = False
        
        # 処理間引き
        self._last_process_time: Dict[str, float] = {}
        self._process_interval = 0.5 
        self._processing: Set[str] = set()
        self._processing_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="SymWorker")
        
        # ハートビート
        self._heartbeat_count = 0
        self._last_status_log = 0

        logging.warning("🔧 WebsocketController v19.3 initialized")

    def run(self):
        self.is_running = True
        
        if hasattr(self, 'notifier'):
            self.notifier.send("🤖 Bot起動 (v19.3 Force Universal/WS)", "startup")
        
        logging.warning(f"🚀 Starting WS Mode... ({len(self.syms)} symbols)")
        
        try:
            self.api.auth()
        except Exception as e:
            logging.error(f"❌ API Auth Error: {e}")
            return

        if not self.cfg.paper_trading:
            self._recover_ghost_positions()

        # 起動時の初期データ取得
        self._initialize_buffer()
        self._register_push()
        
        # ハートビート
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        
        # WebSocket接続
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
                    logging.warning("🔄 WebSocket再接続中...")
                    time.sleep(3)
            except Exception as e:
                logging.error(f"WebSocket error: {e}")
                time.sleep(3)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            code = str(data.get("Symbol"))
            
            if code not in self.target_codes:
                return
            
            # ボード更新（process_symbolで使用）
            if code not in self.boards:
                self.boards[code] = {}
            self.boards[code].update(data)
            
            # 処理間引き
            now_t = time.time()
            last_time = self._last_process_time.get(code, 0)
            
            # ポジション保有中は反応速度を上げる
            has_pos = code in self.brk.pos
            interval = 0.2 if has_pos else self._process_interval
            
            if now_t - last_time < interval:
                return
            
            # 多重処理防止
            with self._processing_lock:
                if code in self._processing:
                    return
                self._processing.add(code)
            
            self._last_process_time[code] = now_t
            
            # 非同期で処理実行（親クラス v19.3 のロジックへ）
            self._executor.submit(self._safe_process_symbol, code, self.boards[code])
            
        except Exception as e:
            logging.debug(f"on_message error: {e}")

    def _safe_process_symbol(self, code: str, board: dict):
        try:
            self.process_symbol(code, board)
        except Exception as e:
            logging.error(f"Process error [{code}]: {e}")
        finally:
            with self._processing_lock:
                self._processing.discard(code)

    def process_symbol(self, code_s: str, board: dict = None):
        """
        ★オーバーライド: v19.3 基準の時間ガード
        """
        now_jst = datetime.now(JST)
        hm = now_jst.strftime("%H:%M")
        
        # 昼休み完全停止
        if "11:30" <= hm < "12:25":
            return

        # エントリー禁止時間帯（ポジションなしの場合のみ）
        if code_s not in self.brk.pos:
            # 寄り付き直後の激動を避ける
            if now_jst.hour == 9 and now_jst.minute < 15:
                return
            if now_jst.hour == 12 and 30 <= now_jst.minute < 35:
                return
        
        # 親クラス(v19.3)の19次元AI判定・トレードロジックを実行
        super().process_symbol(code_s, board)

    def _initialize_buffer(self):
        logging.warning("⏳ 初期データ取得中...")
        for s in self.syms:
            try:
                board = self.api.board(s.code, s.exchange)
                if board:
                    code_s = str(s.code)
                    self.boards[code_s] = board
                    # 指標初期計算
                    self._update_indicators(code_s, board)
                time.sleep(0.1) 
            except Exception as e:
                logging.debug(f"Init error [{s.code}]: {e}")
        logging.warning(f"✅ 初期化完了 ({len(self.boards)} symbols)")

    def _register_push(self):
        try: self.api.register_symbols(self.syms)
        except Exception as e: logging.warning(f"PUSH登録エラー: {e}")

    def _heartbeat_loop(self):
        while self.is_running:
            try:
                time.sleep(10.0)
                pos_info = f" pos={list(self.brk.pos.keys())}" if self.brk.pos else ""
                logging.warning(f"💓 HB v19.3 | pnl={self._daily_pnl:.0f}{pos_info}")
            except: pass

    def _on_open(self, ws): logging.warning("🔗 WebSocket Connected")
    def _on_error(self, ws, error): logging.error(f"❌ WebSocket Error: {error}")
    def _on_close(self, ws, status, msg): logging.warning(f"🔒 WebSocket Closed ({status})")

    def stop(self):
        self.is_running = False
        if self.ws: self.ws.close()
        self._executor.shutdown(wait=False)
        logging.warning("🛑 Controller stopped")


if __name__ == "__main__":
    target_codes = None
    if len(sys.argv) > 1:
        raw_arg = sys.argv[1]
        target_codes = [x.strip() for x in raw_arg.split(",") if x.strip()]
    
    ctrl = WebsocketController(symbols=target_codes)
    
    try:
        ctrl.run()
    except KeyboardInterrupt:
        logging.warning("🛑 Ctrl+C detected")
        ctrl.stop()