@echo off
cd /d C:\trade_bot\kabu-ste-ai

:: 1. まず8:30になるまでここで止まる（二重の保険）
py -3.12 wait_time.py

:: 2. 8:30になったら自動ログイン開始
py -3.12 auto_login.py

:: 3. ログイン完了まで少し待つ (20秒待機)
timeout /t 20

:: 4. ログイン後の処理 (APIパスワード入力など) を実行
py -3.12 wakeup.py

pause