# -*- coding: utf-8 -*-
"""
WebsocketController v22.5 (18次元 - 短期決戦仕様)
=================================================
【v22.5 変更点】
1. 親クラス(Controller v22.5)を継承し、18次元観測空間に対応
2. 重複していたモデルロード処理を削除（親クラスに委譲）
3. モデルパス: Master_AI_v2 対応
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

# 親クラス (Controller v22.5) をインポート
from controller import Controller

JST = timezone(timedelta(hours=9))


class WebsocketController(Controller):
    def __init__(self, config_path="config.yaml", symbols: Optional[List[str]] = None):
        try:
            cfg = load_cfg()
        except Exception as e:
            logging.error(f"Config load failed: {e}")
            raise e

        # 親クラスの初期化 (ここで AIモデルロード v22.5 が実行されます)
        super().__init__(cfg)

        if symbols is not None and len(symbols) > 0:
            logging.warning(f"🤖 [分身モード] {len(symbols)} 銘柄監視")
            # 監視対象を絞り込む
            subset_syms = [s for s in self.syms if str(s.code) in symbols]
            self.syms = subset_syms
            self.target_codes = [str(s.code) for s in self.syms]
            self.sym_map = {str(s.code): s for s in self.syms}
        else:
            logging.warning(f"🤖 [本体モード] {len(self.syms)} 銘柄監視")

        # WebSocket URLの構築 (Configから自動生成)
        base_url = self.cfg.api.base  # 例: http://localhost:18080/kabusapi
        if base_url.startswith("http"):
            self.ws_url = base_url.replace("http", "ws") + "/websocket"
        else:
            self.ws_url = "ws://localhost:18080/kabusapi/websocket"

        self.ws = None
        self.is_running = False
        
        self._last_process_time: Dict[str, float] = {}
        # 処理頻度はConfigに合わせる (0.2秒など)
        self._process_interval = self.cfg.safety.api_call_interval_sec
        
        self._processing: Set[str] = set()
        self._processing_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="SymWorker")
        
        self._heartbeat_count = 0
        self._last_status_log = 0

        logging.warning("🔧 WebsocketController v22.5 (18次元) initialized")

    def run(self):
        self.is_running = True
        
        if hasattr(self, 'notifier'):
            self.notifier.send("🤖 Bot起動 (v22.5 - Hybrid AI)", "startup")
        
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
            # 板情報の更新
            self.boards[code].update(data)
            
            now_t = time.time()
            last_time = self._last_process_time.get(code, 0)
            
            # ポジション保有中は反応速度を上げる
            has_pos = code in self.brk.pos
            interval = 0.1 if has_pos else self._process_interval
            
            if now_t - last_time < interval:
                return
            
            with self._processing_lock:
                if code in self._processing:
                    return
                self._processing.add(code)
            
            self._last_process_time[code] = now_t
            
            # ボードのコピーを渡して非同期処理
            self._executor.submit(self._safe_process_symbol, code, self.boards[code].copy())
            
        except Exception as e:
            # logging.debug(f"on_message error: {e}")
            pass

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
        ★ LogFix オーバーライド (v22.5)
        手動決済や予期せぬポジション解消を検知してログに残すためのラッパー
        """
        now_jst = datetime.now(JST)
        hm = now_jst.strftime("%H:%M")
        
        # 昼休み等はスキップ
        if "11:30" <= hm < "12:25": return
        if code_s not in self.brk.pos:
            if now_jst.hour == 9 and now_jst.minute < 15: return
            if now_jst.hour == 12 and 30 <= now_jst.minute < 40: return

        # -----------------------------------------------------
        # [LogFix] 処理前の状態を記録 (Before)
        # -----------------------------------------------------
        had_pos = code_s in self.brk.pos
        entry_price = 0.0
        entry_side = ""
        entry_qty = 0.0
        
        if had_pos:
            pos_data = self.brk.pos.get(code_s, {})
            try:
                entry_price = float(pos_data.get("entry", 0)) # v22.5のキー名に修正
                entry_side = pos_data.get("side", "")
                entry_qty = float(pos_data.get("qty", 0))
            except:
                pass

        # =====================================================
        # 親クラスのロジック実行 (AI推論 & 注文)
        # =====================================================
        super().process_symbol(code_s, board)
        # =====================================================

        # -----------------------------------------------------
        # [LogFix] 処理後の状態を確認 (After)
        # -----------------------------------------------------
        has_pos_now = code_s in self.brk.pos

        if had_pos and not has_pos_now:
            # 決済価格の取得 (WSのキーはCamelCaseの場合とsnake_caseの場合がある)
            exit_price = float(
                board.get("CurrentPrice") or 
                board.get("current_price") or 
                board.get("ClosePrice") or 
                board.get("close_price") or 
                0
            )
            
            if exit_price > 0 and entry_qty > 0 and entry_price > 0:
                pnl = 0
                if entry_side == "BUY":
                    pnl = (exit_price - entry_price) * entry_qty
                elif entry_side == "SHORT":
                    pnl = (entry_price - exit_price) * entry_qty
                
                sign = "+" if pnl >= 0 else ""
                log_msg = f"💰 [{code_s}] EXIT REPORT: ¥{sign}{int(pnl)} (Price: {entry_price:.1f}->{exit_price:.1f})"
                logging.warning(log_msg)

    def _initialize_buffer(self):
        logging.warning("⏳ 初期データ取得中...")
        for s in self.syms:
            try:
                board = self.api.board(s.code, s.exchange)
                if board:
                    code_s = str(s.code)
                    self.boards[code_s] = board
                    # 親クラスのメソッドでインジケータ計算
                    if hasattr(self, '_update_indicators'):
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
                # プロパティがあれば表示
                pnl_str = f"{self._daily_pnl:.0f}" if hasattr(self, '_daily_pnl') else "?"
                logging.warning(f"💓 HB v22.5 | pnl={pnl_str}{pos_info}")
            except: pass

    def _on_open(self, ws): logging.warning("🔗 WebSocket Connected")
    def _on_error(self, ws, error): logging.error(f"❌ WebSocket Error: {error}")
    def _on_close(self, ws, status, msg): logging.warning(f"🔒 WebSocket Closed ({status})")

    def stop(self):
        self.is_running = False
        if self.ws: self.ws.close()
        self._executor.shutdown(wait=False)
        
        # 日次レポート出力
        if hasattr(self, 'generate_daily_report'):
            self.generate_daily_report()
        
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