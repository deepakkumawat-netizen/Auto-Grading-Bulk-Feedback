@echo off
echo Starting Auto Grade...
start "Auto Grade Backend" cmd /k "cd /d %~dp0backend && venv\Scripts\python.exe -m uvicorn main:app --port 8031 --reload"
ping -n 4 127.0.0.1 >nul
start "Auto Grade Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
ping -n 5 127.0.0.1 >nul
echo.
echo Backend:  http://127.0.0.1:8031
echo Frontend: http://localhost:5181
echo.
start http://localhost:5181
