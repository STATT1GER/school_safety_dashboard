@echo off
cd /d "%~dp0"
where uv >nul 2>nul
if %errorlevel%==0 (
    uv run streamlit run app.py
) else (
    python -m streamlit run app.py
)
pause
