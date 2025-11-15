# -*- coding: utf-8 -*-
import datetime

class RegimeManager:
    """
    時刻から市場レジームを判定する:
      - OPEN: 寄り直後 (リスク高め)
      - AM  : 午前
      - PM  : 午後
    """

    def __init__(self,
                 open_end: str = "09:10",
                 lunch_end: str = "12:30"):
        # "HH:MM" 形式で受け取る前提（JST）
        self.open_end = datetime.time.fromisoformat(open_end)
        self.lunch_end = datetime.time.fromisoformat(lunch_end)

    def get_regime(self, ts: datetime.datetime) -> str:
        t = ts.time()
        if t < self.open_end:
            return "OPEN"
        if t < self.lunch_end:
            return "AM"
        return "PM"

    def is_open(self, ts: datetime.datetime) -> bool:
        return self.get_regime(ts) == "OPEN"
