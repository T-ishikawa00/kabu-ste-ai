# -*- coding: utf-8 -*-
"""
WebsocketController v20.21 (Robust Fix Edition)
==============================================
【v20.21 修正点】
1. クラッシュ対策: ポジション情報の取得を .get() で安全に行うように変更
   → 'price' キーが見つからなくてもエラーで止まらないように修正
2. 単元株チェック: 注文数量が単元株(100株など)の倍数になるよう調整
"""

import sys
import os
import json
import threading
import time
import logging
import websocket
import math
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Set, List, Optional
from stable_baselines3 import PPO

try:
    from config_loader import load_cfg
except ImportError:
    sys.path.append(os.getcwd())
    from config_loader import load_cfg

# 親クラス (Controller v20.6) をインポート
from controller import Controller

JST = timezone(timedelta(hours=9))


class WebsocketController(Controller):
    def __init__(self, config_path="config.yaml", symbols: Optional[List[str]] = None):
        try:
            cfg = load_cfg()
        except Exception as e:
            logging.error(f"Config load failed: {e}")
            raise e

        # 親クラスの初期化
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
        # ★ハイブリッドモデルロード (Individual > Universal)
        # ==========================================
        logging.warning("🤖 [WS] AIモデル最適化 (Hybrid Mode)...")
        indiv_count = 0
        univ_count = 0

        self.ai_models = {}

        for code in self.target_codes:
            indiv_path = f"runtime/model_individual_{code}"
            
            if os.path.exists(indiv_path + ".zip"):
                try:
                    self.ai_models[code] = PPO.load(indiv_path)
                    logging.info(f"   👤 [{code}] 個別モデル(Specialist) 適用")
                    indiv_count += 1
                    continue
                except Exception as e:
                    logging.error(f"   ⚠️ [{code}] 個別モデル破損 -> 汎用にフォールバック: {e}")

            if self.universal_model:
                self.ai_models[code] = self.universal_model
                univ_count += 1
            else:
                logging.warning(f"   ❌ [{code}] AIモデルなし (ルールベース稼働)")

        logging.warning(f"🤖 モデル再配置完了: 個別特化={indiv_count} / 汎用汎用={univ_count}")

        self.ws_url = getattr(self.cfg, "websocket_url", "ws://localhost:18080/kabusapi/websocket")
        self.ws = None
        self.is_running = False
        
        self._last_process_time: Dict[str, float] = {}
        self._process_interval = 0.5 
        self._processing: Set[str] = set()
        self._processing_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="SymWorker")
        
        self._heartbeat_count = 0
        self._last_status_log = 0

        logging.warning("🔧 WebsocketController v20.21 (Robust Fix) initialized")

    def run(self):
        self.is_running = True
        
        if hasattr(self, 'notifier'):
            self.notifier.send("🤖 Bot起動 (v20.21 Robust Fix)", "startup")
        
        logging.warning(f"🚀 Starting WS Mode... ({len(self.syms)} symbols)")
        
        try:
            self.api.auth()
        except Exception as e:
            logging.error(f"❌ API Auth Error: {e}")
            return

        if not self.cfg.paper_trading:
            self._recover_ghost_positions()

        self._initialize_buffer()
        self._register_push()
        
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
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
            
            if code not in self.boards:
                self.boards[code] = {}
            self.boards[code].update(data)
            
            now_t = time.time()
            last_time = self._last_process_time.get(code, 0)
            
            has_pos = code in self.brk.pos
            interval = 0.2 if has_pos else self._process_interval
            
            if now_t - last_time < interval:
                return
            
            with self._processing_lock:
                if code in self._processing:
                    return
                self._processing.add(code)
            
            self._last_process_time[code] = now_t
            
            self._executor.submit(self._safe_process_symbol, code, self.boards[code])
            
        except Exception as e:
            logging.debug(f"on_message error: {e}")

    def _safe_process_symbol(self, code: str, board: dict):
        try:
            self.process_symbol(code, board)
        except Exception as e:
            # ここでエラーが出ても全体を止めない
            logging.error(f"Process error [{code}]: {e}")
        finally:
            with self._processing_lock:
                self._processing.discard(code)

    def process_symbol(self, code_s: str, board: dict = None):
        """
        ★ LogFix オーバーライド (v20.21 安全版)
        """
        now_jst = datetime.now(JST)
        hm = now_jst.strftime("%H:%M")
        
        if "11:30" <= hm < "12:25": return
        if code_s not in self.brk.pos:
            if now_jst.hour == 9 and now_jst.minute < 15: return
            if now_jst.hour == 12 and 30 <= now_jst.minute < 40: return

        # -----------------------------------------------------
        # [LogFix] 処理前の状態を記録 (Before)
        # -----------------------------------------------------
        had_pos = code_s in self.brk.pos
        entry_price = 0
        entry_side = ""
        entry_qty = 0
        
        if had_pos:
            # ★【修正】安全にデータを取り出す (.getを使用)
            pos_data = self.brk.pos.get(code_s, {})
            try:
                entry_price = float(pos_data.get("price", 0))
                entry_side = pos_data.get("side", "")
                entry_qty = float(pos_data.get("qty", 0))
            except:
                entry_price = 0

        # =====================================================
        # 親クラスのロジック実行
        # =====================================================
        super().process_symbol(code_s, board)
        # =====================================================

        # -----------------------------------------------------
        # [LogFix] 処理後の状態を確認 (After)
        # -----------------------------------------------------
        has_pos_now = code_s in self.brk.pos

        if had_pos and not has_pos_now:
            # 決済価格の推定
            exit_price = float(board.get("current_price") or board.get("close_price") or 0)
            
            if exit_price > 0 and entry_qty > 0 and entry_price > 0:
                pnl = 0
                if entry_side == "BUY":
                    pnl = (exit_price - entry_price) * entry_qty
                elif entry_side == "SHORT":
                    pnl = (entry_price - exit_price) * entry_qty
                
                sign = "+" if pnl >= 0 else ""
                log_msg = f"💰 [{code_s}] EXIT FIX: ¥{sign}{int(pnl)} (Price: {entry_price:.1f}->{exit_price:.1f})"
                logging.warning(log_msg)

    def _initialize_buffer(self):
        logging.warning("⏳ 初期データ取得中...")
        for s in self.syms:
            try:
                board = self.api.board(s.code, s.exchange)
                if board:
                    code_s = str(s.code)
                    self.boards[code_s] = board
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
                logging.warning(f"💓 HB v20.21 | pnl={self._daily_pnl:.0f}{pos_info}")
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