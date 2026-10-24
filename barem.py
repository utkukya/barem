"""
Barem isabet oranları: iddaa baremlerini takımların son N maçıyla karşılaştırır.

Her barem ve yön (Alt / Üst) için:
  - Form %       : takımın son N maçında bu baremi geçme (Üst) / altında kalma (Alt) oranı
  - Rakip form % : rakibin son N maçında rakiplerine bu baremi geçirme oranı ("kullandırtma")
                   (maç toplamı pazarlarında: rakibin kendi maçlarının toplamı)
  - Model %      : tahmin modelinin (takım gücü × rakip zaafı) olasılığı
  - Adil %       : iddaa oranından marj ayıklanmış olasılık
  - Değer        : Model % × oran - 1 (pozitifse oran, modele göre değerli)
"""
import sqlite3

import numpy as np
import pandas as pd

from istatistikler import ISTATISTIKLER
from tahmin import DB, kolonlar, son_maclar, tahmin_et, ustunde_olasilik

# Hesaplama mantığı değiştiğinde artır: arayüzün önbelleği eski sonuçları göstermesin
SURUM = 6

PAZAR_ETIKET = {
    "gol": "Gol", "totalShotsOnGoal": "Şut", "shotsOnGoal": "İsabetli Şut", "cornerKicks": "Korner",
    "kart": "Kart", "yellowCards": "Sarı Kart", "fouls": "Faul", "throwIns": "Taç", "goalKicks": "Aut",
    "offsides": "Ofsayt", "goalkeeperSaves": "Kurtarış",
}
PERIYOT_ETIKET = {"": "Maç", "IY_": "İlk yarı", "2Y_": "İkinci yarı"}
KAPSAM_ETIKET = {"ev": "Takım toplamı", "dep": "Takım toplamı", "toplam": "Maç toplamı"}


def baremleri_yukle(db=DB):
    with sqlite3.connect(db) as con:
        try:
            b = pd.read_sql("SELECT * FROM baremler", con)
        except Exception:
            return pd.DataFrame()
    if not b.empty:
        b["tarih"] = pd.to_datetime(b["tarih"])
    return b


def seriler(df, takim, stat, periyot, n, kapsam):
    """(takımın değerleri, rakiplerinin değerleri, maç tablosu) — son n maç."""
    m = son_maclar(df, takim, n, None, stat, periyot)
    yap, yen = kolonlar(stat, periyot)
    if kapsam == "toplam":
        return m[yap] + m[yen], m, (yap, yen)
    return m[yap], m, (yap, yen)


def izin_serisi(df, takim, stat, periyot, n, kapsam):
    """Rakip formu: takımın rakiplerine izin verdiği (takım toplamı) ya da maç toplamları."""
    m = son_maclar(df, takim, n, None, stat, periyot)
    yap, yen = kolonlar(stat, periyot)
    return (m[yap] + m[yen]) if kapsam == "toplam" else m[yen]


def oran_hesapla(s, cizgi, yon):
    if len(s) == 0:
        return np.nan
    return float((s > cizgi).mean() if yon == "Üst" else (s < cizgi).mean())


# iddaa'nın Alt/Üst baremi açmadığı istatistikler: baremleri verimizden üretilir (oran yok).
# iddaa aynı maçta bu pazarı açmışsa (ör. ofsayt aralık pazarı) o barem kullanılır, sistem baremi eklenmez.
SISTEM_STATLARI = ["totalShotsOnGoal", "shotsOnGoal", "throwIns", "fouls", "goalKicks", "offsides",
                   "goalkeeperSaves"]
SISTEM_PERIYOTLARI = ["", "IY_"]


def _cizgiler(mu):
    """Beklenen değerin çevresinde 3 barem: en yakın x.5 ve iki yanı (adım ≈ √μ)."""
    if pd.isna(mu) or mu <= 0:
        return []
    merkez = np.floor(mu) + 0.5
    adim = max(1.0, float(np.round(np.sqrt(mu) * 0.8)))
    return [c for c in (merkez - adim, merkez, merkez + adim) if c > 0]


def sistem_baremleri(df, baremler, n=10, cekim=2.0, statlar=SISTEM_STATLARI, periyotlar=SISTEM_PERIYOTLARI):
    """iddaa'daki her maç için şut, isabetli şut, taç, faul, aut, ofsayt ve kaleci kurtarışında
    kendi baremlerimizi üretir.
    Barem merkezi: iki takım da veritabanındaysa modelin beklentisi, değilse takımın kendi ortalaması."""
    if baremler.empty:
        return pd.DataFrame()
    bilinen = set(df["takim"])
    maclar = baremler.drop_duplicates("event_id")
    zaten = set(zip(baremler["event_id"], baremler["stat"], baremler["periyot"], baremler["kapsam"]))
    satirlar = []
    for r in maclar.itertuples(index=False):
        ev = r.ev if isinstance(r.ev, str) and r.ev in bilinen else None
        dep = r.dep if isinstance(r.dep, str) and r.dep in bilinen else None
        if not (ev or dep):
            continue
        for stat in statlar:
            for periyot in periyotlar:
                if kolonlar(stat, periyot)[0] not in df.columns:
                    continue
                mdl = (tahmin_et(df, ev, dep, stat, periyot, n, False, cekim, simulasyon=0)
                       if ev and dep else None)
                for kapsam in ("ev", "dep", "toplam"):
                    if (r.event_id, stat, periyot, kapsam) in zaten:
                        continue  # iddaa bu pazarı açmışsa onu kullan
                    takim = ev if kapsam == "ev" else dep if kapsam == "dep" else (ev or dep)
                    if takim is None:
                        continue
                    if mdl is not None:
                        mu = {"ev": mdl["bek_ev"], "dep": mdl["bek_dep"]}.get(
                            kapsam, mdl["bek_ev"] + mdl["bek_dep"])
                    else:
                        s_, _, _ = seriler(df, takim, stat, periyot, n, kapsam)
                        mu = s_.mean() if len(s_) else np.nan
                    for c in _cizgiler(mu):
                        satirlar.append({"event_id": r.event_id, "lig": r.lig, "tarih": r.tarih,
                                         "ev_iddaa": r.ev_iddaa, "dep_iddaa": r.dep_iddaa,
                                         "ev": r.ev, "dep": r.dep,
                                         "pazar_adi": "Sistem baremi (iddaa'da bu pazar yok)", "stat": stat,
                                         "periyot": periyot, "kapsam": kapsam, "cizgi": float(c),
                                         "alt_oran": np.nan, "ust_oran": np.nan, "cekilme": r.cekilme})
    return pd.DataFrame(satirlar)


def h2h_serisi(h2h_g, event_id, stat, periyot, kapsam):
    """Kendi aralarındaki maçlarda (en fazla son 10) barem değerleri: ev sahibinin, deplasmanın ya da maç toplamı."""
    m = h2h_g.get(event_id)
    yap, yen = kolonlar(stat, periyot)
    if m is None or yap not in m.columns or yen not in m.columns:
        return pd.Series(dtype=float)
    m = m.dropna(subset=[yap, yen])
    return {"ev": m[yap], "dep": m[yen]}.get(kapsam, m[yap] + m[yen]).astype(float)


def tablo_olustur(df, baremler, n=10, cekim=2.0, sistem=True, h2h=None):
    """Her barem satırını Alt ve Üst olarak iki satıra açar ve oranları hesaplar.
    sistem=True: iddaa'nın açmadığı şut / isabetli şut / taç / faul için kendi baremlerini de ekler."""
    if baremler.empty:
        return pd.DataFrame()
    baremler = baremler.assign(kaynak="iddaa")
    if sistem:
        ek = sistem_baremleri(df, baremler, n, cekim)
        if not ek.empty:
            baremler = pd.concat([baremler, ek.assign(kaynak="Sistem")], ignore_index=True)
    model_onbellek = {}
    h2h_g = {} if h2h is None or h2h.empty else {k: v.tail(10) for k, v in h2h.groupby("event_id", sort=False)}
    h2h_onbellek = {}
    satirlar = []
    b = baremler[baremler["stat"].isin(ISTATISTIKLER)]
    bilinen = set(df["takim"])
    for r in b.itertuples(index=False):
        ev = r.ev if isinstance(r.ev, str) and r.ev in bilinen else None
        dep = r.dep if isinstance(r.dep, str) and r.dep in bilinen else None
        ev_ad, dep_ad = ev or r.ev_iddaa, dep or r.dep_iddaa
        if r.kapsam == "dep":
            takim, rakip, takim_ad, rakip_ad = dep, ev, dep_ad, ev_ad
        elif r.kapsam == "ev":
            takim, rakip, takim_ad, rakip_ad = ev, dep, ev_ad, dep_ad
        else:  # maç toplamı: veritabanındaki takımın maçlarına bak (ikisi de varsa ev sahibi)
            takim, rakip = (ev, dep) if ev else (dep, ev)
            takim_ad, rakip_ad = (ev_ad, dep_ad) if ev else (dep_ad, ev_ad)
        if takim is None:
            continue
        mdl = None
        if ev and dep:
            anahtar_ = (ev, dep, r.stat, r.periyot)
            if anahtar_ not in model_onbellek:
                model_onbellek[anahtar_] = tahmin_et(df, ev, dep, r.stat, r.periyot, n,
                                                     False, cekim, simulasyon=0)
            mdl = model_onbellek[anahtar_]

        kendi, _, _ = seriler(df, takim, r.stat, r.periyot, n, r.kapsam)
        izin = izin_serisi(df, rakip, r.stat, r.periyot, n, r.kapsam) if rakip else pd.Series(dtype=float)
        if len(kendi) == 0:
            continue
        hk = (r.event_id, r.stat, r.periyot, r.kapsam)
        if hk not in h2h_onbellek:
            h2h_onbellek[hk] = h2h_serisi(h2h_g, r.event_id, r.stat, r.periyot, r.kapsam)
        hs = h2h_onbellek[hk]

        if mdl is not None:
            mu = {"ev": mdl["bek_ev"], "dep": mdl["bek_dep"]}.get(r.kapsam, mdl["bek_ev"] + mdl["bek_dep"])
            p_ust = ustunde_olasilik(mu, mdl["lig_ist"]["sacilim"], r.cizgi)
        else:
            mu, p_ust = np.nan, np.nan

        alt_o = pd.to_numeric(r.alt_oran, errors="coerce")
        ust_o = pd.to_numeric(r.ust_oran, errors="coerce")
        if pd.notna(alt_o) and pd.notna(ust_o):
            ters_alt, ters_ust = 1 / alt_o, 1 / ust_o
            adil_ust = ters_ust / (ters_alt + ters_ust)
            adil_alt = 1 - adil_ust
        else:
            # Tek taraflı (eşik pazarı "N+": yalnızca Üst) ya da sistem baremi: marj ayıklanamaz,
            # oranın ima ettiği olasılık 1/oran olarak verilir
            adil_ust = 1 / ust_o if pd.notna(ust_o) else np.nan
            adil_alt = 1 / alt_o if pd.notna(alt_o) else np.nan

        for yon, oran, model_p, adil in (("Üst", ust_o, p_ust, adil_ust),
                                         ("Alt", alt_o, 1 - p_ust if pd.notna(p_ust) else np.nan, adil_alt)):
            if r.kaynak == "iddaa" and pd.isna(oran):
                continue  # iddaa bu tarafı satmıyor (ör. "Toplam 31+" pazarında Alt yok)
            satirlar.append({
                "event_id": r.event_id, "Lig": r.lig, "Tarih": r.tarih,
                "Takım": takim_ad,
                "Rakip": rakip_ad,
                "rakip_db": rakip,
                "Maç": f"{ev_ad} – {dep_ad}",
                "Ev": ev_ad, "Dep": dep_ad, "ev_db": ev, "dep_db": dep,
                "Pazar": PAZAR_ETIKET.get(r.stat, r.stat),
                "stat": r.stat, "periyot": r.periyot, "kapsam": r.kapsam,
                "Kapsam": KAPSAM_ETIKET[r.kapsam],
                "Periyot": PERIYOT_ETIKET.get(r.periyot, r.periyot),
                "Yön": yon, "Çizgi": r.cizgi,
                "Barem": f"{'Ü' if yon == 'Üst' else 'A'} {r.cizgi:g}",
                "Oran": oran,
                "Form": oran_hesapla(kendi, r.cizgi, yon),
                "Rakip form": oran_hesapla(izin, r.cizgi, yon),
                "Maç sayısı": len(kendi),
                "Ort.": float(kendi.mean()),
                "Rakip izin ort.": float(izin.mean()) if len(izin) else np.nan,
                "Rakip maç sayısı": len(izin),
                "H2H": oran_hesapla(hs, r.cizgi, yon), "H2H n": len(hs),
                "H2H ort.": float(hs.mean()) if len(hs) else np.nan,
                "Beklenen": mu,
                "Model": model_p,
                "Adil": adil,
                "Değer": model_p * oran - 1 if pd.notna(model_p) else np.nan,
                "Model oranı": 1 / model_p if pd.notna(model_p) and model_p > 0 else np.nan,
                "Kaynak": r.kaynak,
                "iddaa pazarı": r.pazar_adi,
            })
    t = pd.DataFrame(satirlar)
    if not t.empty:
        sayisal = ["Oran", "Form", "Rakip form", "Ort.", "Rakip izin ort.", "Beklenen", "Model", "Adil", "Değer",
                   "Model oranı", "H2H", "H2H ort."]
        t[sayisal] = t[sayisal].apply(pd.to_numeric, errors="coerce")
        for k in ("ev_db", "dep_db", "rakip_db"):  # eksik takım her zaman None olsun (NaN değil)
            t[k] = t[k].astype(object).where(t[k].notna(), None)
        t["Birleşik"] = (t["Form"] + t["Rakip form"]) / 2
    return t
