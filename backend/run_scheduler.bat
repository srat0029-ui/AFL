@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -m app.player_modelling.cli run-scheduler
