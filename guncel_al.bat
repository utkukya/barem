@echo off
REM Buluttan (Streamlit) yapilan guncellemeleri bu bilgisayara indirir. Yerelde guncellemeye baslamadan once calistirin.
cd /d "%~dp0"
git add -A
git commit -m "Yerel degisiklikler %date% %time%"
git pull --no-rebase --no-edit -X theirs origin main
echo.
echo Bulutta yapilan guncellemeler indirildi.
pause
