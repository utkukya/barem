@echo off
REM Giris kullanici adi ve sifresini ayarlar (sifre ekranda gorunmez).
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" giris_ayarla.py
) else (
    python giris_ayarla.py
)
pause
