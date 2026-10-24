@echo off
REM Yerelde guncellenen verileri GitHub a gonderir; Streamlit Cloud birkac dakika icinde yeni veriyle acilir.
cd /d "%~dp0"
git add -A
git commit -m "Veri guncellemesi %date% %time%"
git push
echo.
echo Yayinlandi. Streamlit Cloud birkac dakika icinde guncellenir.
pause
