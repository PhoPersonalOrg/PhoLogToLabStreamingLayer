@echo off

echo Starting LSL Logger App...

if exist ".venv\Scripts\python.exe" (

    .venv\Scripts\python.exe logger_app.py %*

) else (

    uv run logger_app %*

)

pause

