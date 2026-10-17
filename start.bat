@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ModelDock] 首次运行，正在创建运行环境…
  python -m venv .venv || (echo 请先安装 Python 3.10+ & pause & exit /b 1)
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe run.py %*
pause
