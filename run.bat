@echo off
rem Desk Robot 上位机启动脚本
cd /d "%~dp0"
where python >nul 2>nul && (python main.py) || (py -3.14 main.py)
if errorlevel 1 pause
