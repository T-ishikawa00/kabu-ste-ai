# -*- coding: utf-8 -*-
import time, logging, datetime
from typing import Optional, Dict, Any, List, Tuple

class Broker:
    """
    安定版: 成行オンリー + 制度信用ペイロード固定
      - 新規: 信用新規 成行 (FrontOrderType=10)
      - 返済: ExecutionIDをClosePositions.HoldIDに指定（取得不可ならFIFO）
      - ガード: API遅延/取引時間/同時建玉数/同銘柄重複をBroker内でも弾く
    """

    def __init__(self, api, cfg):
        self.api, self.cfg = api, cfg
        self.paper = bool(getattr(cfg, "paper_trading", False))
        self.pos: Dict[str, Dict[str, Any]] = {}
        self.last_order_ts: Dict[str, float] = {}
        self.fail_ts: List[float] = []
        self._last_buy_order_id: Dict[str, str] = {}

        # 信用区分（既存互換のまま）
        cred = getattr(cfg, "credit", object())
        self._mt = int(getattr(cred, "margin_type", 1))            # 3=デイトレ, 1=制度（あなたの環境の既存値を優先）
        self._mt_fallback = bool(getattr(cred, "fallback_to_1", True))

        # 同時建玉上限（config.portfolio.max_open_positions を参照）
        port = getattr(cfg, "portfolio", object())
        self._max_open_positions = int(getattr(port, "max_open_positions", 1))

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

    # -------------------- 建玉チェック（Broker内ガード） --------------------
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

    # -------------------- ExecutionID 収集（~1.5s） --------------------
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

            # 2) /positions（ExecutionID が入る環境あり）
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

    # -------------------- 新規（信用 成行） --------------------
    def _live_buy_credit(self, sym, qty: int):
        # ★ペイロードはあなたの安定版に完全準拠（変更なし）
        payload = {
            "Password": self.cfg.api.trade_password,
            "Symbol": str(sym.code),
            "Exchange": int(sym.exchange),
            "SecurityType": 1,
            "Side": 2,                 # 買い（新規）
            "CashMargin": 2,           # 信用 新規
            "MarginTradeType": 3,      # ※あなたの安定版通り
            "DelivType": 0,
            "FundType": 11,            # 信用
            "AccountType": 4,          # 特定
            "Qty": int(qty),
            "FrontOrderType": 10,      # 成行
            "Price": 0,
            "ExpireDay": 0,
        }
        self._coerce_ints(payload, ["Exchange","SecurityType","Side","CashMargin",
                                    "MarginTradeType","DelivType","FundType","AccountType",
                                    "Qty","FrontOrderType","Price","ExpireDay"])
        logging.info("[LIVE] sendorder BUY payload=%s", payload)
        res = self.api.sendorder(payload)
        if res and isinstance(res, dict) and res.get("OrderId"):
            self._last_buy_order_id[str(sym.code)] = res["OrderId"]
        return res

    # -------------------- 返済：建玉指定（ExecutionID配列） --------------------
    def _live_sell_credit_close_multi(self, sym, qty: int, execs: List[Tuple[str, int]]):
        rest = int(qty)
        closes: List[Dict[str, Any]] = []
        for exid, q in execs:
            take = min(rest, int(q))
            if take <= 0:
                break
            closes.append({"HoldID": str(exid), "Qty": int(take)})  # HoldIDにExecutionID
            rest -= take
        if not closes:
            return None

        payload = {
            "Password": self.cfg.api.trade_password,
            "Symbol": str(sym.code),
            "Exchange": int(sym.exchange),
            "SecurityType": 1,
            "Side": 1,                 # 売り（返済）
            "CashMargin": 3,           # 信用 返済
            "MarginTradeType": 3,      # ※あなたの安定版通り
            "DelivType": 2,            # 返済
            "FundType": 11,            # 信用
            "AccountType": 4,          # 特定
            "Qty": int(qty),
            "ClosePositions": closes,  # ClosePositionOrder とは排他
            "FrontOrderType": 10,      # 成行
            "Price": 0,
            "ExpireDay": 0,
        }
        self._coerce_ints(payload, ["Exchange","SecurityType","Side","CashMargin",
                                    "MarginTradeType","DelivType","FundType","AccountType",
                                    "Qty","FrontOrderType","Price","ExpireDay"])
        logging.info("[LIVE] sendorder SELL payload=%s", payload)
        return self.api.sendorder(payload)

    # -------------------- 返済：FIFO（ClosePositionOrder=0） --------------------
    def _live_sell_credit_fifo(self, sym, qty: int):
        payload = {
            "Password": self.cfg.api.trade_password,
            "Symbol": str(sym.code),
            "Exchange": int(sym.exchange),
            "SecurityType": 1,
            "Side": 1,                 # 売り（返済）
            "CashMargin": 3,           # 信用 返済
            "MarginTradeType": 1,      # ※あなたの安定版通り（ここは1のまま）
            "DelivType": 2,            # 返済
            "FundType": 11,            # 信用
            "AccountType": 4,          # 特定
            "Qty": int(qty),
            "ClosePositionOrder": 0,   # FIFO
            "FrontOrderType": 10,      # 成行
            "Price": 0,
            "ExpireDay": 0,
        }
        self._coerce_ints(payload, ["Exchange","SecurityType","Side","CashMargin",
                                    "MarginTradeType","DelivType","FundType","AccountType",
                                    "Qty","ClosePositionOrder","FrontOrderType","Price","ExpireDay"])
        logging.info("[LIVE] sendorder SELL payload=%s", payload)
        return self.api.sendorder(payload)

    # -------------------- 外部呼び出し --------------------
    def buy(self, sym, board, cur_px):
        """新規（信用 成行） + 同時建玉/同銘柄ガード"""
        try:
            if not self.can_trade_now(is_entry=True):
                logging.warning("[GUARD] time/latency gate blocks entry")
                return None

            # Broker内ガード（同時建玉・同銘柄）
            if self._open_count() >= self._max_open_positions:
                logging.warning("[GUARD] max_open_positions reached: %d", self._max_open_positions)
                return None
            if self._has_symbol_open(str(sym.code)):
                logging.warning("[GUARD] symbol already open: %s", sym.code)
                return None

            qty = int(getattr(sym, "qty", 100))
            res = self._live_buy_credit(sym, qty)
            if not res or res.get("Result") != 0:
                logging.error("LIVE BUY 失敗(信用): %s", res)
                return None
            return res
        except Exception as e:
            logging.error("LIVE BUY 失敗(信用): %s", e)
            return None

    def sell(self, sym, qty: int):
        """
        返済。順序:
          1) /orders のExecutionID群を ~1.5s 収集
          2) 取れた分は ClosePositions で指定返済 成行
          3) ダメなら FIFO 成行
        """
        try:
            if not self.can_trade_now(is_entry=False):
                logging.warning("[GUARD] time/latency gate blocks exit")
                return False, "guard"

            code = str(sym.code)
            last_oid = self._last_buy_order_id.get(code)

            execs = self._wait_exec_ids(code=code, order_id=last_oid, wait_ms=1500)
            if execs:
                # 約定検知したら fill_ts を記録（必要なら）
                if str(sym.code) in self.pos:
                    self.pos[str(sym.code)]["fill_ts"] = time.time()

                res = self._live_sell_credit_close_multi(sym, qty, execs)
                if res and isinstance(res, dict) and res.get("Result") == 0:
                    return True, res

            logging.warning("ExecutionID未取得。FIFO返済にフォールバックします。（code=%s, order=%s）", code, last_oid)
            res2 = self._live_sell_credit_fifo(sym, qty)
            ok2 = bool(res2 and isinstance(res2, dict) and res2.get("Result") == 0)
            return ok2, res2

        except Exception as e:
            logging.error("LIVE SELL 失敗(信用): %s", e)
            return False, str(e)
