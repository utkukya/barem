"""
Veri erişimi ve tahmin modeli (arayüzden bağımsız; ayrı olarak test edilebilir / kullanılabilir).

Model (sayılan istatistikler, örn. şut):
    Beklenen ev değeri  = Lig ev ort. × (Ev takımının yaptığı / lig ort.) × (Deplasmanın rakibe izin verdiği / lig ort.)
    Beklenen dep. değeri = Lig dep. ort. × (Dep. takımının yaptığı / lig ort.) × (Ev takımının rakibe izin verdiği / lig ort.)
Yani bir takımın kendi şut ortalaması, rakibin ne kadar şut yediğiyle birlikte değerlendirilir.
Az maç olduğu için takım ortalamaları lig ortalamasına doğru biraz çekilir (küçük örneklem düzeltmesi).
Olasılıklar, ligdeki saçılıma göre Poisson / negatif binom dağılımından simülasyonla hesaplanır.
Yüzde istatistiklerinde (topa sahip olma vb.) iki tarafın ortalaması alınır.
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from istatistikler import ISTATISTIKLER, TOPLAMI_100

DB = Path(__file__).resolve().parent / "futbol.db"


def veri_yukle(db=DB):
    """Takım bazlı maç verisi: Ofsayt'tan (eskiden SofaScore'dan) gelen istatistikli maçlar (maclar tablosu) + Maçkolik günlük sonuç
    arşivi (skor ve ilk yarı skoru). Aynı takımın aynı gündeki maçı iki kaynakta da varsa istatistikli olan kalır."""
    with sqlite3.connect(db) as con:
        try:
            df = pd.read_sql("SELECT * FROM maclar", con)
        except Exception:
            df = pd.DataFrame(columns=["lig", "mac_id", "tarih", "hafta", "saha", "takim", "rakip"])
    from db_olustur import sayiya_cevir
    df = sayiya_cevir(df)
    df["tarih"] = pd.to_datetime(df["tarih"])
    mk = mackolik_satirlari(db)
    if len(mk):
        df = pd.concat([df, mk], ignore_index=True)
        df["_dolu"] = df.notna().sum(axis=1)
        df["_gun"] = df["tarih"].dt.date
        df = (df.sort_values("_dolu", ascending=False)
                .drop_duplicates(subset=["takim", "_gun"], keep="first")
                .drop(columns=["_dolu", "_gun"]))
    return _turet(df).sort_values("tarih")


def mackolik_satirlari(db=DB):
    """Maçkolik arşivindeki biten futbol maçları, maclar tablosu biçiminde (gol, ilk yarı, ikinci yarı)."""
    try:
        import mackolik
        m = mackolik.takim_maclari(db, "futbol")
    except Exception:
        return pd.DataFrame()
    if m.empty:
        return pd.DataFrame()
    return pd.DataFrame({
        "lig": m["lig"], "mac_id": "mk:" + m["mac_id"].astype(str), "tarih": m["tarih"], "hafta": None,
        "saha": m["saha"], "takim": m["takim"], "rakip": m["rakip"],
        "takim_gol": m["takim_skor"], "rakip_gol": m["rakip_skor"],
        "IY_takim_gol": m["takim_iy"], "IY_rakip_gol": m["rakip_iy"],
        "2Y_takim_gol": m["takim_skor"] - m["takim_iy"], "2Y_rakip_gol": m["rakip_skor"] - m["rakip_iy"],
    })


def h2h_yukle(db=DB):
    """İki takımın kendi aralarındaki maçları (h2h tablosu; bugünkü ev sahibinin açısından), event_id ile."""
    with sqlite3.connect(db) as con:
        try:
            df = pd.read_sql("SELECT * FROM h2h", con)
        except Exception:
            return pd.DataFrame(columns=["event_id", "tarih", "takim", "rakip"])
    from db_olustur import sayiya_cevir
    df = sayiya_cevir(df.drop(columns=["event_id"])).assign(event_id=df["event_id"])
    df["tarih"] = pd.to_datetime(df["tarih"])
    return _turet(df).sort_values("tarih")


def _turet(df):
    # Türetilmiş: toplam kart = sarı + kırmızı (iddaa "Toplam Kart" pazarları için)
    ek = {}
    for on in ("", "IY_", "2Y_"):
        for t in ("takim", "rakip"):
            s, k = f"{on}{t}_yellowCards", f"{on}{t}_redCards"
            if s in df:
                ek[f"{on}{t}_kart"] = df[s] + (df[k].fillna(0) if k in df else 0)
    return pd.concat([df, pd.DataFrame(ek, index=df.index)], axis=1)


def ustunde_olasilik(mu, sacilim, cizgi):
    """Sayılan bir istatistiğin çizgiyi geçme olasılığı: P(X > cizgi).
    sacilim <= 1.05 -> Poisson, üstü -> negatif binom (varyans = sacilim × ortalama)."""
    import math
    mu = max(float(mu), 1e-9)
    k = int(math.floor(cizgi))
    if k < 0:
        return 1.0
    if sacilim <= 1.05:
        terim = math.exp(-mu)
        top = terim
        for i in range(1, k + 1):
            terim *= mu / i
            top += terim
    else:
        r = mu / (sacilim - 1)
        p = r / (r + mu)
        lp, l1p = math.log(p), math.log1p(-p)
        top = sum(math.exp(math.lgamma(i + r) - math.lgamma(r) - math.lgamma(i + 1) + r * lp + i * l1p)
                  for i in range(k + 1))
    return float(min(max(1 - top, 0.0), 1.0))


def kolonlar(anahtar, periyot=""):
    return f"{periyot}takim_{anahtar}", f"{periyot}rakip_{anahtar}"


def mevcut_istatistikler(df, periyot=""):
    """Veritabanında bu periyot için verisi olan istatistikler."""
    return [k for k in ISTATISTIKLER if kolonlar(k, periyot)[0] in df.columns
            and df[kolonlar(k, periyot)[0]].notna().any()]


_onbellek = {"anahtar": None, "gruplar": {}, "sonuc": {}}


def _takim_gruplari(df):
    """Takım -> o takımın satırları (aynı df için bir kez hesaplanır; yüzlerce maçlık bültende büyük hız farkı)."""
    a = (id(df), len(df), len(df.columns))
    if _onbellek["anahtar"] != a:
        _onbellek.update(anahtar=a, gruplar=dict(tuple(df.groupby("takim", sort=False))), sonuc={})
    return _onbellek["gruplar"]


def son_maclar(df, takim, n=10, saha=None, anahtar=None, periyot=""):
    """Takımın son n maçı. saha='Ev' / 'Deplasman' ile filtrelenebilir.
    anahtar verilirse o istatistiği boş olan maçlar atlanır."""
    gruplar = _takim_gruplari(df)
    k = (takim, n, saha, anahtar, periyot)
    if k in _onbellek["sonuc"]:
        return _onbellek["sonuc"][k]
    d = gruplar.get(takim)
    if d is None:
        d = df.iloc[0:0]
    if saha:
        d = d[d["saha"] == saha]
    if anahtar:
        kol = list(kolonlar(anahtar, periyot))
        if all(c in d.columns for c in kol):
            d = d[d[kol].notna().all(axis=1)]
        else:
            d = d.iloc[0:0]
    sonuc = d.tail(n)
    _onbellek["sonuc"][k] = sonuc
    return sonuc


MIN_LIG_SATIR = 40  # bu kadar veri yoksa lig ortalaması güvenilmez -> tüm veri kullanılır


def ana_lig(df, takim):
    """Takımın en çok maç oynadığı lig (kupa maçları karışık olduğunda son maçın ligi yanıltıcı)."""
    d = df.loc[df["takim"] == takim, "lig"]
    return d.mode().iloc[0] if len(d) else None


def lig_istatistikleri(df, lig, anahtar, periyot=""):
    _takim_gruplari(df)
    k = ("lig", lig, anahtar, periyot)
    if k not in _onbellek["sonuc"]:
        _onbellek["sonuc"][k] = _lig_istatistikleri(df, lig, anahtar, periyot)
    return _onbellek["sonuc"][k]


def _lig_istatistikleri(df, lig, anahtar, periyot=""):
    """Lig genel, ev ve deplasman ortalaması + takım içi saçılım oranı (varyans / ortalama).
    Ligde yeterli veri yoksa (az takımı çekilmiş bir lig) tüm veritabanının ortalaması kullanılır."""
    yap, _ = kolonlar(anahtar, periyot)
    d = df[(df["lig"] == lig)].dropna(subset=[yap])
    if len(d) < MIN_LIG_SATIR:
        d = df.dropna(subset=[yap])
    genel = d[yap].mean()
    ev = d.loc[d["saha"] == "Ev", yap].mean()
    dep = d.loc[d["saha"] == "Deplasman", yap].mean()
    # Takım içi saçılım: takımlar arası farkı saymadan, aynı takımın maçtan maça oynaması
    g = d.groupby("takim")[yap]
    ic_var = g.var().mean()
    sacilim = float(np.clip(ic_var / genel, 1.0, 60.0)) if genel and genel > 0 else 1.0
    return {"genel": genel, "ev": ev if pd.notna(ev) else genel,
            "dep": dep if pd.notna(dep) else genel, "sacilim": sacilim}


def _cek(deger, n, lig_ort, k):
    """Küçük örneklem düzeltmesi: n maçlık ortalamayı lig ortalamasına doğru çeker."""
    if k <= 0 or n == 0:
        return deger if n else lig_ort
    return (n * deger + k * lig_ort) / (n + k)


def tahmin_et(df, ev_takim, dep_takim, anahtar, periyot="", n=10,
              saha_ayrimi=False, cekim=2.0, simulasyon=200_000, tohum=42):
    if simulasyon:
        return _tahmin_et(df, ev_takim, dep_takim, anahtar, periyot, n, saha_ayrimi, cekim, simulasyon, tohum)
    _takim_gruplari(df)
    k = ("tahmin", ev_takim, dep_takim, anahtar, periyot, n, saha_ayrimi, cekim)
    if k not in _onbellek["sonuc"]:
        _onbellek["sonuc"][k] = _tahmin_et(df, ev_takim, dep_takim, anahtar, periyot, n, saha_ayrimi, cekim, 0, tohum)
    return _onbellek["sonuc"][k]


def _tahmin_et(df, ev_takim, dep_takim, anahtar, periyot="", n=10,
               saha_ayrimi=False, cekim=2.0, simulasyon=200_000, tohum=42):
    yap, yen = kolonlar(anahtar, periyot)
    tur = ISTATISTIKLER[anahtar][1]

    ev_m = son_maclar(df, ev_takim, n, "Ev" if saha_ayrimi else None, anahtar, periyot)
    dep_m = son_maclar(df, dep_takim, n, "Deplasman" if saha_ayrimi else None, anahtar, periyot)
    if ev_m.empty or dep_m.empty:
        return None

    lig = ana_lig(df, ev_takim) or ev_m["lig"].iloc[-1]
    L = lig_istatistikleri(df, lig, anahtar, periyot)

    ozet = {}
    for ad, m in (("ev", ev_m), ("dep", dep_m)):
        ozet[ad] = {
            "n": len(m),
            "yaptigi": m[yap].mean(),
            "yedigi": m[yen].mean(),
            "yaptigi_duz": _cek(m[yap].mean(), len(m), L["genel"], cekim),
            "yedigi_duz": _cek(m[yen].mean(), len(m), L["genel"], cekim),
        }
    e, d = ozet["ev"], ozet["dep"]

    if tur == "oran":
        bek_ev = (e["yaptigi_duz"] + d["yedigi_duz"]) / 2
        bek_dep = (d["yaptigi_duz"] + e["yedigi_duz"]) / 2
        if anahtar in TOPLAMI_100 and bek_ev + bek_dep > 0:
            t = bek_ev + bek_dep
            bek_ev, bek_dep = 100 * bek_ev / t, 100 * bek_dep / t
    elif L["genel"] and L["genel"] > 0:
        bek_ev = L["ev"] * (e["yaptigi_duz"] / L["genel"]) * (d["yedigi_duz"] / L["genel"])
        bek_dep = L["dep"] * (d["yaptigi_duz"] / L["genel"]) * (e["yedigi_duz"] / L["genel"])
    else:
        bek_ev = (e["yaptigi_duz"] + d["yedigi_duz"]) / 2
        bek_dep = (d["yaptigi_duz"] + e["yedigi_duz"]) / 2

    sonuc = {"lig": lig, "lig_ist": L, "ozet": ozet, "tur": tur,
             "bek_ev": bek_ev, "bek_dep": bek_dep, "ev_maclar": ev_m, "dep_maclar": dep_m}
    if tur == "sayac" and simulasyon:
        sonuc.update(olasiliklar(bek_ev, bek_dep, L["sacilim"], simulasyon, tohum))
    return sonuc


def _ornekle(rng, mu, sacilim, boyut):
    mu = max(mu, 1e-9)
    if sacilim <= 1.05:
        return rng.poisson(mu, boyut)
    r = mu / (sacilim - 1)            # varyans = sacilim × ortalama
    return rng.negative_binomial(r, r / (r + mu), boyut)


def _cizgiler(mu, sd):
    adim = max(1, int(round(sd / 2)))
    merkez = int(round(mu))
    return sorted({max(0.5, merkez + adim * j - 0.5) for j in range(-2, 3)})


def olasiliklar(bek_ev, bek_dep, sacilim, boyut=200_000, tohum=42):
    rng = np.random.default_rng(tohum)
    x = _ornekle(rng, bek_ev, sacilim, boyut)
    y = _ornekle(rng, bek_dep, sacilim, boyut)
    top = x + y

    def tablo(s):
        return pd.DataFrame([{"Çizgi": c, "Üst": (s > c).mean(), "Alt": (s < c).mean()}
                             for c in _cizgiler(s.mean(), s.std())])

    skor = (pd.Series(list(zip(x, y))).value_counts(normalize=True).head(6)
            if bek_ev + bek_dep < 8 else None)
    return {
        "p_ev_fazla": (x > y).mean(), "p_esit": (x == y).mean(), "p_dep_fazla": (x < y).mean(),
        "alt_ust_ev": tablo(x), "alt_ust_dep": tablo(y), "alt_ust_toplam": tablo(top),
        "bek_toplam": bek_ev + bek_dep, "olasi_skorlar": skor,
        "aralik_toplam": (np.percentile(top, 10), np.percentile(top, 90)),
    }
