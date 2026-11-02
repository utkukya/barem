"""
Arayüzden başlatılan futbol güncellemesinin bitmesini bekler (en fazla 8 saat).
Bitti sayılır: hazır tablolar (barem tarayıcı) son güncellemenin çekilme zamanıyla yazılmış ve futbol.db
2 dakikadır değişmiyor.
"""
import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent / "futbol.db"
LOG = Path(__file__).resolve().parent / "bekleme_log.txt"


def yaz(m):
    satir = f"[{datetime.now():%H:%M:%S}] {m}"
    print(satir, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(satir + "\n")


def bitti_mi():
    try:
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30) as con:
            cekilme = con.execute("SELECT cekilme FROM barem_meta").fetchone()[0]
            damga = con.execute("SELECT damga FROM hazir_tablo WHERE anahtar LIKE 'futbol_barem|%'").fetchone()
    except Exception as hata:
        return False, f"okunamadı ({hata})"
    hazir = bool(damga) and str(damga[0]).startswith(str(cekilme))
    sessiz = time.time() - DB.stat().st_mtime > 120
    return hazir and sessiz, f"çekilme {cekilme}, hazır tablo {'güncel' if hazir else 'eski'}"


if __name__ == "__main__":
    LOG.write_text("", encoding="utf-8")
    yaz("Futbol güncellemesinin bitmesi bekleniyor…")
    t0 = time.time()
    while True:
        ok, durum = bitti_mi()
        if ok:
            yaz(f"Futbol güncellemesi bitmiş ({durum}).")
            break
        if time.time() - t0 > 8 * 3600:
            yaz(f"8 saat beklendi, futbol hâlâ bitmedi ({durum}); devam ediliyor.")
            break
        if int(time.time() - t0) % 600 < 30:
            yaz(f"  bekleniyor: {durum}")
        time.sleep(30)
