@echo off
cd /d C:\trade_bot\kabu-ste-ai

echo ==========================================
echo  Morning Auto Start - %date% %time%
echo ==========================================

:: 1. 8:30になるまで待機
echo [1/6] Waiting for 8:30...
py -3.12 wait_time.py

:: 2. kabuステーション自動ログイン
echo [2/6] Starting kabuStation login...
py -3.12 auto_login.py

:: 3. ログイン完了まで待機 (20秒)
echo [3/6] Waiting for login to complete...
timeout /t 20 /nobreak

:: 4. 8:40まで待機（板情報が出揃うのを待つ）
echo [4/6] Waiting until 8:40 for board data...
py -3.12 wait_840.py

:: 5. 朝の板チェック（候補10銘柄→最終5銘柄に絞る）
echo [5/6] Running morning board check...
py -3.12 step7_morning_filter.py
if errorlevel 1 (
    echo Warning: Board check had issues, continuing anyway...
)

:: 6. 8:50まで待機してからボット起動（wakeup.pyがmain.pyを起動）
echo [6/6] Waiting for 8:50 to start trading...
py -3.12 wakeup.py

echo ==========================================
echo  Bot stopped: %time%
echo ==========================================
pause