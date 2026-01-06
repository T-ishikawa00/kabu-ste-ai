# test_notify.py
# 通知機能だけの単体テスト
import yaml
from notifier import Notifier

class MockConfig:
    def __init__(self, raw_dict):
        self.notification = raw_dict.get("notification", {})

# config.yaml を読み込む
try:
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg_data = yaml.safe_load(f)
        print("✅ config.yaml の読み込み成功")
except Exception as e:
    print(f"❌ config.yaml が読めません: {e}")
    exit()

# 通知テスト
print(f"現在の設定: {cfg_data.get('notification', {})}")
cfg = MockConfig(cfg_data)
bot = Notifier(cfg)

print("📨 テスト通知を送信中...")
bot.send("🔔 テスト通知です！これが届けば設定OKです。", "info")
print("完了。Discordを確認してください。")