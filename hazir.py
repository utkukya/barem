"""
Ön hesaplanmış tablolar.

Sayfaların ağır tabloları (futbol barem tarayıcı, maç istatistikleri, basketbol baremleri, NBA barem tarayıcı ve
değer tablosu) her açılışta yeniden hesaplanmasın diye veritabanına yazılır: veri ve kod değişmediği sürece
hazır tablo okunur (saniyenin altında). Yerel bilgisayarda ilk görüntülemede ya da güncellemeden hemen sonra
hesaplanıp kaydedilir; Streamlit Cloud yalnızca okur.

Komut satırı (yayinla.bat bunu çalıştırır):  python hazir.py
"""
import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd

KLASOR = Path(__file__).resolve().parent
VARSAYILAN_N = 10


# ---------------------------------------------------------------- saklama
def _tablo(con):
    con.execute("CREATE TABLE IF NOT EXISTS hazir_tablo (anahtar TEXT PRIMARY KEY, damga TEXT, veri TEXT)")


def _tablo_adi(anahtar):
    return "hz_" + hashlib.md5(anahtar.encode()).hexdigest()[:12]


def oku(db, anahtar, damga):
    if not Path(db).exists():
        return None
    try:
        with sqlite3.connect(db) as con:
            _tablo(con)
            r = con.execute("SELECT damga, veri FROM hazir_tablo WHERE anahtar = ?", (anahtar,)).fetchone()
            if not r or r[0] != damga:
                return None
            df = pd.read_sql(f'SELECT * FROM "{_tablo_adi(anahtar)}"', con)
    except (sqlite3.Error, pd.errors.DatabaseError):
        return None
    try:
        meta = json.loads(r[1])
        tipler, adlar = meta["tipler"], meta["adlar"]
        df.columns = [adlar.get(c, c) for c in df.columns]
        for kol, tip in tipler.items():
            if kol not in df.columns:
                continue
            if tip.startswith("datetime64"):
                df[kol] = pd.to_datetime(df[kol], errors="coerce")
            elif tip == "bool":
                df[kol] = df[kol].astype(bool)
            elif tip.startswith(("int", "float")):
                df[kol] = pd.to_numeric(df[kol], errors="coerce")
            elif tip == "object":  # boş değerler hesaplamadaki gibi None kalsın (NaN değil)
                df[kol] = df[kol].astype(object).where(df[kol].notna(), None)
        df = df.set_index("__sira__")
        df.index.name = None
        return df
    except Exception:
        return None


def yaz(db, anahtar, damga, df):
    """Tablo veritabanına kendi tablosu olarak yazılır; sütun türleri ayrıca saklanır (ek paket gerekmez)."""
    try:
        kayit = df.copy()
        kayit.index.name = "__sira__"
        kayit = kayit.reset_index()
        tipler = {str(k): str(t) for k, t in kayit.dtypes.items()}
        for kol, tip in tipler.items():  # nesne sütunlarında karışık değerler metne çevrilir (None korunur)
            if tip == "object":
                kayit[kol] = kayit[kol].map(lambda v: v if v is None or isinstance(v, (str, int, float)) else str(v))
        # SQLite sütun adlarında büyük/küçük harf ayrımı yok ("kapsam" ile "Kapsam" çakışır): c0, c1... ile yazılır
        adlar = {f"c{i}": str(k) for i, k in enumerate(kayit.columns)}
        kayit.columns = list(adlar)
        with sqlite3.connect(db) as con:
            _tablo(con)
            kayit.to_sql(_tablo_adi(anahtar), con, if_exists="replace", index=False)
            con.execute("INSERT OR REPLACE INTO hazir_tablo VALUES (?, ?, ?)",
                        (anahtar, damga, json.dumps({"tipler": tipler, "adlar": adlar}, ensure_ascii=False)))
    except Exception as hata:  # saklanamayan tablo yalnızca bir sonraki açılışta yeniden hesaplanır
        import sys
        print(f"hazir: {anahtar} kaydedilemedi: {hata}", file=sys.stderr)


def al(db, anahtar, damga, hesapla):
    """Hazır tablo güncelse onu, değilse hesaplayıp (yerelde) kaydederek döner."""
    df = oku(db, anahtar, damga)
    if df is not None:
        return df
    df = hesapla()
    from ortam import guncelleme_acik
    if guncelleme_acik() and isinstance(df, pd.DataFrame):
        yaz(db, anahtar, damga, df)
    return df


def kod_surumu(*dosyalar):
    """Hesaplamayı yapan dosyaların içeriğinden kısa özet: kod değişince hazır tablo da yenilenir."""
    h = hashlib.md5()
    for d in dosyalar:
        try:
            h.update((KLASOR / d).read_bytes().replace(b"\r\n", b"\n"))
        except OSError:
            pass
    return h.hexdigest()[:10]


def _sorgu(db, sql, varsayilan=None):
    try:
        with sqlite3.connect(db) as con:
            return con.execute(sql).fetchone()
    except sqlite3.Error:
        return varsayilan


# ---------------------------------------------------------------- futbol
def futbol_damga():
    from iddaa_baremler import DB
    b = _sorgu(DB, "SELECT MAX(cekilme) FROM baremler")
    m = _sorgu(DB, "SELECT COUNT(*) FROM maclar")
    if not b or not b[0]:
        return ""
    return f"{b[0]}|{m[0] if m else 0}|{kod_surumu('barem.py', 'tahmin.py', 'istatistikler.py', 'mackolik.py')}"


def futbol_barem(n=VARSAYILAN_N, cekim=2.0):
    from barem import baremleri_yukle, tablo_olustur
    from iddaa_baremler import DB
    from tahmin import veri_yukle
    return al(DB, f"futbol_barem|n={n}|c={cekim}", futbol_damga(),
              lambda: tablo_olustur(veri_yukle(), baremleri_yukle(), n, cekim))


def futbol_ozet_damga():
    from tahmin import DB
    b = _sorgu(DB, "SELECT MAX(cekilme) FROM bulten")
    m = _sorgu(DB, "SELECT COUNT(*) FROM maclar")
    if not b or not b[0]:
        return ""
    return f"{b[0]}|{m[0] if m else 0}|{kod_surumu('mac_ozeti.py', 'tahmin.py', 'mackolik.py')}"


def futbol_ozet(n=VARSAYILAN_N):
    import mac_ozeti as mo
    from tahmin import DB, veri_yukle
    return al(DB, f"futbol_ozet|n={n}", futbol_ozet_damga(),
              lambda: mo.ozet_tablosu(mo.bulten_yukle(), veri_yukle(), n))


# ---------------------------------------------------------------- basketbol
def basket_damga():
    import basketbol as bk
    son = bk.son_guncelleme()
    m = _sorgu(bk.DB, "SELECT COUNT(*) FROM maclar")
    if not son:
        return ""
    return f"{son['cekilme']}|{m[0] if m else 0}|{kod_surumu('basketbol.py', 'mackolik.py')}"


def basket_tablolar(n=VARSAYILAN_N):
    """(barem tablosu, maç tablosu)."""
    import basketbol as bk
    damga = basket_damga()
    sakli = {}

    def hesapla():
        bulten, baremler, maclar = bk.verileri_yukle()
        bt = bk.barem_tablosu(bulten, baremler, maclar, n)
        sakli["mt"] = bk.mac_tablosu(bulten, bt, maclar, n)
        return bt

    bt = al(bk.DB, f"basket_barem|n={n}", damga, hesapla)
    if "mt" in sakli:
        mt = sakli["mt"]
        from ortam import guncelleme_acik
        if guncelleme_acik():
            yaz(bk.DB, f"basket_mac|n={n}", damga, mt)
    else:
        def mac_hesapla():
            bulten, _, maclar = bk.verileri_yukle()
            return bk.mac_tablosu(bulten, bt, maclar, n)
        mt = al(bk.DB, f"basket_mac|n={n}", damga, mac_hesapla)
    return bt, mt


# ---------------------------------------------------------------- NBA / EuroLeague
def _lig(lig):
    import ligler
    return ligler.modul(lig)


def nba_damga(lig="nba"):
    m = _lig(lig)
    if not m.DB.exists():
        return ""
    k = _sorgu(m.DB, "SELECT COUNT(*), COUNT(DISTINCT mac_id) FROM nba_kutu")
    b = _sorgu(m.DB, "SELECT deger FROM nba_meta WHERE anahtar = 'bulten'")
    p = _sorgu(m.DB, "SELECT MAX(cekilme), COUNT(*) FROM nba_prop")
    if not k or not k[0]:
        return ""
    return (f"{k[0]}|{k[1]}|{b[0] if b else ''}|{p[0] if p else ''}|{p[1] if p else ''}|"
            f"{kod_surumu('nba.py', 'ligler.py', 'mackolik.py')}")


def nba_tarama(kutu, n=VARSAYILAN_N, agirlik=0.5, lig="nba"):
    m = _lig(lig)
    return al(m.DB, f"nba_tarama|n={n}|a={agirlik}", nba_damga(lig),
              lambda: m.barem_tarayici(m.props_yukle(), kutu, n, agirlik))


def nba_deger(kutu, n=VARSAYILAN_N, agirlik=0.5, lig="nba"):
    m = _lig(lig)
    return al(m.DB, f"nba_deger|n={n}|a={agirlik}", nba_damga(lig),
              lambda: m.deger_tablosu(m.props_yukle(), kutu, n, agirlik))


# ---------------------------------------------------------------- hepsi
def hepsini_hazirla(yazdir=print, sporlar=("futbol", "basketbol", "nba", "euroleague")):
    """Varsayılan ayarlarla (son 10 maç) tüm ağır tabloları hesaplayıp veritabanlarına yazar."""
    import time
    for spor in sporlar:
        t0 = time.time()
        try:
            if spor == "futbol":
                if not futbol_damga():
                    continue
                t = futbol_barem()
                o = futbol_ozet() if futbol_ozet_damga() else pd.DataFrame()
                yazdir(f"Futbol: {len(t)} barem satırı, {len(o)} maç hazır ({time.time() - t0:.0f} sn).")
            elif spor == "basketbol":
                if not basket_damga():
                    continue
                bt, mt = basket_tablolar()
                yazdir(f"Basketbol: {len(bt)} barem satırı, {len(mt)} maç hazır ({time.time() - t0:.0f} sn).")
            elif spor in ("nba", "euroleague"):
                if not nba_damga(spor):
                    continue
                m = _lig(spor)
                v = m.verileri_yukle()
                if v is None or v[1].empty:
                    continue
                t = nba_tarama(v[1], lig=spor)
                d = nba_deger(v[1], lig=spor)
                yazdir(f"{m.BASLIK}: {len(t)} barem satırı, {len(d)} değer satırı hazır "
                       f"({time.time() - t0:.0f} sn).")
        except Exception as hata:
            yazdir(f"{spor}: hazır tablo oluşturulamadı ({hata}).")


if __name__ == "__main__":
    print("Hazır tablolar hesaplanıyor (yayınlanan sürüm bunları okur)…")
    hepsini_hazirla()
