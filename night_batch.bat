@echo off
REM ==========================================
REM night_batch.bat - 夜間スクリーニング (J-Quants版)
REM 17:00以降に実行推奨
REM ==========================================
@echo off
rem 文字コードをUTF-8に変更
chcp 65001 >nul
echo ==========================================
echo  夜間バッチ処理: %date% %time%
echo ==========================================

cd /d C:\trade_bot\kabu-ste-ai

REM 1. J-Quants から日足データ取得
echo.
echo [1/3] 日足データ更新中 (J-Quants API)...
py -3.12 step1_jquants_daily.py
if errorlevel 1 (
    echo ❌ 日足取得に失敗しました
    pause
    exit /b 1
)
echo ✅ 日足更新完了

REM 2. スクリーニング実行
echo.
echo [2/3] スクリーニング実行中...
py -3.12 step2_screening.py
if errorlevel 1 (
    echo ❌ スクリーニングに失敗しました
    pause
    exit /b 1
)
echo ✅ スクリーニング完了

REM 3. config.yaml に注入
echo.
echo [3/3] config.yaml 更新中...
py -3.12 step6_inject_config.py
if errorlevel 1 (
    echo ❌ config更新に失敗しました
    pause
    exit /b 1
)
echo ✅ config更新完了

echo.
echo ==========================================
echo  夜間バッチ完了: %time%
echo  明日の監視銘柄が更新されました！
echo ==========================================
pause