@echo off
cd /d "%~dp0"
start "Basketbol" .venv\Scripts\streamlit run basketbol_app.py --server.port 8502
