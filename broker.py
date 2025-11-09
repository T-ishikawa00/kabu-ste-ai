# -*- coding: utf-8 -*-
import time, logging, datetime
from typing import Optional, Dict, Any, List, Tuple

from utils_common import tick_size

class Broker:
    """
    kabuステ向けブローカー（制度信用・価格保護付マーケタブル指値）
    - 新規: マーケタブル指値（疑似IOC + 価格キャップ + 段階再試行）
    - 返済: 同様（ExecutionIDのClosePositions指定を優先。無理ならFIFO）
    - 非常時（強制EXITなど）: 再試行で解消できなければ、設定により成行フォールバック可
    - 5円刻み: 成行を禁止（config.orders.forbid_market_on_5yen=True）
    """

    def __init__(self, api, cfg):
        self.api, self.cfg = api, cfg
        self.paper = bool(getattr(cfg, "paper_trading", False))
        self.pos: Dict[str, Dict[str, Any]] = {}
        self.last_order_ts: Dict[str, float] = {}
        self.fail_ts: List[float] = []
        self._last_buy_order_id: Dict[str, str] = {}

        # 信用区分設定（既存互換）
        cred = getattr(cfg, "credit", object())
        self._mt = int(getattr(cred, "margin_type", 1))            # 3=デイトレ, 1=制度（環境依存のため既存値を優先）
        self._mt_fallback = bool(getattr(cred, "fallback_to_1", True))

        # 注文スタイル設定（market / marketable_limit）
        od = getattr(cfg, "orders", object())
        self._style = str(getattr(od, "style", "marketable_limit"))
        self._ioc = bool(getattr(od, "ioc", True))
        self._cap_ticks_entry = int(getattr(od, "cap_ticks_entry", 2))
        self._cap_ticks_exit  = int(getattr(od, "cap_ticks_exit", 3))
        self._cap_pct_entry   = float(getattr(od, "cap_pct_entry", 0.30))  # %
        self._cap_pct_exit    = float(getattr(od, "cap_pct_exit", 0.50))   # %
        self._retry_enabled   = bool(getattr(getattr(od, "retry", object()), "enabled", True))
        self._retry_max       = int(getattr(getattr(od, "retry", object()), "max_attempts", 2))
        self._retry_widen     = int(getattr(getattr(od, "retry", object()), "widen_ticks_each", 1))
        self._retry_wait_ms   = int(getattr(getattr(od, "retry", object()), "wait_ms_between", 200))
        self._forbid_market_5 = bool(getattr(od, "forbid_market_on_5yen", True))
        self._force_exit_allow_market = bool(getattr(od, "force_exit_allow_market", True))  # 任意

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

    # -------------------- ユーティリティ --------------------
    def _cancel_order_safe(self, order_id: str) -> bool:
        """cancelorder / cancel_order いずれかを呼ぶ（環境差異吸収）"""
        try:
            if hasattr(self.api, "cancelorder"):
                r = self.api.cancelorder({"OrderId": order_id, "Password": self.cfg.api.trade_password})
            elif hasattr(self.api, "cancel_order"):
                r = self.api.cancel_order({"OrderId": order_id, "Password": self.cfg.api.trade_password})
            else:
                logging.warning("[LIVE] cancel not supported by API wrapper")
                return False
            ok = bool(isinstance(r, dict) and (r.get("Result") == 0 or r.get("Code") == 0))
            if not ok:
                logging.warning("[LIVE] cancel failed resp=%s", r)
            return ok
        except Exception as e:
            logging.warning("[LIVE] cancel error: %s", e)
            return False

    def _cap_price(self, side: str, best_bid: float, best_ask: float, ref: float, tick: float,
                   cap_ticks: int, cap_pct: float) -> float:
        """
        価格キャップ（tick制限と%制限の厳しい方）
        BUY: 上限を抑える / SELL: 下限を抑える
        """
        if side == "BUY":
            lim_by_ticks = float(best_ask) + cap_ticks * float(tick)
            lim_by_pct   = float(ref) * (1.0 + cap_pct / 100.0)
            return min(lim_by_ticks, lim_by_pct)
        else:
            lim_by_ticks = float(best_bid) - cap_ticks * float(tick)
            lim_by_pct   = float(ref) * (1.0 - cap_pct / 100.0)
            return max(lim_by_ticks, lim_by_pct)

    def _place_limit(self, sym, side: str, qty: int, price: float, is_close: bool,
                     closes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        kabuステ sendorder（指値）。疑似IOCは呼び出し側でキャンセル制御。
        """
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")

        # BUY=新規信用 / SELL=返済信用 を前提（制度信用寄り）
        if side == "BUY":
            payload = {
                "Password": self.cfg.api.trade_password,
                "Symbol": str(sym.code),
                "Exchange": int(sym.exchange),
                "SecurityType": 1,
                "Side": 2,                 # 買い（新規）
                "CashMargin": 2,           # 信用 新規
                "MarginTradeType": 3,      # 制度信用(環境により1/3を適宜)
                "DelivType": 0,
                "FundType": 11,
                "AccountType": 4,
                "Qty": int(qty),
                "FrontOrderType": 20,      # 指値
                "Price": float(price),
                "ExpireDay": 0,
            }
        else:
            payload = {
                "Password": self.cfg.api.trade_password,
                "Symbol": str(sym.code),
                "Exchange": int(sym.exchange),
                "SecurityType": 1,
                "Side": 1,                 # 売り（返済）
                "CashMargin": 3,           # 信用 返済
                "MarginTradeType": 3,      # 制度信用
                "DelivType": 2,            # 返済
                "FundType": 11,
                "AccountType": 4,
                "Qty": int(qty),
                "FrontOrderType": 20,      # 指値
                "Price": float(price),
                "ExpireDay": 0,
            }
            if closes:
                payload["ClosePositions"] = closes  # HoldID=ExecutionID, Qty=…
            else:
                payload["ClosePositionOrder"] = 0   # FIFO

        self._coerce_ints(payload, ["Exchange","SecurityType","Side","CashMargin",
                                    "MarginTradeType","DelivType","FundType","AccountType",
                                    "Qty","FrontOrderType","ExpireDay"])
        logging.info("[LIVE] sendorder %s LIMIT payload=%s", side, payload)
        return self.api.sendorder(payload) or {}

    def _place_market(self, sym, side: str, qty: int, is_close: bool,
                      closes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        kabuステ sendorder（成行）。禁止条件に注意（5円刻みなど）。
        """
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")

        if side == "BUY":
            payload = {
                "Password": self.cfg.api.trade_password,
                "Symbol": str(sym.code),
                "Exchange": int(sym.exchange),
                "SecurityType": 1,
                "Side": 2,
                "CashMargin": 2,
                "MarginTradeType": 3,
                "DelivType": 0,
                "FundType": 11,
                "AccountType": 4,
                "Qty": int(qty),
                "FrontOrderType": 10,      # 成行
                "Price": 0,
                "ExpireDay": 0,
            }
        else:
            payload = {
                "Password": self.cfg.api.trade_password,
                "Symbol": str(sym.code),
                "Exchange": int(sym.exchange),
                "SecurityType": 1,
                "Side": 1,
                "CashMargin": 3,
                "MarginTradeType": 3,
                "DelivType": 2,
                "FundType": 11,
                "AccountType": 4,
                "Qty": int(qty),
                "FrontOrderType": 10,      # 成行
                "Price": 0,
                "ExpireDay": 0,
            }
            if closes:
                payload["ClosePositions"] = closes
            else:
                payload["ClosePositionOrder"] = 0

        self._coerce_ints(payload, ["Exchange","SecurityType","Side","CashMargin",
                                    "MarginTradeType","DelivType","FundType","AccountType",
                                    "Qty","FrontOrderType","Price","ExpireDay"])
        logging.info("[LIVE] sendorder %s MARKET payload=%s", side, payload)
        return self.api.sendorder(payload) or {}

    def _send_limit_ioc(self, sym, side: str, qty: int, limit_px: float, is_close: bool,
                        closes: Optional[List[Dict[str, Any]]] = None) -> Tuple[bool, int, Optional[str]]:
        """
        疑似IOC: 指値を送って短時間で約定チェック→未約定分は即キャンセル。
        戻り値: (filled_ok, filled_qty, order_id)
        """
        res = self._place_limit(sym, side, qty, limit_px, is_close, closes)
        oid = isinstance(res, dict) and res.get("OrderId")
        if not oid:
            logging.warning("[LIVE] limit order rejected: %s", res)
            return (False, 0, None)

        # 短時間ポーリングで約定数量を確認
        filled_qty = 0
        deadline = time.time() + max(0.15, self._retry_wait_ms / 1000.0)  # IOC相当の短い猶予
        while time.time() < deadline:
            try:
                # /orders を優先参照（約定累計）
                for od in (self.api.orders() or []):
                    if od.get("OrderId") != oid:
                        continue
                    # Details[].CumQty の合計を採用
                    dq = 0
                    for d in (od.get("Details") or []):
                        q = int(d.get("CumQty") or d.get("Qty") or 0)
                        dq = max(dq, q)
                    filled_qty = max(filled_qty, dq)
                    if filled_qty >= qty:
                        break
            except Exception:
                pass
            if filled_qty >= qty:
                break
            time.sleep(0.05)

        # 未充足分があればキャンセル（疑似IOC）
        remaining = max(0, qty - filled_qty)
        if remaining > 0:
            self._cancel_order_safe(oid)

        logging.info("[IOC] side=%s limit=%.3f filled=%d/%d oid=%s", side, float(limit_px), filled_qty, qty, oid)
        return (filled_qty > 0, filled_qty, oid)

    # -------------------- ExecutionID 取得（返済用） --------------------
    def _wait_exec_ids(self, code: str, order_id: Optional[str], wait_ms: int = 1500) -> List[Tuple[str, int]]:
        """
        returns: [(execution_id, qty), ...]
        /orders の Details[].ExecutionID 優先、/positions は保険。
        """
        deadline = time.time() + max(0.2, wait_ms / 1000.0)
        code_s = str(code)
        found: List[Tuple[str, int]] = []
        first = True

        while time.time() < deadline:
            if first:
                time.sleep(0.25)
                first = False

            # 1) /orders 優先
            try:
                for od in (self.api.orders() or []):
                    if order_id and od.get("OrderId") != order_id:
                        continue
                    for d in (od.get("Details") or []):
                        exid = d.get("ExecutionID")
                        q = d.get("CumQty") or d.get("Qty") or 0
                        if exid and int(q) > 0:
                            found.append((str(exid), int(q)))
            except Exception:
                pass

            # 2) /positions（ExecutionID が入る環境あり。無ければ無視）
            try:
                for p in (self.api.positions() or []):
                    if str(p.get("Symbol")) != code_s:
                        continue
                    exid = p.get("ExecutionID")
                    q = p.get("LeavesQty") or p.get("HoldQty") or p.get("Qty") or 0
                    if exid and int(q) > 0:
                        found.append((str(exid), int(q)))
            except Exception:
                pass

            if found:
                return found
            time.sleep(0.08)
        return []

    # -------------------- BUY（マーケタブル指値） --------------------
    def buy(self, sym, board, cur_px):
        """
        新規（信用）— マーケタブル指値（疑似IOC＋段階再試行）
        Controller側がPAPER時は呼ばれない想定（LIVEのみ）。
        """
        try:
            qty = int(getattr(sym, "qty", 100))
            # 板情報
            best_bid = (board.get("BestBid") or {}).get("Price") or board.get("BidPrice")
            best_ask = (board.get("BestAsk") or {}).get("Price") or board.get("AskPrice")
            ref = board.get("CurrentPrice") or best_ask or cur_px
            tk = tick_size(sym.code, ref, board)

            # 5円刻みは成行禁止（ただし本メソッドは指値のみを使うため配慮済）
            cap_ticks = self._cap_ticks_entry
            cap_pct   = self._cap_pct_entry
            limit_px  = self._cap_price("BUY", best_bid, best_ask, ref, tk, cap_ticks, cap_pct)

            filled_total = 0
            # 1st: 疑似IOC
            ok, filled, oid = self._send_limit_ioc(sym, "BUY", qty, limit_px, is_close=False)
            filled_total += int(filled)
            tries = 0

            # 段階的再試行
            while self._retry_enabled and filled_total < qty and tries < self._retry_max:
                tries += 1
                time.sleep(max(0.05, self._retry_wait_ms / 1000.0))
                cap_ticks += self._retry_widen
                limit_px = self._cap_price("BUY", best_bid, best_ask, ref, tk, cap_ticks, cap_pct)
                ok2, filled2, oid2 = self._send_limit_ioc(sym, "BUY", qty - filled_total, limit_px, is_close=False)
                filled_total += int(filled2)

            if filled_total <= 0:
                logging.warning("[LIVE] BUY not filled (IOC+retry). skipped.")
                return {"Result": -1, "OrderId": None}

            # 直近のOrderId（返済のExecutionID参照に利用）
            if oid:
                self._last_buy_order_id[str(sym.code)] = oid

            logging.info("[LIVE] BUY filled qty=%d/%d", filled_total, qty)
            return {"Result": 0, "OrderId": oid}

        except Exception as e:
            logging.error("LIVE BUY 失敗: %s", e)
            return None

    # -------------------- SELL（返済：マーケタブル指値→必要ならFIFO/成行） --------------------
    def sell(self, sym, qty: int):
        """
        返済（信用）。以下の順で実施:
          1) /orders から直近BUYのExecutionID群を収集（~1.5s）
          2) ClosePositions 指定でマーケタブル指値（疑似IOC＋再試行）
          3) 残があれば FIFO で同様
          4) それでも残れば、設定により成行フォールバック（5円刻みは禁止可）
        戻り値: (ok: bool, resp: Any)
        """
        try:
            code = str(sym.code)
            last_oid = self._last_buy_order_id.get(code)
            execs = self._wait_exec_ids(code=code, order_id=last_oid, wait_ms=1500)

            # 板（SELL側上限・下限計算に用いる）
            try:
                b = self.api.board(sym.code, sym.exchange)
            except Exception:
                b = {}
            best_bid = (b.get("BestBid") or {}).get("Price") or b.get("BidPrice") or 0.0
            best_ask = (b.get("BestAsk") or {}).get("Price") or b.get("AskPrice") or 0.0
            ref = b.get("CurrentPrice") or best_bid
            tk = tick_size(sym.code, ref or best_bid or best_ask or 1.0, b)

            cap_ticks = self._cap_ticks_exit
            cap_pct   = self._cap_pct_exit

            filled_total = 0

            # 2) ClosePositions 指定で試行
            if execs:
                rest = int(qty)
                closes: List[Dict[str, Any]] = []
                for exid, q in execs:
                    take = min(rest, int(q))
                    if take <= 0:
                        break
                    closes.append({"HoldID": str(exid), "Qty": int(take)})
                    rest -= take

                while closes and filled_total < qty:
                    limit_px = self._cap_price("SELL", best_bid, best_ask, ref, tk, cap_ticks, cap_pct)
                    ok, filled, oid = self._send_limit_ioc(sym, "SELL", qty - filled_total, limit_px,
                                                           is_close=True, closes=closes)
                    filled_total += int(filled)
                    if filled_total >= qty:
                        break
                    if not self._retry_enabled:
                        break
                    # 残があれば段階拡張で再試行
                    tries = 0
                    while filled_total < qty and tries < self._retry_max:
                        tries += 1
                        time.sleep(max(0.05, self._retry_wait_ms / 1000.0))
                        cap_ticks += self._retry_widen
                        limit_px = self._cap_price("SELL", best_bid, best_ask, ref, tk, cap_ticks, cap_pct)
                        ok2, filled2, oid2 = self._send_limit_ioc(sym, "SELL", qty - filled_total, limit_px,
                                                                  is_close=True, closes=closes)
                        filled_total += int(filled2)
                        if filled_total >= qty:
                            break

            # 3) FIFO で残を解消（同様の指値IOC）
            if filled_total < qty:
                limit_px = self._cap_price("SELL", best_bid, best_ask, ref, tk, cap_ticks, cap_pct)
                ok, filled, oid = self._send_limit_ioc(sym, "SELL", qty - filled_total, limit_px,
                                                       is_close=True, closes=None)
                filled_total += int(filled)
                if self._retry_enabled and filled_total < qty:
                    tries = 0
                    while filled_total < qty and tries < self._retry_max:
                        tries += 1
                        time.sleep(max(0.05, self._retry_wait_ms / 1000.0))
                        cap_ticks += self._retry_widen
                        limit_px = self._cap_price("SELL", best_bid, best_ask, ref, tk, cap_ticks, cap_pct)
                        ok2, filled2, oid2 = self._send_limit_ioc(sym, "SELL", qty - filled_total, limit_px,
                                                                  is_close=True, closes=None)
                        filled_total += int(filled2)

            # 4) まだ残る ⇒ 設定により成行フォールバック（5円刻みは原則禁止）
            remaining = max(0, qty - filled_total)
            if remaining > 0:
                allow_market = self._force_exit_allow_market and not (tk >= 5.0 and self._forbid_market_5)
                if allow_market:
                    logging.warning("[LIVE] SELL fallback to MARKET for remaining=%d", remaining)
                    res_m = self._place_market(sym, "SELL", remaining, is_close=True, closes=None)
                    ok_m = bool(isinstance(res_m, dict) and res_m.get("Result") == 0)
                    return ok_m, res_m
                else:
                    logging.error("[LIVE] SELL remaining not cleared and MARKET forbidden (5yen or setting). remaining=%d", remaining)
                    return False, {"Result": -1, "Message": "Remaining not cleared, market forbidden."}

            return True, {"Result": 0, "OrderId": self._last_buy_order_id.get(code)}

        except Exception as e:
            logging.error("LIVE SELL 失敗: %s", e)
            return False, str(e)


