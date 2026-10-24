"""
CSV'deki maç istatistiklerini yerel SQLite veritabanına (futbol.db) yazar.

Kullanım:
    python db_olustur.py                          # klasördeki son10_takim_bazli.csv'yi kullanır
    python db_olustur.py son10_takim_bazli.csv
    python db_olustur.py yeni_veri.csv --ekle     # var olan veritabanına ekler (aynı maçlar tekrar yazılmaz)
"""
import argparse
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


def ekle(yeni):
    """Hazırlanmış satırları maclar tablosuna ekler; aynı (mac_id, takim) varsa yenisi geçerli.
    Yeni istatistik kolonları gelirse tablo genişletilir."""
    with sqlite3.connect(DB) as con:
        try:
            eski = pd.read_sql("SELECT * FROM maclar", con)
        except Exception:
            eski = pd.DataFrame()
        tum = pd.concat([eski, yeni], ignore_index=True)
        tum = sayiya_cevir(tum.drop_duplicates(subset=["mac_id", "takim"], keep="last"))
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
