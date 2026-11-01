"""
Flashscore'dan futbol takım istatistikleri (korner, kart, şut, faul, taç... — tüm maç, 1. ve 2. yarı).
Ofsayt.com ile iş bölümü yapar: istatistik gereken takımların bir kısmı buradan, kalanı Ofsayt'tan çekilir.
Bir takım hangi siteden çekilmeye başlandıysa hep oradan çekilir (geçmişi tek kaynakta, aynı adla kalır).

  - Takımın maçları: takımın sonuçlar sayfası (takım başına 1 istek; son ~13 maç ve sıradaki maçın saati).
  - Maç istatistikleri: maç başına 1 istek. Bir kez çekilen maç tekrar istenmez.
  - İlk yarı skoru: önce Maçkolik arşivinden (istek yok); bulunamazsa maç özetinden (1 istek).
Satırlar futbol.db'deki maclar tablosuna Ofsayt/SofaScore ile aynı adlandırmayla yazılır.
"""
import hashlib
import sqlite3
import time
from datetime import datetime, timedelta

import pandas as pd

import flashscore as fs
from db_olustur import DB, ekle, hazirla_df

SON_N = 10
TAZE_SAAT = 12


def sayisal_id(mac_id):
    """maclar tablosundaki mac_id tamsayı; Flashscore kimliğinden kararlı bir sayı (Ofsayt kimlikleriyle çakışmaz)."""
    return int(hashlib.md5(("fs:" + mac_id).encode()).hexdigest()[:15], 16)


def _tablolar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS fs_takim (takim_id TEXT PRIMARY KEY, takim TEXT, slug TEXT,
        son_cekim TEXT, sonraki_mac INTEGER)""")
    con.execute("CREATE TABLE IF NOT EXISTS fs_istatistik_yok (mac_id TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS fs_eslesme (iddaa_adi TEXT PRIMARY KEY, takim_id TEXT, takim TEXT, "
                "slug TEXT)")


def hafiza():
    """{iddaa takım adı: (Flashscore takım id, ad, slug)}"""
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        return {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT * FROM fs_eslesme")}


def hafiza_yaz(kayitlar):
    """kayitlar: [(iddaa adı, takım id, ad, slug)]"""
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        con.executemany("INSERT OR REPLACE INTO fs_eslesme VALUES (?, ?, ?, ?)", kayitlar)


# ---------------------------------------------------------------- ilk yarı skoru (Maçkolik arşivinden)
class _IlkYari:
    def __init__(self):
        import mackolik as mk
        with sqlite3.connect(DB) as con:
            mk._tablolar(con)
            self.d = pd.read_sql("SELECT ts, ev, dep, ev_skor, dep_skor, ev_iy, dep_iy FROM mk_mac "
                                 "WHERE spor = 'futbol' AND ev_iy IS NOT NULL", con)
        self.kova = {}
        for r in self.d.itertuples(index=False):
            self.kova.setdefault(int(r.ts) // 3600, []).append(r)

    def bul(self, mac):
        """Aynı saat (±20 dk), aynı skor ve en az bir takım adı benzer olan Maçkolik maçının ilk yarısı."""
        from sofascore import _isim_puani_tek
        ts = mac["ts"] or 0
        for k in (ts // 3600 - 1, ts // 3600, ts // 3600 + 1):
            for r in self.kova.get(k, []):
                if (abs(r.ts - ts) <= 1200 and r.ev_skor == mac["ev_gol"] and r.dep_skor == mac["dep_gol"]
                        and max(_isim_puani_tek(r.ev, mac["ev"] or ""), _isim_puani_tek(r.dep, mac["dep"] or "")) >= 0.6):
                    return int(r.ev_iy), int(r.dep_iy)
        return None


# ---------------------------------------------------------------- takımlar
def _mevcut():
    import ofsayt
    var = ofsayt._mevcut()[0]  # istatistiği zaten olan mac_id'ler (maclar tablosu)
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        yok = {r[0] for r in con.execute("SELECT mac_id FROM fs_istatistik_yok")}
        tablo = pd.read_sql("SELECT * FROM fs_takim", con).set_index("takim_id")
        diger = {r[0] for r in con.execute("SELECT takim FROM ofsayt_takim")} if con.execute(
            "SELECT name FROM sqlite_master WHERE name = 'ofsayt_takim'").fetchone() else set()
    return var, yok, tablo, diger


def _db_adi(tid, ad, tablo, diger):
    """Başka bir takım (Flashscore'da ya da Ofsayt'ta) aynı adı taşıyorsa ada kısa kimlik eklenir."""
    baskasi = tablo[(tablo["takim"] == ad) & (tablo.index != tid)]
    return f"{ad} #{tid[:4]}" if len(baskasi) or ad in diger else ad


def takimlari_guncelle(takimlar, yazdir=print, ilerleme=None, zorla=False):
    """takimlar: {Flashscore takım id: (ad, slug)}. Son SON_N maçı istatistikleriyle maclar tablosuna yazar.
    {takım id: veritabanındaki ad} döner."""
    import ofsayt
    var, yok, tablo, diger = _mevcut()
    simdi = datetime.now()
    adlar, yapilacak = {}, []
    for tid, (ad, slug) in takimlar.items():
        if not tid or not slug:
            continue
        if not zorla and tid in tablo.index:
            son = pd.to_datetime(tablo.loc[tid, "son_cekim"], errors="coerce")
            sonraki = tablo.loc[tid, "sonraki_mac"]
            mac_yok = pd.notna(sonraki) and time.time() < float(sonraki) + 3 * 3600
            if pd.notna(son) and (simdi - son < timedelta(hours=TAZE_SAAT) or mac_yok):
                adlar[tid] = tablo.loc[tid, "takim"]
                continue
        yapilacak.append((tid, ad, slug))
    yazdir(f"Flashscore: {len(takimlar)} takımdan {len(yapilacak)} tanesi güncellenecek "
           f"({len(takimlar) - len(yapilacak)} takım zaten güncel).")
    if not yapilacak:
        return adlar
    iy = _IlkYari()
    biriken, bos = [], []
    t0, istek0, iy_istek = time.time(), fs.istek_sayisi(), 0

    def yaz():
        nonlocal biriken, bos
        if biriken:
            ekle(hazirla_df(pd.DataFrame(biriken)))
        with sqlite3.connect(DB) as con:
            _tablolar(con)
            con.executemany("INSERT OR IGNORE INTO fs_istatistik_yok VALUES (?)", [(m,) for m in bos])
        biriken, bos = [], []

    onbellek = {}
    for n, (tid, ad, slug) in enumerate(yapilacak, 1):
        try:
            maclar, sonraki = fs.futbol_sonuclari(slug, tid)
            maclar = maclar[-SON_N:]
        except fs.FlashscoreEngel:
            yaz()
            raise
        except Exception as hata:
            yazdir(f"  Flashscore hatası ({ad}): {hata}")
            continue
        if not maclar:
            continue
        gercek = next((m["ev"] if m["ev_id"] == tid else m["dep"] for m in reversed(maclar)
                       if (m["ev"] if m["ev_id"] == tid else m["dep"])), ad)
        db_adi = _db_adi(tid, gercek, tablo, diger)
        for m in maclar:
            sid = sayisal_id(m["mac_id"])
            if sid in var or m["mac_id"] in yok:
                continue
            if m["mac_id"] not in onbellek:
                try:
                    ist = fs.futbol_istatistikleri(m["mac_id"])
                    ilk = iy.bul(m)
                    if ilk is None and ist:  # arşivde yok: maç özetinden (yalnızca istatistiği olan maçlar)
                        ilk = fs.ilk_yari_skoru(m["mac_id"])
                        iy_istek += 1
                    m["ev_iy"], m["dep_iy"] = ilk or (None, None)
                    onbellek[m["mac_id"]] = (ist, m)
                except fs.FlashscoreEngel:
                    yaz()
                    raise
                except Exception as hata:
                    yazdir(f"  Flashscore maç hatası: {hata}")
                    onbellek[m["mac_id"]] = (None, m)
            ist, m = onbellek[m["mac_id"]]
            if ist == {}:
                bos.append(m["mac_id"])
            biriken.append(ofsayt._uzun(m, ist, tid, db_adi, sid=sid))
        adlar[tid] = db_adi
        tablo.loc[tid, ["takim", "slug"]] = [db_adi, slug]
        with sqlite3.connect(DB) as con:
            _tablolar(con)
            con.execute("INSERT OR REPLACE INTO fs_takim VALUES (?, ?, ?, ?, ?)",
                        (tid, db_adi, slug, simdi.strftime("%Y-%m-%d %H:%M"), sonraki))
        if ilerleme:
            ilerleme(n, len(yapilacak))
        if n % 10 == 0 or n == len(yapilacak):
            gecen = time.time() - t0
            yazdir(f"  [{n}/{len(yapilacak)}] takım · {fs.istek_sayisi() - istek0} istek "
                   f"({iy_istek} ilk yarı) · kalan ≈ {gecen / n * (len(yapilacak) - n) / 60:.0f} dk")
        if len(biriken) >= 60:
            yaz()
    yaz()
    return adlar
