@echo off
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8000
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --reload
