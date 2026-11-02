@echo off
REM Futbol bitince: basketbol guncelle, GitHub a gonder, bilgisayari kapat
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
.venv\Scripts\python futbol_bekle.py
.venv\Scripts\python guncelle_hepsi.py basketbol
echo Basladi %date% %time% > yayinla_log.txt
.venv\Scripts\python hazir.py >> yayinla_log.txt 2>&1
git add -A >> yayinla_log.txt 2>&1
git commit -m "Veri guncellemesi %date% %time%" >> yayinla_log.txt 2>&1
git pull --no-rebase --no-edit -X ours origin main >> yayinla_log.txt 2>&1
git push >> yayinla_log.txt 2>&1
echo PUSH_KODU %errorlevel% >> yayinla_log.txt
git log -1 --oneline >> yayinla_log.txt 2>&1
echo BITTI >> yayinla_log.txt
echo Bilgisayar 2 dakika icinde kapanacak. Iptal: shutdown /a
shutdown /s /t 120
