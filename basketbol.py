"""
Basketbol: iddaa'da açık basketbol maçlarının oranları ve Alt/Üst baremleri + takımların son maçları (Maçkolik).
Veriler futbol veritabanından ayrı tutulur: basketbol.db

Kullanım:
    python basketbol.py                  # bugünkü tüm iddaa basketbol maçları
    python basketbol.py --gun 2          # önümüzdeki 2 gün
    python basketbol.py --lig 2170,2176  # yalnızca bu iddaa yarışmaları
    python basketbol.py --ligleri-listele

Baremler (hepsi uzatmalar dahil, iddaa ile aynı): maç toplam sayı, ev sahibi / deplasman takım sayısı,
1. yarı toplam ve takım sayıları. Takım verisi: Maçkolik günlük sonuç arşivinden son 10 maçın skoru ve ilk yarı
skoru (iddaa maçları Maçkolik'teki iddaa koduyla birebir eşleşir).
"""
import argparse
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import iddaa_baremler as ib

KLASOR = Path(__file__).resolve().parent
DB = KLASOR / "basketbol.db"
SPOR_IDDAA = 2          # iddaa spor kimliği (st / si)
SON_N = 10

# iddaa pazar kimliği (t_st) -> (kapsam, periyot)
PAZARLAR = {"2_114": ("toplam", ""), "2_619": ("ev", ""), "2_620": ("dep", ""),
            "2_116": ("toplam", "IY_"), "2_746": ("ev", "IY_"), "2_747": ("dep", "IY_")}
KAPSAM_ETIKET = {"toplam": "Maç toplamı", "ev": "Takım toplamı", "dep": "Takım toplamı"}
PERIYOT_ETIKET = {"": "Maç", "IY_": "İlk yarı"}


# ================================================================ iddaa
def yarismalar():
    try:
        veri = ib.get("competitions")
    except Exception:
        return {}
    liste = veri if isinstance(veri, list) else veri.get("competitions", veri.get("c", []))
    return {c["i"]: (c.get("n", str(c["i"])), c.get("cid"), ib._ulke_adi(c)) for c in liste
            if isinstance(c, dict) and str(c.get("si")) == str(SPOR_IDDAA)}


def olaylar_al():
    return ib.get(f"events?st={SPOR_IDDAA}&type=0&version=0").get("events", [])


def lig_ozeti(gun=0):
    """Seçilen dönemde iddaa'da maçı olan basketbol yarışmaları (arayüzdeki lig seçimi için)."""
    olaylar, adlar = olaylar_al(), yarismalar()
    simdi, sinir = (t.timestamp() for t in ib.zaman_araligi(gun))
    sayac = {}
    for e in olaylar:
        if simdi < e.get("d", 0) <= sinir and not ib.kupa_mi(adlar.get(e.get("ci"), ("",))[0]):
            sayac[e.get("ci")] = sayac.get(e.get("ci"), 0) + 1
    d = pd.DataFrame([{"ci": ci, "ad": adlar.get(ci, (f"Yarışma {ci}",))[0],
                       "ulke": adlar.get(ci, (None, None))[1],
                       "ulke_adi": adlar.get(ci, (None, None, "Diğer"))[2] or "Diğer", "mac": n}
                      for ci, n in sayac.items()])
    if not len(d):
        return d
    d["ulke_mac"] = d.groupby("ulke_adi")["mac"].transform("sum")
    return d.sort_values(["ulke_mac", "ulke_adi", "mac"], ascending=[False, True, False])


def _pazar_sinifi(anahtar, ad):
    """Pazar kimliği ya da adından (kapsam, periyot); Alt/Üst sayı pazarı değilse None."""
    if anahtar in PAZARLAR:
        return PAZARLAR[anahtar]
    if not ad:
        return None
    a = f" {ib.tr_kucuk(ad)} "
    if not ("alt/üst" in a or "altı/üstü" in a):
        return None
    if any(x in a for x in ("çeyrek", "maç sonucu", "en yüksek", "en düşük", "oyuncu", "handikap", " ve ")):
        return None
    periyot = "IY_" if ("1. yarı" in a or "ilk yarı" in a) else ""
    if "2. yarı" in a:
        return None
    kapsam = "ev" if "ev sahibi" in a else "dep" if "deplasman" in a else "toplam"
    return kapsam, periyot


def baremleri_cikar(olay, pazar_adlari):
    """Maç ayrıntısından barem satırları ve maç sonucu oranları."""
    satirlar, oranlar = [], {}
    for m in olay.get("m", []) or []:
        anahtar = f"{m.get('t')}_{m.get('st')}"
        secenek = {o.get("n"): ib._oran(o.get("odd")) for o in m.get("o", [])}
        if anahtar == "1_2":
            oranlar.update(ms1=secenek.get("1"), ms2=secenek.get("2"))
            continue
        if anahtar == "2_621":
            oranlar.update(iy1=secenek.get("1"), iy0=secenek.get("0"), iy2=secenek.get("2"))
            continue
        if set(secenek) != {"Alt", "Üst"} or m.get("sov") is None:
            continue
        sinif = _pazar_sinifi(anahtar, pazar_adlari.get(anahtar))
        if not sinif:
            continue
        try:
            cizgi = float(str(m["sov"]).replace(",", "."))
        except ValueError:
            continue
        kapsam, periyot = sinif
        satirlar.append({"kapsam": kapsam, "periyot": periyot, "cizgi": cizgi,
                         "alt_oran": secenek["Alt"], "ust_oran": secenek["Üst"],
                         "pazar_adi": (pazar_adlari.get(anahtar) or "Alt/Üst").replace("{0}", str(m["sov"])).strip()})
    # aynı barem iki kez gelirse ilki
    gorulen, temiz = set(), []
    for r in satirlar:
        k = (r["kapsam"], r["periyot"], r["cizgi"])
        if k not in gorulen:
            gorulen.add(k)
            temiz.append(r)
    return temiz, oranlar


# ================================================================ veritabanı
BULTEN_KOLONLAR = ["event_id", "lig", "ulke", "tarih", "ev_iddaa", "dep_iddaa", "ev", "dep", "ev_id", "dep_id",
                   "mk_id", "ms1", "ms2", "iy1", "iy0", "iy2", "cekilme"]
BAREM_KOLONLAR = ["event_id", "kapsam", "periyot", "cizgi", "alt_oran", "ust_oran", "pazar_adi"]


def guncelle(gun=0, ligler="hepsi", yazdir=print, takim_verisi=True, ilerleme=None):
    """iddaa basketbol bültenini ve baremlerini çeker; takımların son maçları Maçkolik arşivinden gelir.
    (maç, barem) sayısı döner."""
    import mackolik as mk
    cfg = ib.get("get_market_config").get("m", {})
    pazar_adlari = {k: v.get("n") for k, v in cfg.items() if isinstance(v, dict)}
    olaylar, adlar = olaylar_al(), yarismalar()
    simdi, sinir = ib.zaman_araligi(gun)
    secili = [e for e in olaylar if simdi.timestamp() < e.get("d", 0) <= sinir.timestamp()]
    if ligler != "hepsi" and ligler:
        kume = {int(c) for c in ligler}
        secili = [e for e in secili if e.get("ci") in kume]
    secili = ib.kupalari_cikar(secili, adlar, yazdir)
    secili.sort(key=lambda e: e.get("d", 0))
    donem = "bugün" if gun <= 0 else f"önümüzdeki {gun} günde"
    yazdir(f"iddaa'da {len(olaylar)} basketbol maçı açık; {donem} seçili liglerden {len(secili)} maç taranacak.")
    cekilme = simdi.strftime("%Y-%m-%d %H:%M")
    detaylar = ib._detaylari_cek(secili, yazdir) if secili else {}

    eslesme = {}
    if secili and takim_verisi:
        try:
            mk.arsiv_guncelle(DB, "basketbol", yazdir=yazdir)
            eslesme = mk.iddaa_eslesme("basketbol", secili, yazdir)
            yazdir(f"{len(eslesme)}/{len(secili)} maç Maçkolik'te iddaa koduyla eşleşti "
                   f"(Maçkolik'e {mk.istek_sayisi()} istek).")
        except mk.MackolikEngel as hata:
            yazdir(f"⚠ {hata}")
        except Exception as hata:
            yazdir(f"Maçkolik'e bağlanılamadı, takım verisi güncellenmedi: {hata}")
    adlar_mk = mk.takim_adlari(DB, "basketbol")

    bulten, baremler = [], []
    for e in secili:
        bs, oranlar = baremleri_cikar(detaylar.get(e["i"]) or e, pazar_adlari)
        s = eslesme.get(e["i"])
        ci = e.get("ci")
        bulten.append({"event_id": e["i"], "lig": adlar.get(ci, (f"Yarışma {ci}",))[0],
                       "ulke": adlar.get(ci, (None, None))[1],
                       "tarih": datetime.fromtimestamp(e["d"], ib.TZ).strftime("%Y-%m-%d %H:%M"),
                       "ev_iddaa": e.get("hn"), "dep_iddaa": e.get("an"),
                       "ev": adlar_mk.get(s["ev_id"], s["ev"]) if s else None,
                       "dep": adlar_mk.get(s["dep_id"], s["dep"]) if s else None,
                       "ev_id": s["ev_id"] if s else None, "dep_id": s["dep_id"] if s else None,
                       "mk_id": s["mk_id"] if s else None, **oranlar, "cekilme": cekilme})
        baremler.extend({"event_id": e["i"], **b} for b in bs)

    with sqlite3.connect(DB) as con:
        pd.DataFrame(bulten, columns=BULTEN_KOLONLAR).to_sql("bulten", con, if_exists="replace", index=False)
        pd.DataFrame(baremler, columns=BAREM_KOLONLAR).to_sql("baremler", con, if_exists="replace", index=False)
        con.execute("DROP TABLE IF EXISTS meta")
        con.execute("CREATE TABLE meta (cekilme, gun, mac)")
        con.execute("INSERT INTO meta VALUES (?, ?, ?)", (cekilme, gun, len(bulten)))
    eksik = [f"{b['lig']}: {b['ev_iddaa']} – {b['dep_iddaa']}" for b in bulten if b["ev_id"] is None]
    if eksik and eslesme:
        yazdir(f"Maçkolik'te bulunamayan {len(eksik)} maç (geçmiş verisi yok):\n  " + "\n  ".join(eksik[:30]))
    yazdir(f"Bitti: {len(bulten)} maç, {len(baremler)} barem.")
    return len(bulten), len(baremler)


# ================================================================ analiz (arayüzden bağımsız)
def son_guncelleme():
    try:
        with sqlite3.connect(DB) as con:
            r = con.execute("SELECT cekilme, gun, mac FROM meta").fetchone()
    except sqlite3.Error:
        return None
    return dict(zip(["cekilme", "gun", "mac"], r)) if r else None


def verileri_yukle():
    import mackolik as mk
    with sqlite3.connect(DB) as con:
        try:
            bulten = pd.read_sql("SELECT * FROM bulten", con)
            baremler = pd.read_sql("SELECT * FROM baremler", con)
        except Exception:
            bulten, baremler = pd.DataFrame(columns=BULTEN_KOLONLAR), pd.DataFrame(columns=BAREM_KOLONLAR)
    for k in BULTEN_KOLONLAR:
        if k not in bulten:
            bulten[k] = None
    for k in ("ev_id", "dep_id"):  # Maçkolik takım id'leri metin; eski (SofaScore) sayısal id'ler eşleşmez
        bulten[k] = bulten[k].where(bulten[k].notna(), None).astype(object)
        bulten[k] = bulten[k].map(lambda v: v if isinstance(v, str) else None)
    for k in ("ms1", "ms2", "iy1", "iy0", "iy2"):
        bulten[k] = pd.to_numeric(bulten[k], errors="coerce")
    bulten["tarih"] = pd.to_datetime(bulten["tarih"])
    m = mk.takim_maclari(DB, "basketbol").rename(columns={"takim_skor": "takim_sayi", "rakip_skor": "rakip_sayi"})
    m["uzatma"] = 0  # Maçkolik günlük verisinde uzatma bilgisi yok (skorlar uzatmalar dahil)
    return bulten, baremler, m


class Seriler:
    """Takım id'si (Maçkolik) -> son n maç."""

    def __init__(self, maclar, n):
        self.n = n
        self.g = {k: v.tail(n) for k, v in maclar.groupby("takim_id", sort=False)} if len(maclar) else {}

    def maclar(self, tid, periyot=""):
        if not isinstance(tid, str):
            return None
        m = self.g.get(tid)
        if m is None or m.empty:
            return None
        if periyot == "IY_":
            m = m.dropna(subset=["takim_iy", "rakip_iy"])
        return m if len(m) else None

    def seri(self, tid, kapsam_turu, periyot=""):
        """kapsam_turu: 'atti' takımın sayısı, 'yedi' rakibin sayısı, 'toplam' maç toplamı."""
        m = self.maclar(tid, periyot)
        if m is None:
            return pd.Series(dtype=float)
        a, b = ("takim_iy", "rakip_iy") if periyot == "IY_" else ("takim_sayi", "rakip_sayi")
        return {"atti": m[a], "yedi": m[b], "toplam": m[a] + m[b]}[kapsam_turu].astype(float)


def _p_ust(mu, sd, cizgi):
    if pd.isna(mu) or pd.isna(sd):
        return np.nan
    return 0.5 * (1 - math.erf((cizgi - mu) / (sd * math.sqrt(2))))


def _oran_hesapla(s, cizgi, yon):
    if len(s) == 0:
        return np.nan
    return float((s > cizgi).mean() if yon == "Üst" else (s < cizgi).mean())


def beklenen(S, ev_id, dep_id, periyot=""):
    """Basit model: takımın attığı ile rakibin yediğinin ortalaması; saçılım = son maçlardaki standart sapma."""
    ea, ey = S.seri(ev_id, "atti", periyot), S.seri(ev_id, "yedi", periyot)
    da, dy = S.seri(dep_id, "atti", periyot), S.seri(dep_id, "yedi", periyot)
    if min(len(ea), len(da)) < 3:
        return None
    bek_ev = (ea.mean() + dy.mean()) / 2
    bek_dep = (da.mean() + ey.mean()) / 2
    alt = 4.0 if periyot == "IY_" else 6.0
    sd_ev = max(np.nanmean([ea.std(), dy.std()]), alt)
    sd_dep = max(np.nanmean([da.std(), ey.std()]), alt)
    sd_top = max(np.nanmean([S.seri(ev_id, "toplam", periyot).std(), S.seri(dep_id, "toplam", periyot).std()]),
                 alt * 1.5)
    sd_fark = max(np.nanmean([(ea - ey).std(), (da - dy).std()]), alt * 1.5)
    return {"ev": bek_ev, "dep": bek_dep, "sd_ev": sd_ev, "sd_dep": sd_dep, "sd_top": sd_top,
            "p_ev": _p_ust(bek_ev - bek_dep, sd_fark, 0.0)}


def barem_tablosu(bulten, baremler, maclar, n=10):
    """Her barem × Alt/Üst için son n maç isabet oranları, model olasılığı ve iddaa oranı."""
    S = Seriler(maclar, n)
    b = baremler.merge(bulten, on="event_id", how="inner")
    model = {}
    satirlar = []
    for r in b.itertuples(index=False):
        ev_ad, dep_ad = r.ev_iddaa, r.dep_iddaa
        if r.kapsam == "toplam":
            takim_id, rakip_id, takim_ad, rakip_ad = r.ev_id, r.dep_id, f"{ev_ad} – {dep_ad}", None
            kendi = S.seri(r.ev_id, "toplam", r.periyot)
            rakip = S.seri(r.dep_id, "toplam", r.periyot)
        elif r.kapsam == "ev":
            takim_id, rakip_id, takim_ad, rakip_ad = r.ev_id, r.dep_id, ev_ad, dep_ad
            kendi, rakip = S.seri(r.ev_id, "atti", r.periyot), S.seri(r.dep_id, "yedi", r.periyot)
        else:
            takim_id, rakip_id, takim_ad, rakip_ad = r.dep_id, r.ev_id, dep_ad, ev_ad
            kendi, rakip = S.seri(r.dep_id, "atti", r.periyot), S.seri(r.ev_id, "yedi", r.periyot)
        k = (r.event_id, r.periyot)
        if k not in model:
            model[k] = beklenen(S, r.ev_id, r.dep_id, r.periyot)
        mdl = model[k]
        if mdl:
            mu, sd = {"ev": (mdl["ev"], mdl["sd_ev"]), "dep": (mdl["dep"], mdl["sd_dep"]),
                      "toplam": (mdl["ev"] + mdl["dep"], mdl["sd_top"])}[r.kapsam]
            p_ust = _p_ust(mu, sd, r.cizgi)
        else:
            mu, p_ust = np.nan, np.nan
        alt_o, ust_o = pd.to_numeric(r.alt_oran, errors="coerce"), pd.to_numeric(r.ust_oran, errors="coerce")
        if pd.notna(alt_o) and pd.notna(ust_o):
            adil_ust = (1 / ust_o) / (1 / alt_o + 1 / ust_o)
        else:
            adil_ust = np.nan
        for yon, oran, mp, adil in (("Üst", ust_o, p_ust, adil_ust),
                                    ("Alt", alt_o, 1 - p_ust if pd.notna(p_ust) else np.nan,
                                     1 - adil_ust if pd.notna(adil_ust) else np.nan)):
            if pd.isna(oran):
                continue
            satirlar.append({
                "event_id": r.event_id, "Lig": r.lig, "Tarih": r.tarih, "Maç": f"{ev_ad} – {dep_ad}",
                "Takım": takim_ad if r.kapsam != "toplam" else "Maç toplamı", "Rakip": rakip_ad,
                "takim_id": takim_id, "rakip_id": rakip_id, "ev_id": r.ev_id, "dep_id": r.dep_id,
                "Ev": ev_ad, "Dep": dep_ad,
                "kapsam": r.kapsam, "periyot": r.periyot, "Kapsam": KAPSAM_ETIKET[r.kapsam],
                "Periyot": PERIYOT_ETIKET[r.periyot], "Yön": yon, "Çizgi": r.cizgi,
                "Barem": f"{'Ü' if yon == 'Üst' else 'A'} {r.cizgi:g}", "Oran": oran,
                "Form": _oran_hesapla(kendi, r.cizgi, yon), "Rakip form": _oran_hesapla(rakip, r.cizgi, yon),
                "Maç sayısı": len(kendi), "Ort.": kendi.mean() if len(kendi) else np.nan,
                "Rakip ort.": rakip.mean() if len(rakip) else np.nan, "Rakip maç sayısı": len(rakip),
                "Beklenen": mu, "Model": mp, "Adil": adil,
                "Değer": mp * oran - 1 if pd.notna(mp) else np.nan,
                "Ana": False, "_denge": abs(alt_o - ust_o) if pd.notna(alt_o) and pd.notna(ust_o) else 99,
                "iddaa pazarı": r.pazar_adi,
            })
    t = pd.DataFrame(satirlar)
    if t.empty:
        return t
    # Ana barem: her pazarda Alt ve Üst oranları birbirine en yakın çizgi
    grup = ["event_id", "kapsam", "periyot"]
    ana = t.loc[t.groupby(grup)["_denge"].idxmin(), grup + ["Çizgi"]].rename(columns={"Çizgi": "_ana_c"})
    t = t.merge(ana, on=grup, how="left")
    t["Ana"] = t["Çizgi"] == t["_ana_c"]
    t["Birleşik"] = t[["Form", "Rakip form"]].mean(axis=1)
    return t.drop(columns=["_denge", "_ana_c"])


def mac_tablosu(bulten, barem_t, maclar, n=10):
    """Maç başına özet: galibiyet oranları, ana baremler ve son n maç Üst oranları."""
    S = Seriler(maclar, n)
    ana = barem_t[(barem_t["Ana"]) & (barem_t["Yön"] == "Üst") & (barem_t["periyot"] == "")] \
        if not barem_t.empty else pd.DataFrame()
    satirlar = []
    for r in bulten.itertuples(index=False):
        ozet = {}
        for taraf, tid in (("ev", r.ev_id), ("dep", r.dep_id)):
            m = S.maclar(tid)
            if m is None:
                ozet[taraf] = None
                continue
            at, ye = m["takim_sayi"], m["rakip_sayi"]
            ozet[taraf] = {"n": len(m), "g": float((at > ye).mean()), "at": at.mean(), "ye": ye.mean(),
                           "top": (at + ye).mean(),
                           "form": "".join(np.where(at > ye, "G", "M")[-5:])}
        mdl = beklenen(S, r.ev_id, r.dep_id)
        a = ana[ana["event_id"] == r.event_id] if len(ana) else ana

        def pazar(kapsam):
            x = a[a["kapsam"] == kapsam] if len(a) else a
            return x.iloc[0] if len(x) else None

        top, evp, depp = pazar("toplam"), pazar("ev"), pazar("dep")
        oe, od = ozet["ev"], ozet["dep"]

        satirlar.append({
            "event_id": r.event_id, "Tarih": r.tarih, "Lig": r.lig, "Maç": f"{r.ev_iddaa} – {r.dep_iddaa}",
            "Ev": r.ev_iddaa, "Dep": r.dep_iddaa, "ev_id": r.ev_id, "dep_id": r.dep_id,
            "MS1": r.ms1, "MS2": r.ms2,
            "Adil 1": (1 / r.ms1) / (1 / r.ms1 + 1 / r.ms2) if pd.notna(r.ms1) and pd.notna(r.ms2) else np.nan,
            "Model 1": mdl["p_ev"] if mdl else np.nan,
            "Ev G": oe["g"] if oe else np.nan, "Dep G": od["g"] if od else np.nan,
            "Ev form": oe["form"] if oe else "", "Dep form": od["form"] if od else "",
            "Toplam": top["Çizgi"] if top is not None else np.nan,
            "Toplam Ü oran": top["Oran"] if top is not None else np.nan,
            "Toplam Ev L": top["Form"] if top is not None else np.nan,
            "Toplam Dep L": top["Rakip form"] if top is not None else np.nan,
            "Toplam model": top["Model"] if top is not None else np.nan,
            "Bek. toplam": (mdl["ev"] + mdl["dep"]) if mdl else np.nan,
            "Ev barem": evp["Çizgi"] if evp is not None else np.nan,
            "Ev barem L": evp["Form"] if evp is not None else np.nan,
            "Ev barem rakip": evp["Rakip form"] if evp is not None else np.nan,
            "Dep barem": depp["Çizgi"] if depp is not None else np.nan,
            "Dep barem L": depp["Form"] if depp is not None else np.nan,
            "Dep barem rakip": depp["Rakip form"] if depp is not None else np.nan,
            "Ev at": oe["at"] if oe else np.nan, "Ev ye": oe["ye"] if oe else np.nan,
            "Dep at": od["at"] if od else np.nan, "Dep ye": od["ye"] if od else np.nan,
            "Ev n": oe["n"] if oe else 0, "Dep n": od["n"] if od else 0,
        })
    return pd.DataFrame(satirlar)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--gun", type=int, default=0, help="0 = bugün (varsayılan), N = önümüzdeki N gün")
    p.add_argument("--lig", default="hepsi", help='"hepsi" ya da virgülle iddaa yarışma kimlikleri')
    p.add_argument("--takim-verisi-yok", action="store_true")
    p.add_argument("--ligleri-listele", action="store_true")
    a = p.parse_args()
    if a.ligleri_listele:
        print(lig_ozeti(a.gun).to_string(index=False))
        raise SystemExit
    guncelle(a.gun, "hepsi" if a.lig == "hepsi" else [int(x) for x in a.lig.split(",")],
             takim_verisi=not a.takim_verisi_yok)
