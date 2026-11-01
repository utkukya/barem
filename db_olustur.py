"""
CSV'deki maç istatistiklerini yerel SQLite veritabanına (futbol.db) yazar.

Kullanım:
    python db_olustur.py                          # klasördeki son10_takim_bazli.csv'yi kullanır
    python db_olustur.py son10_takim_bazli.csv
    python db_olustur.py yeni_veri.csv --ekle     # var olan veritabanına ekler (aynı maçlar tekrar yazılmaz)
"""
import argparse
import re
import sqlite3
from pathlib import Path

import pandas as pd

from istatistikler import SIFIRSA_BOS

KLASOR = Path(__file__).resolve().parent
DB = KLASOR / "futbol.db"


def hazirla(csv_yolu):
    return hazirla_df(pd.read_csv(csv_yolu))


def hazirla_df(df):
    """Scraper'ın takım bazlı (uzun) tablosunu veritabanı biçimine getirir."""
    # Uzatma (ET1/ET2) kolonları lig maçlarında boş
    df = df[[c for c in df.columns if not c.startswith("ET")]].copy()

    # Golleri diğer istatistiklerle aynı adlandırmaya getir: gol, IY_gol, 2Y_gol
    ek = {}
    for t in ("takim", "rakip"):
        if f"{t}_iy_gol" in df:
            ek[f"IY_{t}_gol"] = df[f"{t}_iy_gol"]
            ek[f"2Y_{t}_gol"] = df[f"{t}_gol"] - df[f"{t}_iy_gol"]
    df = pd.concat([df.drop(columns=[c for c in ("takim_iy_gol", "rakip_iy_gol") if c in df]),
                    pd.DataFrame(ek, index=df.index)], axis=1)

    # SofaScore'un göndermediği sıfırları doldur (yalnızca istatistiği olan maçlarda)
    isaret = [c for c in ("takim_passes", "takim_ballPossession", "takim_totalShotsOnGoal") if c in df]
    var = df[isaret].notna().any(axis=1) if isaret else pd.Series(False, index=df.index)
    for on in ("", "IY_", "2Y_"):
        for k in SIFIRSA_BOS:
            for t in ("takim", "rakip"):
                c = f"{on}{t}_{k}"
                if c in df:
                    df.loc[var, c] = df.loc[var, c].fillna(0)

    df["tarih"] = pd.to_datetime(df["tarih"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    return df


METIN_KOLONLARI = {"lig", "tarih", "hafta", "saha", "takim", "rakip"}


def sayiya_cevir(df):
    """İstatistik kolonlarını sayıya çevirir. Boş değer içeren yeni kolonlar pandas'ta 'object' olur ve
    SQLite'a metin olarak yazılır; okurken 'string' gelir ve ortalama alınamaz."""
    for k in df.columns:
        if k not in METIN_KOLONLARI and not pd.api.types.is_numeric_dtype(df[k]):
            df[k] = pd.to_numeric(df[k], errors="coerce")
    return df


TUTULAN_MAC = 10  # her takımın yalnızca son bu kadar maçı saklanır (veritabanı şişmesin)

# Kadın liglerindeki maçlar: takım adına " (K)" eklenir (iddaa ve Maçkolik de böyle yazar). Yoksa SofaScore gibi
# kaynaklarda "Eintracht Frankfurt" kadın takımının maçları erkek takımının maçlarına karışır.
KADIN_LIG = re.compile(r"wom[ae]n|frauen|kad[ıi]n|femen|f[ée]minin|feminil|femminil|\bwsl\b|ladies|damallsvenskan|"
                       r"\bnwsl\b|\(w\)|bayan|\bliga f\b|we[- ]?league|kobiet|kvind|kvinn|naisten|[žz]ensk|n[őo]i\b|"
                       r"vrouwen|\bdames\b|\bdamen\b|toppserien|elitettan", re.I)
KADIN_AD = re.compile(r"\(k\)|\bwomen\b|\bkad[ıi]n|\bladies\b|\bfemenin|\bféminin|\bfrauen\b|\s\(?w\)?$", re.I)


def _kadin_adi(ad):
    return ad if not isinstance(ad, str) or KADIN_AD.search(ad) else f"{ad} (K)"


def kadin_ayir(df):
    """Kadın ligi satırlarında takım ve rakip adlarına ' (K)' ekler."""
    if df.empty or "lig" not in df:
        return df
    kadin = df["lig"].fillna("").astype(str).str.contains(KADIN_LIG)
    if kadin.any():
        df = df.copy()
        for k in ("takim", "rakip"):
            if k in df:
                df.loc[kadin, k] = df.loc[kadin, k].map(_kadin_adi)
    return df


def kadinlari_ayir_db(db, tablo="maclar"):
    """Veritabanındaki eski satırlar için kadin_ayir. Değişen satır sayısını döner."""
    with sqlite3.connect(db) as con:
        if not con.execute("SELECT name FROM sqlite_master WHERE name = ?", (tablo,)).fetchone():
            return 0
        kol = [r[1] for r in con.execute(f'PRAGMA table_info("{tablo}")')]
        if "lig" not in kol or "takim" not in kol:
            return 0
        satir = con.execute(f'SELECT rowid, lig, takim, {"rakip" if "rakip" in kol else "NULL"} FROM "{tablo}"').fetchall()
        degisen = [(_kadin_adi(t), _kadin_adi(r) if r is not None else None, i) for i, lig, t, r in satir
                   if lig and KADIN_LIG.search(str(lig)) and _kadin_adi(t) != t]
        if degisen:
            if "rakip" in kol:
                con.executemany(f'UPDATE "{tablo}" SET takim = ?, rakip = ? WHERE rowid = ?', degisen)
            else:
                con.executemany(f'UPDATE "{tablo}" SET takim = ? WHERE rowid = ?', [(a, i) for a, _, i in degisen])
        return len(degisen)


def son_n_birak(df, n=TUTULAN_MAC):
    """Her takımın en yeni n maç gününün satırları (tarihe göre); daha eskiler atılır. Gün sayılır, satır değil:
    aynı maç iki kaynaktan gelmişse (analizde biri atılır) yer kaplayıp eski bir maçı sildirmesin."""
    if df.empty or "takim" not in df or "tarih" not in df:
        return df
    gun = pd.to_datetime(df["tarih"], errors="coerce").dt.normalize()
    sira = gun.groupby(df["takim"]).rank(method="dense", ascending=False)
    return df[(sira <= n) | sira.isna()]


def tabloyu_kirp(db, n=TUTULAN_MAC, tablo="maclar"):
    """Var olan takım-maç tablosunda her takımın yalnızca son n maç gününü bırakır. Silinen satır sayısını döner.
    Önce kadın ligi maçları ayrılır (erkek takımının son 10 maçında yer kaplamasınlar)."""
    kadinlari_ayir_db(db, tablo)
    with sqlite3.connect(db) as con:
        if not con.execute("SELECT name FROM sqlite_master WHERE name = ?", (tablo,)).fetchone():
            return 0
        cur = con.execute(f"""DELETE FROM "{tablo}" WHERE rowid IN (SELECT rowid FROM (
            SELECT rowid, DENSE_RANK() OVER (PARTITION BY takim ORDER BY date(tarih) DESC) AS sira FROM "{tablo}")
            WHERE sira > ?)""", (n,))
        return cur.rowcount


def sikistir(db):
    """Silinen satırlardan kalan boşluğu atar (dosya küçülür)."""
    try:
        with sqlite3.connect(db) as con:
            con.execute("VACUUM")
    except sqlite3.Error:
        pass


def ekle(yeni):
    """Hazırlanmış satırları maclar tablosuna ekler; aynı (mac_id, takim) varsa yenisi geçerli.
    Yeni istatistik kolonları gelirse tablo genişletilir."""
    with sqlite3.connect(DB) as con:
        try:
            eski = pd.read_sql("SELECT * FROM maclar", con)
        except Exception:
            eski = pd.DataFrame()
        tum = kadin_ayir(pd.concat([eski, yeni], ignore_index=True))
        tum = sayiya_cevir(tum.drop_duplicates(subset=["mac_id", "takim"], keep="last"))
        tum = son_n_birak(tum)
        tum.to_sql("maclar", con, if_exists="replace", index=False)
        con.execute("CREATE INDEX IF NOT EXISTS ix_takim ON maclar(takim, tarih)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_lig ON maclar(lig)")
    return len(tum)


def db_hazir():
    """futbol.db var ve içinde maç tablosu varsa True."""
    if not DB.exists():
        return False
    try:
        with sqlite3.connect(DB) as con:
            return con.execute("SELECT COUNT(*) FROM maclar").fetchone()[0] > 0
    except sqlite3.Error:
        return False


def csv_bul():
    """Uygulama klasöründe maç verisi CSV'sini arar (önce standart ad, sonra son10*.csv)."""
    standart = KLASOR / "son10_takim_bazli.csv"
    if standart.exists():
        return standart
    adaylar = sorted(KLASOR.glob("*son10*takim*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    adaylar = [p for p in adaylar if "duzenli" not in p.name]  # Excel için düzenlenmiş kopya değil
    return adaylar[0] if adaylar else None


def olustur(csv_yolu, ekle=False):
    yeni = hazirla(csv_yolu)
    with sqlite3.connect(DB) as con:
        if ekle:
            try:
                eski = pd.read_sql("SELECT * FROM maclar", con)
                yeni = (pd.concat([eski, yeni], ignore_index=True)
                        .drop_duplicates(subset=["mac_id", "takim"], keep="last"))
            except Exception:
                pass
        yeni.to_sql("maclar", con, if_exists="replace", index=False)
        con.execute("CREATE INDEX IF NOT EXISTS ix_takim ON maclar(takim, tarih)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_lig ON maclar(lig)")
    return yeni


def main():
    p = argparse.ArgumentParser()
    p.add_argument("csv", nargs="?", default=None)
    p.add_argument("--ekle", action="store_true", help="Veritabanını silmeden yeni maçları ekle")
    a = p.parse_args()

    csv_yolu = Path(a.csv) if a.csv else csv_bul()
    if csv_yolu is None or not csv_yolu.exists():
        raise SystemExit(f"CSV bulunamadı: {a.csv or 'son10_takim_bazli.csv'} "
                         f"(aranan klasör: {KLASOR})")
    yeni = olustur(csv_yolu, a.ekle)
    print(f"{DB}: {len(yeni)} satır, {yeni['takim'].nunique()} takım, {yeni['lig'].nunique()} lig")


if __name__ == "__main__":
    main()
