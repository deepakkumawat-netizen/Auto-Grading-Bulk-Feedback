@echo off
echo Starting AutoGrader...
start "AutoGrader Backend" cmd /k "cd /d %~dp0backend && venv\Scripts\activate && uvicorn main:app --port 8031 --reload"
timeout /t 3 /nobreak >nul
start "AutoGrader Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
timeout /t 4 /nobreak >nul
echo.
echo Backend:  http://127.0.0.1:8031
echo Frontend: http://localhost:5181
echo.
start http://localhost:5181
