@echo off
cd /d %~dp0

echo ==========================================
echo  Night Batch Process Started
echo ==========================================
echo.

echo [1/3] Updating J-Quants Data...
py -3.12 step1_jquants_daily.py
echo [OK] Step 1 Finished.
echo.

echo [2/3] Running Screening...
py -3.12 step2_screening.py
echo [OK] Step 2 Finished.
echo.

echo [3/3] Updating Config...
py -3.12 step6_inject_config.py
echo [OK] Step 3 Finished.
echo.

echo ==========================================
echo  Night Batch Completed Successfully!
echo  Check target_list.csv for results.
echo ==========================================
pause