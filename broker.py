# -*- coding: utf-8 -*-
"""
Broker v11.3 (Order Failure Sync)
=====================================================
v11.3 修正点:
- buy()/short_entry()で注文失敗時も建玉確認・同期
- _sync_position_if_exists()ヘルパー追加

v11.2 修正点:
- can_order()内のis_pendingチェックを削除
- mark_pending()とcan_order()の順序問題を解決
"""

import time
import logging
import datetime
import threading
from typing import Optional, Dict, Any, List, Tuple, Set
from contextlib import contextmanager

class SymbolLock:
    """銘柄単位の排他制御"""
    def __init__(self):
        self._locks: Dict[str, threading.Lock] = {}
        self._master_lock = threading.Lock()
        self._pending: Set[str] = set()

    def get_lock(self, code: str) -> threading.Lock:
        with self._master_lock:
            if code not in self._locks:
                self._locks[code] = threading.Lock()
            return self._locks[code]

    @contextmanager
    def acquire(self, code: str, timeout: float = 0.1):
        lock = self.get_lock(code)
        acquired = lock.acquire(timeout=timeout)
        if not acquired:
            raise RuntimeError(f"Lock timeout: {code}")
        try:
            yield
        finally:
            lock.release()

    def mark_pending(self, code: str) -> bool:
        with self._master_lock:
            if code in self._pending: return False
            self._pending.add(code)
            return True

    def unmark_pending(self, code: str):
        with self._master_lock:
            self._pending.discard(code)
    
    def is_pending(self, code: str) -> bool:
        with self._master_lock:
            return code in self._pending


class Broker:
    """
    Broker v11.3
    - 注文失敗時の建玉同期機能追加
    """
    def __init__(self, api, cfg):
        self.api = api
        self.cfg = cfg
        self.paper = bool(getattr(cfg, "paper_trading", False))

        logging.info(f"🔧 Broker v11.3 Initialized (Order Failure Sync)")

        self.pos: Dict[str, Dict[str, Any]] = {}
        self.last_order_ts: Dict[str, float] = {}
        self._max_open_positions = int(getattr(getattr(cfg, "portfolio", object()), "max_open_positions", 5))

        self._symbol_lock = SymbolLock()
        self._order_cooldown: Dict[str, float] = {}
        self._pending_orders: Set[str] = set()
        self._pending_lock = threading.Lock()

        safety = getattr(cfg, "safety", object())
        self._min_order_interval = float(getattr(safety, "min_order_interval_sec", 1.0))
        self.WAIT_SECONDS = 5

        short_cfg = getattr(cfg, "short", object())
        self._short_enabled = bool(getattr(short_cfg, "enabled", False))
        self._short_allowed_symbols = set(getattr(short_cfg, "allowed_symbols", []))

    # ==========================================
    # 共通チェック
    # ==========================================
    def can_order(self, code: str) -> Tuple[bool, str]:
        code = str(code)
        
        last_order = self._order_cooldown.get(code, 0)
        if (time.time() - last_order) < self._min_order_interval: 
            return False, "cooldown"
        
        if code in self.pos: 
            return False, "position_exists"
        if len(self.pos) >= self._max_open_positions: 
            return False, "max_positions"
        
        return True, "ok"

    def can_short(self, code: str, board: dict) -> Tuple[bool, str]:
        if not self._short_enabled: 
            return False, "short_disabled"
        if self._short_allowed_symbols and code not in self._short_allowed_symbols:
            return False, "symbol_not_allowed"
        return self.can_order(code)

    def can_trade_now(self, is_entry: bool) -> bool:
        return True

    # ==========================================
    # 注文処理 (BUY)
    # ==========================================
    def buy(self, sym, board, cur_px) -> Optional[dict]:
        code = str(sym.code)
        
        if not self._symbol_lock.mark_pending(code):
            logging.debug(f"[BUY] {code} skipped: already pending")
            return None

        try:
            can, reason = self.can_order(code)
            if not can:
                logging.info(f"[BUY] {code} skipped: {reason}")
                return None

            qty = int(getattr(sym, "qty", 100))

            # --- Paper Trading ---
            if self.paper:
                cur = float(cur_px)
                self.pos[code] = {
                    "entry": cur, "qty": qty, "ts": time.time(),
                    "side": "BUY", "strategy": "SCALP"
                }
                self._order_cooldown[code] = time.time()
                logging.info(f"[PAPER] BUY {code} @ {cur}")
                return {"Result": 0}

            # --- Live Trading ---
            payload = {
                "Password": self.cfg.api.trade_password,
                "Symbol": code, "Exchange": int(sym.exchange), "SecurityType": 1,
                "Side": "2", "CashMargin": 2, "MarginTradeType": 3, "DelivType": 0,
                "FundType": "11", "AccountType": 4, "Qty": qty,
                "FrontOrderType": 10, "Price": 0, "ExpireDay": 0 
            }

            logging.info(f"[LIVE] BUY Sending... {code} qty={qty}")
            res = self.api.sendorder(payload)

            if not res or res.get("Result") != 0:
                logging.warning(f"⚠️ BUY注文失敗 {code}: {res}")
                # ★v11.3: 失敗でも建玉を確認
                time.sleep(0.3)
                synced = self._sync_position_if_exists(code, qty, "BUY", cur_px)
                if synced:
                    logging.warning(f"🔄 [BUY] {code} 注文失敗だが約定を検出、同期完了")
                    return {"Result": 0, "Synced": True}
                return None

            oid = res.get("OrderId")
            self._order_cooldown[code] = time.time()

            fill_price = self._wait_and_get_execution_price(oid, code)
            final_entry_price = float(fill_price if fill_price else cur_px)

            self.pos[code] = {
                "entry": final_entry_price,
                "qty": qty, "ts": time.time(),
                "side": "BUY", "strategy": "SCALP",
                "fill_ts": time.time()
            }
            
            if fill_price:
                logging.info(f"✅ [Execution] {code} BUY @ {fill_price} (Confirmed)")
            else:
                logging.warning(f"⚠️ [Execution] {code} BUY @ {cur_px} (Unconfirmed)")

            return res

        except Exception as e:
            logging.error(f"BUY Error: {e}")
            return None
        finally:
            self._symbol_lock.unmark_pending(code)

    # ==========================================
    # 注文処理 (SHORT)
    # ==========================================
    def short_entry(self, sym, board, cur_px) -> Optional[dict]:
        code = str(sym.code)
        
        if not self._symbol_lock.mark_pending(code):
            logging.debug(f"[SHORT] {code} skipped: already pending")
            return None

        try:
            can, reason = self.can_short(code, board)
            if not can:
                logging.info(f"[SHORT] {code} skipped: {reason}")
                return None

            qty = int(getattr(sym, "qty", 100))

            # --- Paper ---
            if self.paper:
                cur = float(cur_px)
                self.pos[code] = {
                    "entry": cur, "qty": qty, "ts": time.time(),
                    "side": "SHORT", "strategy": "SCALP"
                }
                self._order_cooldown[code] = time.time()
                logging.info(f"[PAPER] SHORT {code} @ {cur}")
                return {"Result": 0}

            # --- Live ---
            payload = {
                "Password": self.cfg.api.trade_password,
                "Symbol": code, "Exchange": int(sym.exchange), "SecurityType": 1,
                "Side": "1", "CashMargin": 2, "MarginTradeType": 3, "DelivType": 0,
                "FundType": "11", "AccountType": 4, "Qty": qty,
                "FrontOrderType": 10, "Price": 0, "ExpireDay": 0
            }

            logging.info(f"[LIVE] SHORT Sending... {code} qty={qty}")
            res = self.api.sendorder(payload)

            if not res or res.get("Result") != 0:
                logging.warning(f"⚠️ SHORT Failed: {res}")
                # ★v11.3: 失敗でも建玉を確認
                time.sleep(0.3)
                synced = self._sync_position_if_exists(code, qty, "SHORT", cur_px)
                if synced:
                    logging.warning(f"🔄 [SHORT] {code} 注文失敗だが約定を検出、同期完了")
                    return {"Result": 0, "Synced": True}
                return None

            oid = res.get("OrderId")
            self._order_cooldown[code] = time.time()

            fill_price = self._wait_and_get_execution_price(oid, code)
            final_entry_price = float(fill_price if fill_price else cur_px)

            self.pos[code] = {
                "entry": final_entry_price,
                "qty": qty, "ts": time.time(),
                "side": "SHORT", "strategy": "SCALP",
                "fill_ts": time.time()
            }
            
            if fill_price:
                logging.info(f"✅ [Execution] {code} SHORT @ {fill_price} (Confirmed)")
            else:
                logging.warning(f"⚠️ [Execution] {code} SHORT @ {cur_px} (Unconfirmed)")

            return res

        except Exception as e:
            logging.error(f"SHORT Error: {e}")
            return None
        finally:
            self._symbol_lock.unmark_pending(code)

    # ==========================================
    # 決済処理 (SELL / COVER)
    # ==========================================
    def sell(self, sym, qty: int):
        return self._exit_order(sym, qty, side="1", trade_type="SELL")

    def cover(self, sym, qty: int):
        return self._exit_order(sym, qty, side="2", trade_type="COVER")

    def _exit_order(self, sym, qty, side, trade_type):
        code = str(sym.code)
        if self.paper:
            logging.info(f"[PAPER] {trade_type} {code}")
            return True, "PAPER_OK"

        # 1. まず既存の注文をキャンセル
        try:
            orders = self.api.orders() or []
            for o in orders:
                if str(o.get("Symbol")) == code and o.get("State") in [1, 2]:
                    order_id = o.get("ID")
                    logging.debug(f"[EXIT] Cancelling order {order_id} for {code}")
                    cancel_res = self.api.cancelorder({
                        "Password": self.cfg.api.trade_password, "OrderId": order_id
                    })
                    if not cancel_res:
                        logging.warning(f"⚠️ Cancel order failed for {code}: {order_id}")
            time.sleep(0.2)
        except Exception as e:
            logging.warning(f"⚠️ Cancel orders exception for {code}: {e}")

        # 2. 建玉指定で決済
        try:
            positions = self.api.positions() or []
            target_pos = [p for p in positions if str(p.get("Symbol")) == code and int(p.get("LeavesQty", 0)) > 0]
            
            closes = []
            rest = int(qty)
            for p in target_pos:
                hold_id = p.get("HoldID")
                if hold_id is None:
                    logging.warning(f"⚠️ HoldID missing for {code}, skipping this position")
                    continue
                    
                hold_qty = int(p.get("LeavesQty"))
                take = min(rest, hold_qty)
                closes.append({"HoldID": str(hold_id), "Qty": int(take)})
                rest -= take
                if rest <= 0: break
            
            logging.info(f"[EXIT] {code} ClosePositions: {closes}")
            
            if not closes:
                logging.warning(f"⚠️ 建玉不明、汎用返済を試行: {code}")
                return self._exit_fallback(code, sym.exchange, side, qty)

            payload = {
                "Password": self.cfg.api.trade_password,
                "Symbol": code, "Exchange": int(sym.exchange), "SecurityType": 1,
                "Side": side, "CashMargin": 3, "MarginTradeType": 3, "DelivType": 2,
                "FundType": "11", "AccountType": 4, "Qty": int(qty),
                "ClosePositions": closes,
                "FrontOrderType": 10, "Price": 0, "ExpireDay": 0
            }
            
            res = self.api.sendorder(payload)
            if res and res.get("Result") == 0:
                logging.info(f"✅ {trade_type} 成功: {code}")
                return True, res
            else:
                logging.error(f"❌ {trade_type} 失敗: {res}")
                return False, res

        except Exception as e:
            logging.error(f"{trade_type} Error: {e}")
            return False, str(e)

    def _exit_fallback(self, code, exchange, side, qty):
        payload = {
            "Password": self.cfg.api.trade_password,
            "Symbol": code, "Exchange": int(exchange), "SecurityType": 1,
            "Side": side, "CashMargin": 3, "MarginTradeType": 3, "DelivType": 2,
            "FundType": "11", "AccountType": 4, "Qty": int(qty),
            "ClosePositionOrder": 0,
            "FrontOrderType": 10, "Price": 0, "ExpireDay": 0
        }
        res = self.api.sendorder(payload)
        return bool(res and res.get("Result") == 0), res

    # ==========================================
    # ★v11.3: 建玉同期ヘルパー
    # ==========================================
    def _sync_position_if_exists(self, code: str, expected_qty: int, side: str, fallback_price: float) -> bool:
        """
        注文失敗後に建玉が存在するか確認し、あれば同期する
        Returns: True if position was synced
        """
        try:
            positions = self.api.positions()
            if not positions:
                return False
            
            for p in positions:
                p_code = str(p.get("Symbol"))
                p_qty = int(p.get("LeavesQty") or 0)
                p_side_api = str(p.get("Side"))  # "1"=売り, "2"=買い
                
                if p_code != code or p_qty <= 0:
                    continue
                
                # Side確認
                expected_api_side = "1" if side == "SHORT" else "2"
                if p_side_api != expected_api_side:
                    continue
                
                # 既にposに登録済みならスキップ
                if code in self.pos:
                    return False
                
                entry_price = float(p.get("Price") or p.get("EntryPrice") or fallback_price)
                
                self.pos[code] = {
                    "entry": entry_price,
                    "qty": p_qty,
                    "ts": time.time(),
                    "side": side,
                    "strategy": "SYNCED",
                    "fill_ts": time.time()
                }
                self._order_cooldown[code] = time.time()
                
                logging.info(f"✅ [SYNC] {code} {side} @ {entry_price} qty={p_qty}")
                return True
            
            return False
        except Exception as e:
            logging.error(f"_sync_position_if_exists error: {e}")
            return False

    # ==========================================
    # ヘルパー: 正確な約定価格を取得
    # ==========================================
    def _wait_and_get_execution_price(self, order_id: str, code: str) -> Optional[float]:
        start = time.time()
        while time.time() - start < self.WAIT_SECONDS:
            try:
                orders = self.api.orders()
                if not orders:
                    time.sleep(0.5)
                    continue
                
                target_order = None
                for o in orders:
                    if o.get("ID") == order_id:
                        target_order = o
                        break
                
                if target_order:
                    state = int(target_order.get("State", 0))
                    if state == 5:
                        price = float(target_order.get("Price") or 0)
                        if price > 0:
                            return price
                    
                time.sleep(0.5)
            except Exception:
                time.sleep(0.5)
        
        return self._get_avg_price_from_positions(code)

    def _get_avg_price_from_positions(self, code: str) -> Optional[float]:
        try:
            positions = self.api.positions()
            if not positions: return None
            
            relevant = [p for p in positions if str(p.get("Symbol")) == code]
            if not relevant: return None
            
            last_pos = relevant[-1]
            return float(last_pos.get("Price") or last_pos.get("EntryPrice") or 0)
        except:
            return None

    def _open_positions(self):
        try:
            return self.api.positions()
        except: return None