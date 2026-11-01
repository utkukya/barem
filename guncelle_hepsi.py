"""
Tüm güncellemeleri sırayla çalıştırır (futbol, basketbol, NBA, EuroLeague) ve tabloları hazırlar.
Kayıt hem ekrana hem guncelleme_log.txt dosyasına yazılır.
Kullanım: guncelle_hepsi.bat (ya da python guncelle_hepsi.py [futbol basketbol nba euroleague])
"""
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

KLASOR = Path(__file__).resolve().parent
LOG = KLASOR / "guncelleme_log.txt"
try:  # Windows konsolunda Türkçe karakterler hata vermesin
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
_dosya = open(LOG, "w", encoding="utf-8")


def yaz(m):
    satir = f"[{datetime.now():%H:%M:%S}] {m}"
    print(satir, flush=True)
    _dosya.write(satir + "\n")
    _dosya.flush()


def adim(ad, fonk):
    yaz(f"===== {ad} başladı =====")
    t0 = time.time()
    try:
        fonk()
        yaz(f"===== {ad} bitti ({(time.time() - t0) / 60:.1f} dk) =====")
    except Exception:
        yaz(f"!!!!! {ad} HATA:\n{traceback.format_exc()}")


def futbol():
    import iddaa_baremler as ib
    import hazir
    ib.guncelle(1, yazdir=yaz, ligler="hepsi", takim_verisi="akilli")
    hazir.hepsini_hazirla(yazdir=yaz, sporlar=("futbol",))


def basketbol():
    import basketbol as bk
    import hazir
    bk.guncelle(2, "hepsi", yazdir=yaz, takim_verisi=True)
    hazir.hepsini_hazirla(yazdir=yaz, sporlar=("basketbol",))


def lig(ad):
    def calis():
        import hazir
        import ligler
        nba = ligler.modul(ad)
        nba.guncelle(yazdir=yaz)
        try:
            nba.bulten_guncelle(1, yazdir=yaz)
        except Exception as e:
            yaz(f"iddaa bülteni alınamadı: {e}")
        try:
            nba.baremleri_guncelle(yazdir=yaz)
        except Exception as e:
            yaz(f"Oyuncu baremleri alınamadı: {e}")
        hazir.hepsini_hazirla(yazdir=yaz, sporlar=(ad,))
    return calis


ADIMLAR = {"futbol": ("Futbol", futbol), "basketbol": ("Basketbol", basketbol),
           "nba": ("NBA", lig("nba")), "euroleague": ("EuroLeague", lig("euroleague"))}

if __name__ == "__main__":
    secim = [a for a in sys.argv[1:] if a in ADIMLAR] or list(ADIMLAR)
    yaz(f"Güncelleme başladı: {', '.join(secim)}")
    for a in secim:
        adim(*ADIMLAR[a])
    yaz("TÜMÜ BİTTİ")
    _dosya.close()
