# -*- coding: utf-8 -*-
"""
notifier.py
Discord / LINE に通知を送るための軽量モジュール (Universal Fix版)
"""
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime

class Notifier:
    def __init__(self, cfg):
        self.discord_url = ""
        self.switches = {}

        # ---------------------------------------------------------
        # 1. "notification" ブロックを探し出す (辞書 or オブジェクト)
        # ---------------------------------------------------------
        notify_block = None
        
        # パターンA: cfg.notification (オブジェクト)
        if hasattr(cfg, "notification"):
            notify_block = getattr(cfg, "notification")
        # パターンB: cfg["notification"] (辞書)
        elif isinstance(cfg, dict) and "notification" in cfg:
            notify_block = cfg["notification"]
        
        # ---------------------------------------------------------
        # 2. ブロックの中から URL を引きずり出す
        # ---------------------------------------------------------
        if notify_block:
            # 設定スイッチの取得
            if isinstance(notify_block, dict):
                self.switches = notify_block
            else:
                # オブジェクトなら __dict__ 経由などで辞書化を試みる
                try:
                    self.switches = vars(notify_block)
                except:
                    self.switches = {}

            # URLの取得 (オブジェクト属性優先)
            if hasattr(notify_block, "discord_webhook_url"):
                self.discord_url = str(getattr(notify_block, "discord_webhook_url", "") or "")
            # 辞書キー
            elif isinstance(notify_block, dict):
                self.discord_url = str(notify_block.get("discord_webhook_url", "") or "")
        
        # 余計な空白除去
        self.discord_url = self.discord_url.strip()

        # ★デバッグ出力
        print(f"[Notifier DEBUG] 抽出結果 URL: {self.discord_url[:10]}... (Len: {len(self.discord_url)})")

    def send(self, message, event_type="info"):
        key = f"notify_{event_type}" if event_type != "info" else None
        
        # スイッチ確認 (辞書になかった場合はオブジェクト属性も念のため確認)
        if key:
            is_on = self.switches.get(key, True)
            # もしswitchesが空で、実はオブジェクト属性だった場合の保険
            if not self.switches and hasattr(self, "cfg_ref"): 
                pass # 簡易化のため省略
            if not is_on:
                return

        full_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"

        if self.discord_url and self.discord_url.startswith("http"):
            self._send_discord(full_msg, event_type)
        else:
            # URLがない場合は静かに無視（ログだけ出す）
            pass

    def _send_discord(self, text, event_type):
        try:
            color = 0x3498db 
            if event_type == "error": color = 0xe74c3c 
            elif event_type == "entry": color = 0x2ecc71 
            elif event_type == "exit": color = 0xf1c40f 

            payload = {
                "username": "KABU-EVO Bot",
                "embeds": [{
                    "description": text,
                    "color": color
                }]
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.discord_url, data=data,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req) as res:
                pass
        except Exception as e:
            logging.warning(f"[Notifier] Discord送信失敗: {e}")