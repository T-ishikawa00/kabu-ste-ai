# -*- coding: utf-8 -*-
import os
import csv
import datetime
import logging

class DataCollector:
    """
    全銘柄の市場データをCSVに高速保存するクラス (Phase 1: Deep Market Depth対応版)
    OverSellQty / UnderBuyQty を記録し、隠れた需給バランスを可視化する
    """
    def __init__(self, save_dir="data_raw"):
        self.save_dir = save_dir
        # 保存するデータの項目（カラム名）
        self.columns = [
            "timestamp", "code", "price", "volume", 
            "bid_price", "bid_qty", 
            "ask_price", "ask_qty", 
            "vwap",
            # ★追加: 深層板情報 (Deep Market Depth)
            "over_sell_qty",   # 成行売りなど、板の上に見えない売り圧力
            "under_buy_qty",   # 成行買いなど、板の下に見えない買い圧力
            "market_cap"       # 時価総額 (銘柄規模の正規化用)
        ]
        self.current_dir = None
        self._ensure_daily_dir()

    def _ensure_daily_dir(self):
        """今日の日付フォルダを作成 (data_raw/20260106/...)"""
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        self.current_dir = os.path.join(self.save_dir, today_str)
        if not os.path.exists(self.current_dir):
            os.makedirs(self.current_dir, exist_ok=True)
            logging.info(f"📁 Created data directory: {self.current_dir}")

    def log_board(self, code: str, board: dict):
        """
        Kabuステーションの板情報(board)を受け取り、Deep Depth情報を含めてCSVに追記する
        """
        try:
            # 日付が変わったらフォルダを作り直すチェック
            today_str = datetime.datetime.now().strftime("%Y%m%d")
            if self.current_dir and today_str not in self.current_dir:
                self._ensure_daily_dir()

            # --- データの抽出 ---
            # 現在値
            price = board.get("CurrentPrice") or board.get("CurrentPriceV2") or board.get("LastTradedPrice")
            
            # 出来高
            vol = board.get("TradingVolume") or board.get("Volume") or board.get("CurrentVolume")
            
            # 板情報の抽出
            best_bid = board.get("BestBid") or {}
            best_ask = board.get("BestAsk") or {}
            
            bid_price = best_bid.get("Price") or board.get("BidPrice")
            bid_qty   = best_bid.get("Qty")   or board.get("BidQty")
            
            ask_price = best_ask.get("Price") or board.get("AskPrice")
            ask_qty   = best_ask.get("Qty")   or board.get("AskQty")
            
            vwap = board.get("VWAP")

            # ★追加: 深層情報の抽出
            # KabuステーションAPI固有のフィールド
            over_sell = board.get("OverSellQty")
            under_buy = board.get("UnderBuyQty")
            market_cap = board.get("TotalMarketValue")

            # データが欠けている場合は保存しない（ゴミデータ防止）
            if price is None:
                return

            # --- CSVへの書き込み ---
            file_path = os.path.join(self.current_dir, f"{code}.csv")
            
            file_exists = os.path.exists(file_path)
            
            # 毎回open/closeすることで、途中で落ちてもデータが消えにくいようにする
            with open(file_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(self.columns)
                
                # 時刻 (ISO形式)
                ts = datetime.datetime.now().isoformat()
                
                # 行データの作成
                row = [
                    ts, code, price, vol,
                    bid_price, bid_qty,
                    ask_price, ask_qty,
                    vwap,
                    # ★追加分
                    over_sell,
                    under_buy,
                    market_cap
                ]
                writer.writerow(row)

        except Exception as e:
            # ログ保存のエラーで本体を止めないようにする
            logging.warning(f"[DataCollector] Error saving {code}: {e}")