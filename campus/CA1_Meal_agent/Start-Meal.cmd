@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create the Python environment first. See README.md.
  pause
  exit /b 1
)
echo CA1 Meal: http://127.0.0.1:8030
".venv\Scripts\python.exe" -m meal.api
pause
