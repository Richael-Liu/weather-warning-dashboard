@echo off
cd /d %~dp0
set NO_PROXY=www.weather.com.cn,weather.com.cn,*
set no_proxy=www.weather.com.cn,weather.com.cn,*
python -u weathercomcn_warning_dashboard.py >> weathercomcn_daily.log 2>&1
if errorlevel 1 (
    echo Failed. See weathercomcn_daily.log
) else (
    echo Done. Check output folder.
)
pause
