@echo off
REM Yerelde guncellenen verileri GitHub a gonderir; Streamlit Cloud birkac dakika icinde yeni veriyle acilir.
cd /d "%~dp0"
echo Hazir tablolar hesaplaniyor (yayindaki surum hesaplama yapmadan okur)...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" hazir.py
) else (
    python hazir.py
)
git add -A
git commit -m "Veri guncellemesi %date% %time%"
git push
echo.
echo Yayinlandi. Streamlit Cloud birkac dakika icinde guncellenir.
pause
