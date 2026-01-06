# -*- coding: utf-8 -*-
import time, logging, datetime
from typing import Optional, Dict, Any, List, Tuple

from utils_common import tick_size

class Broker:
    """
    Broker v7.2 (Audit対応: 完全監視・自動キャンセル・掃除機能付き)
      - 新規: ベストASKを叩く(Taker) -> 10秒監視 -> ダメなら自動キャンセル
      - 返済: 既存の注文をキャンセルしてから、ExecutionID指定で確実に返す
      - 信用区分: デイトレ信用(3)固定
    """

    def __init__(self, api, cfg):
        self.api, self.cfg = api, cfg
        self.paper = bool(getattr(cfg, "paper_trading", False))
        self.pos: Dict[str, Dict[str, Any]] = {}
        self.last_order_ts: Dict[str, float] = {}
        self.fail_ts: List[float] = []
        self._last_buy_order_id: Dict[str, str] = {}

        # 同時建玉上限
        port = getattr(cfg, "portfolio", object())
        self._max_open_positions = int(getattr(port, "max_open_positions", 1))

        # ★追加: 注文監視設定
        self.WAIT_SECONDS = 10  # 約定を待つ秒数

    # -------------------- 軽ガード --------------------
    def can_trade_now(self, is_entry: bool) -> bool:
        try:
            safety = getattr(self.cfg, "safety", object())
            if bool(getattr(safety, "pause_on_latency", True)):
                lim = int(getattr(safety, "api_latency_ms_limit", 1500))
                if getattr(self.api, "last_latency_ms", 0) > lim:
                    return False

            risk = getattr(self.cfg, "risk", object())
            hhmm = datetime.datetime.now().strftime("%H:%M")
            no_new = str(getattr(risk, "no_new_entry_after", "") or "")
            force_c = str(getattr(risk, "force_close_time", "") or "")
            if force_c and hhmm >= force_c:
                return False
            if is_entry and no_new and hhmm >= no_new:
                return False
            return True
        except Exception:
            return True

    # -------------------- 型補正 --------------------
    @staticmethod
    def _coerce_ints(p: dict, keys: list):
        for k in keys:
            if k in p and isinstance(p[k], str) and p[k].isdigit():
                p[k] = int(p[k])

    # -------------------- 建玉チェック --------------------
    def _open_positions(self) -> List[dict]:
        try:
            return list(self.api.positions() or [])
        except Exception:
            return []

    def _open_count(self) -> int:
        return sum(int(p.get("LeavesQty") or p.get("HoldQty") or p.get("Qty") or 0) > 0 for p in self._open_positions())

    def _has_symbol_open(self, code: str) -> bool:
        code_s = str(code)
        for p in self._open_positions():
            if str(p.get("Symbol")) == code_s and int(p.get("LeavesQty") or p.get("HoldQty") or p.get("Qty") or 0) > 0:
                return True
        return False

    # -------------------- ★重要: 注文監視と自動キャンセル --------------------
    def _wait_and_check_order(self, order_id: str, code: str) -> bool:
        """
        注文が約定するまで監視する。
        タイムアウトしたらキャンセル注文を投げ、Falseを返す。
        """
        if self.paper: return True

        logging.info(f"👀 注文監視開始: {code} (ID:{order_id}) - {self.WAIT_SECONDS}秒待機")
        start_time = time.time()
        
        while (time.time() - start_time) < self.WAIT_SECONDS:
            time.sleep(1.0) # 1秒おきに確認
            try:
                # 注文一覧を取得 (※API負荷に注意しつつ)
                orders = self.api.orders() 
                if not orders: continue

                target = None
                for o in orders:
                    if o.get("ID") == order_id:
                        target = o
                        break
                
                if target:
                    state = int(target.get("State", 0))
                    # 5: 全約定
                    if state == 5:
                        logging.info(f"✅ 約定確認: {code}")
                        return True
                    elif state == 3: # 取消/失効済み
                        logging.warning(f"⚠️ 注文は既に取消/失効されています: {code}")
                        return False
            except Exception as e:
                logging.warning(f"監視中エラー(無視): {e}")
        
        # タイムアウト -> キャンセル実行
        logging.warning(f"⛔ タイムアウト: {code} 未約定のためキャンセルします")
        try:
            # キャンセルリクエスト
            self.api.cancelorder({
                "Password": self.cfg.api.trade_password,
                "OrderId": order_id
            })
            logging.info("🗑️ キャンセル注文送信完了")
        except Exception as e:
            logging.error(f"キャンセル送信失敗: {e}")
            
        return False

    # -------------------- ExecutionID 収集 --------------------
    def _wait_exec_ids(self, code: str, order_id: Optional[str], wait_ms: int = 1500) -> List[Tuple[str, int]]:
        deadline = time.time() + max(0.2, wait_ms / 1000.0)
        code_s = str(code)
        found: List[Tuple[str, int]] = []
        first = True

        while time.time() < deadline:
            if first:
                time.sleep(0.25)
                first = False

            try:
                for od in (self.api.orders() or []):
                    if order_id and od.get("OrderId") != order_id: continue
                    for d in (od.get("Details") or []):
                        exid = d.get("ExecutionID")
                        q = d.get("CumQty") or d.get("Qty") or 0
                        if exid and int(q) > 0:
                            found.append((str(exid), int(q)))
            except Exception: pass

            try:
                for p in (self.api.positions() or []):
                    if str(p.get("Symbol")) != code_s: continue
                    exid = p.get("ExecutionID")
                    q = p.get("LeavesQty") or p.get("HoldQty") or p.get("Qty") or 0
                    if exid and int(q) > 0:
                        found.append((str(exid), int(q)))
            except Exception: pass

            if found: return found
            time.sleep(0.08)
        return []

    # -------------------- 新規 (BUY) --------------------
    def buy(self, sym, board, cur_px):
        """
        新規（Taker戦略 + 自動キャンセルガード）
        """
        try:
            if not self.can_trade_now(is_entry=True):
                return None

            # Broker内ガード
            if self._open_count() >= self._max_open_positions:
                logging.warning("[GUARD] max_open_positions limit")
                return None
            if self._has_symbol_open(str(sym.code)):
                logging.warning("[GUARD] symbol already open: %s", sym.code)
                return None

            # 価格決定: Taker戦略
            ask = (board.get("BestAsk") or {}).get("Price") or board.get("AskPrice")
            if not isinstance(ask, (int, float)):
                ask = cur_px

            limit_px = ask 
            qty = int(getattr(sym, "qty", 100))

            # Paper Trading
            if self.paper:
                return {"Result": 0, "OrderId": "PAPER_ID"}

            # Live 発注
            payload = {
                "Password": self.cfg.api.trade_password,
                "Symbol": str(sym.code),
                "Exchange": int(sym.exchange),
                "SecurityType": 1,
                "Side": 2,                 # Buy
                "CashMargin": 2,           # 信用新規
                "MarginTradeType": 3,      # ★3=デイトレ信用(一般)
                "DelivType": 0,
                "FundType": 11,
                "AccountType": 4,
                "Qty": int(qty),
                "FrontOrderType": 20,      # 指値
                "Price": int(limit_px),    # Ask価格
                "ExpireDay": 0,
            }
            self._coerce_ints(payload, ["Exchange","SecurityType","Side","CashMargin",
                                        "MarginTradeType","DelivType","FundType","AccountType",
                                        "Qty","FrontOrderType","Price","ExpireDay"])
            
            logging.info("[LIVE] BUY(Taker) code=%s price=%s qty=%d", sym.code, limit_px, qty)

            res = self.api.sendorder(payload)
            if not res or res.get("Result") != 0:
                logging.error("LIVE BUY 失敗: %s", res)
                return None

            order_id = res.get("OrderId")
            self._last_buy_order_id[str(sym.code)] = order_id

            # ★ここで監視！約定しなければキャンセルして None を返す
            if self._wait_and_check_order(order_id, str(sym.code)):
                return res  # 約定成功 -> Controllerへ
            else:
                return None # キャンセル済み -> Controllerは何もしない

        except Exception as e:
            logging.error("LIVE BUY Exception: %s", e)
            return None

    # -------------------- 返済 (SELL) --------------------
    def sell(self, sym, qty: int):
        """
        返済（成行）
        ★修正: 二重発注を防ぐため、先にこの銘柄の全注文をキャンセルしてから売る
        """
        try:
            if not self.can_trade_now(is_entry=False):
                return False, "guard"

            code = str(sym.code)
            
            if self.paper:
                return True, "PAPER_OK"

            # ★★★ 追加: まず既存の注文を掃除する (ロック解除) ★★★
            logging.info(f"🧹 [{code}] 決済前のクリーンアップ: 既存注文をキャンセルします")
            try:
                orders = self.api.orders()
                if orders:
                    for o in orders:
                        # この銘柄の、有効な(State=1,2)注文ならキャンセル
                        if str(o.get("Symbol")) == code and o.get("State") in [1, 2]:
                            self.api.cancelorder({
                                "Password": self.cfg.api.trade_password,
                                "OrderId": o.get("ID")
                            })
                            logging.info(f"   🗑️ 注文取消: {o.get('ID')}")
                # キャンセルが反映されるまで少し待つ
                time.sleep(0.5) 
            except Exception as e:
                logging.warning(f"キャンセル処理でエラー(無視): {e}")

            # --- ここから下は今まで通り ---

            # 1. 以前の注文IDからExecutionIDを探す
            last_oid = self._last_buy_order_id.get(code)
            execs = self._wait_exec_ids(code=code, order_id=last_oid, wait_ms=1500)
            
            # 2. ExecutionID指定で返済 (これが一番確実)
            if execs:
                if str(sym.code) in self.pos:
                    self.pos[str(sym.code)]["fill_ts"] = time.time()
                
                res = self._live_sell_credit_close_multi(sym, qty, execs)
                if res and isinstance(res, dict) and res.get("Result") == 0:
                    return True, res

            # 3. 見つからなければ FIFO (バックアップ)
            logging.warning("ExecutionID未取得。FIFO返済(成行)を実行: %s", code)
            res2 = self._live_sell_credit_fifo(sym, qty)
            ok2 = bool(res2 and isinstance(res2, dict) and res2.get("Result") == 0)
            return ok2, res2

        except Exception as e:
            logging.error("LIVE SELL Exception: %s", e)
            return False, str(e)

    # (内部メソッド: 指定返済)
    def _live_sell_credit_close_multi(self, sym, qty: int, execs: List[Tuple[str, int]]):
        rest = int(qty)
        closes = []
        for exid, q in execs:
            take = min(rest, int(q))
            if take <= 0: break
            closes.append({"HoldID": str(exid), "Qty": int(take)})
            rest -= take
        if not closes: return None

        payload = {
            "Password": self.cfg.api.trade_password,
            "Symbol": str(sym.code),
            "Exchange": int(sym.exchange),
            "SecurityType": 1,
            "Side": 1, # Sell
            "CashMargin": 3,
            "MarginTradeType": 3, # ★3=デイトレ信用(一般)
            "DelivType": 2,
            "FundType": 11,
            "AccountType": 4,
            "Qty": int(qty),
            "ClosePositions": closes,
            "FrontOrderType": 10, # 成行
            "Price": 0,
            "ExpireDay": 0,
        }
        return self.api.sendorder(payload)

    # (内部メソッド: FIFO返済)
    def _live_sell_credit_fifo(self, sym, qty: int):
        payload = {
            "Password": self.cfg.api.trade_password,
            "Symbol": str(sym.code),
            "Exchange": int(sym.exchange),
            "SecurityType": 1,
            "Side": 1, # Sell
            "CashMargin": 3,
            "MarginTradeType": 3, # ★3=デイトレ信用(一般)
            "DelivType": 2,
            "FundType": 11,
            "AccountType": 4,
            "Qty": int(qty),
            "ClosePositionOrder": 0, # FIFO
            "FrontOrderType": 10, # 成行
            "Price": 0,
            "ExpireDay": 0,
        }
        return self.api.sendorder(payload)