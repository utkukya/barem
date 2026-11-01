@echo off
cd /d "%~dp0"
.venv\Scripts\python guncelle_hepsi.py %*
echo.
echo Bitti. Kayit: guncelleme_log.txt
pause
