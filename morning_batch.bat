@echo off
REM ==========================================
REM morning_batch.bat - 朝のスクリーニング＆取引開始
REM 08:50 に実行推奨
REM ==========================================

echo ==========================================
echo  朝のバッチ処理: %date% %time%
echo ==========================================

cd /d C:\trade_bot\kabu-ste-ai

REM 1. kabuステーションのランキングからスクリーニング
echo.
echo [1/2] 出来高急増銘柄をスクリーニング中...
py -3.12 step2_screening_kabustation.py
if errorlevel 1 (
    echo ❌ スクリーニングに失敗しました
    echo    kabuステーションが起動しているか確認してください
    pause
    exit /b 1
)
echo ✅ スクリーニング完了

REM 2. メインボット起動
echo.
echo [2/2] トレードボット起動中...
py -3.12 main.py

echo.
echo ==========================================
echo  終了: %time%
echo ==========================================
pause