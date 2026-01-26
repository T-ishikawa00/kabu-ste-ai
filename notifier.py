# -*- coding: utf-8 -*-
"""
notifier.py v2.1
================
Discord通知を一手に引き受けるクラス。
urllibを使用しているため、追加ライブラリ不要で確実に通知を届けます。

【v2.1】config_loader.py の NotificationCfg に対応
"""
import json
import logging
import urllib.request
import threading


class Notifier:
    def __init__(self, cfg):
        self.webhook_url = None
        try:
            # cfg.notification (NotificationCfg dataclass) から取得
            notif = getattr(cfg, "notification", None)
            
            if notif:
                self.webhook_url = getattr(notif, "discord_webhook_url", None)
            
            # 見つからない場合、cfg直下も探す（後方互換）
            if not self.webhook_url:
                self.webhook_url = getattr(cfg, "discord_webhook_url", None)
            
            # 空文字列の場合はNoneに
            if self.webhook_url == "":
                self.webhook_url = None
            
            if self.webhook_url:
                masked = "..." + self.webhook_url[-5:] if len(self.webhook_url) > 5 else "***"
                logging.info(f"📢 Notification System: ON (Discord {masked})")
            else:
                logging.warning("🔕 Notification System: OFF (No URL)")
                
        except Exception as e:
            logging.warning(f"🔕 Notification config error: {e}")

    def send(self, message: str, msg_type: str = "info"):
        """
        メッセージをDiscordに送信
        msg_type: "entry", "exit", "error", "info", "startup"
        """
        if not self.webhook_url:
            return

        def _worker():
            try:
                # メッセージに絵文字を付与（重複防止）
                icon = ""
                if msg_type == "entry" and not message.startswith("🚀"):
                    icon = "🚀 "
                elif msg_type == "exit" and not any(message.startswith(e) for e in ["💰", "💸"]):
                    icon = "💰 "
                elif msg_type == "error" and not message.startswith("⚠️"):
                    icon = "⚠️ "
                elif msg_type == "startup" and not message.startswith("🤖"):
                    icon = "🤖 "
                
                content = f"{icon}{message}"
                
                data = json.dumps({"content": content}).encode("utf-8")
                req = urllib.request.Request(
                    self.webhook_url, 
                    data=data, 
                    headers={
                        "Content-Type": "application/json", 
                        "User-Agent": "KabuSteAI/1.0"
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as res:
                    pass
                    
            except Exception as e:
                logging.debug(f"Discord send failed: {e}")

        # トレード処理を邪魔しないよう別スレッドで送信
        threading.Thread(target=_worker, daemon=True).start()