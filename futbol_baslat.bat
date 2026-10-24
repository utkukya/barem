@echo off
cd /d "%~dp0"
start "Futbol" .venv\Scripts\streamlit run app.py --server.port 8501
