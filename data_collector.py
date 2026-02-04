# -*- coding: utf-8 -*-
"""
data_collector.py v22.5 (High-Speed Logger)
===========================================
【v22.5 変更点】
1. バージョン番号をシステム全体(v22.5)と統一
2. Master_AI_v2 の学習に必要な「深層板情報(OverSell/UnderBuy)」を確実に記録
3. リアルタイム書き込み方式 (クラッシュ時のデータロスト防止)
"""
import os
import csv
import datetime
import logging

class DataCollector:
    """
    全銘柄の市場データをCSVに高速保存するクラス
    Master_AI_v2 (18次元) の再学習に必要なデータを網羅的に記録する。
    """
    def __init__(self, save_dir="data_raw"):
        self.save_dir = save_dir
        self.columns = [
            "timestamp", "code", "price", "volume", 
            "bid_price", "bid_qty", 
            "ask_price", "ask_qty", 
            "vwap",
            "over_sell_qty",   # ★重要: AIの「売り圧力」判定に使用
            "under_buy_qty",   # ★重要: AIの「買い圧力」判定に使用
            "market_cap"       # 時価総額
        ]
        self.current_dir = None
        self._ensure_daily_dir()

    def _ensure_daily_dir(self):
        """今日の日付フォルダを作成 (data_raw/20260201/...)"""
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        self.current_dir = os.path.join(self.save_dir, today_str)
        if not os.path.exists(self.current_dir):
            os.makedirs(self.current_dir, exist_ok=True)
            logging.info(f"📁 Created data directory: {self.current_dir}")

    def log(self, code: str, board: dict):
        """
        リアルタイムにCSVへ追記する
        """
        try:
            # 1. フォルダのチェック (日付またぎ対応)
            today_str = datetime.datetime.now().strftime("%Y%m%d")
            if self.current_dir and today_str not in self.current_dir:
                self._ensure_daily_dir()

            # 2. データの抽出
            # 現在値 (APIのバージョン揺れに対応)
            price = board.get("CurrentPrice") or board.get("CurrentPriceV2") or board.get("LastTradedPrice")
            
            # 出来高
            vol = board.get("TradingVolume") or board.get("Volume") or board.get("CurrentVolume")
            
            # 板情報
            best_bid = board.get("BestBid") or {}
            best_ask = board.get("BestAsk") or {}
            bid_price = best_bid.get("Price") or board.get("BidPrice")
            bid_qty   = best_bid.get("Qty")   or board.get("BidQty")
            ask_price = best_ask.get("Price") or board.get("AskPrice")
            ask_qty   = best_ask.get("Qty")   or board.get("AskQty")
            
            # VWAP
            vwap = board.get("VWAP")

            # 深層情報 (AI v22.5 の肝)
            over_sell = board.get("OverSellQty")
            under_buy = board.get("UnderBuyQty")
            market_cap = board.get("TotalMarketValue")

            # 必須データがない場合はスキップ（寄り付き前などのゴミデータ排除）
            if price is None or vol is None:
                return

            # 3. CSVへの書き込み
            file_path = os.path.join(self.current_dir, f"{code}.csv")
            file_exists = os.path.exists(file_path)
            
            # 'a'モードで追記 (都度closeすることでデータ破損を防ぐ)
            with open(file_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(self.columns)
                
                ts = datetime.datetime.now().isoformat()
                
                row = [
                    ts, code, price, vol,
                    bid_price, bid_qty,
                    ask_price, ask_qty,
                    vwap,
                    over_sell,
                    under_buy,
                    market_cap
                ]
                writer.writerow(row)

        except Exception as e:
            # エラーが出ても止まらないようにwarningだけ出す
            logging.warning(f"[DataCollector] Error saving {code}: {e}")

    # 互換性維持のためのエイリアス
    def log_board(self, code: str, board: dict):
        self.log(code, board)

    def save_all(self):
        """
        終了時の処理。
        リアルタイム書き込みのためバッファフラッシュは不要だが、
        Controllerからの呼び出し用に定義しておく。
        """
        pass