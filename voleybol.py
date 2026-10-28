"""
Voleybol: iddaa'da açık voleybol maçlarının oranları ve baremleri + takımların son maçları (Flashscore).
Veriler ayrı veritabanında: voleybol.db

Kullanım:
    python voleybol.py                 # bugünkü tüm iddaa voleybol maçları
    python voleybol.py --gun 2         # önümüzdeki 2 gün
    python voleybol.py --ligleri-listele

Değerlendirilen iddaa pazarları:
    Maç Sonucu · Toplam Sayı Alt/Üst · Toplam Set Sayısı (3/4/5 -> Alt/Üst 3.5 ve 4.5) ·
    Handikaplı Sayı Sonucu · Ev Sahibi / Deplasman Set Kazanır
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
DB = KLASOR / "voleybol.db"
SPOR_IDDAA = 23
SON_N = 10
TAZE_SAAT = 12
SETLER = range(1, 6)


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


def _birlesik(*oranlar):
    """Birden çok seçeneğin birleşik oranı (bahis sitesinin kendi fiyatlamasıyla tutarlı)."""
    return round(1 / sum(1 / o for o in oranlar), 2)


def secenekleri_cikar(olay):
    """Maç ayrıntısından değerlendirilecek seçenekler: her biri bir 'pazar' + 'seçenek' + oran (+ adil olasılık)."""
    sec, oranlar = [], {}

    def ekle(pazar, secenek, tur, cizgi, oran, adil, taraf=None):
        sec.append({"pazar": pazar, "secenek": secenek, "tur": tur, "cizgi": cizgi, "oran": oran,
                    "adil": adil, "taraf": taraf})

    for m in olay.get("m", []) or []:
        k = f"{m.get('t')}_{m.get('st')}"
        o = {x.get("n"): ib._oran(x.get("odd")) for x in m.get("o", [])}
        if k == "1_10":
            oranlar.update(ms1=o.get("1"), ms2=o.get("2"))
            a = _adil(o.get("1"), o.get("2"))
            ekle("Maç sonucu", "MS 1", "ms", None, o.get("1"), a[0], "ev")
            ekle("Maç sonucu", "MS 2", "ms", None, o.get("2"), a[1], "dep")
        elif k == "2_217" and m.get("sov") is not None and set(o) == {"Alt", "Üst"}:
            c = float(str(m["sov"]).replace(",", "."))
            oranlar.update(toplam_sayi=c, ts_alt=o["Alt"], ts_ust=o["Üst"])
            a = _adil(o["Alt"], o["Üst"])
            ekle("Toplam sayı", f"Alt {c:g}", "sayi_alt", c, o["Alt"], a[0])
            ekle("Toplam sayı", f"Üst {c:g}", "sayi_ust", c, o["Üst"], a[1])
        elif k == "2_713" and {"3", "4", "5"} <= set(o):
            o3, o4, o5 = o["3"], o["4"], o["5"]
            oranlar.update(set3=o3, set4=o4, set5=o5)
            p = _adil(o3, o4, o5)
            if all(v is not None for v in (o3, o4, o5)):
                ekle("Toplam set", "Alt 3.5 (3 set)", "set_alt", 3.5, o3, p[0])
                ekle("Toplam set", "Üst 3.5 (4-5 set)", "set_ust", 3.5, _birlesik(o4, o5), p[1] + p[2])
                ekle("Toplam set", "Alt 4.5 (3-4 set)", "set_alt", 4.5, _birlesik(o3, o4), p[0] + p[1])
                ekle("Toplam set", "Üst 4.5 (5 set)", "set_ust", 4.5, o5, p[2])
        elif k == "2_231" and m.get("sov") is not None and {"1", "2"} <= set(o):
            h = float(str(m["sov"]).replace(",", "."))
            oranlar.update(hnd=h, hnd1=o["1"], hnd2=o["2"])
            a = _adil(o["1"], o["2"])
            ekle("Sayı handikapı", f"Ev ({h:+g})", "hnd_ev", h, o["1"], a[0], "ev")
            ekle("Sayı handikapı", f"Dep ({-h:+g})", "hnd_dep", h, o["2"], a[1], "dep")
        elif k in ("2_711", "2_712") and {"Evet", "Hayır"} <= set(o):
            taraf = "ev" if k == "2_711" else "dep"
            ad = "Ev sahibi set alır" if taraf == "ev" else "Deplasman set alır"
            a = _adil(o["Evet"], o["Hayır"])
            ekle(ad, "Evet", "set_alir", None, o["Evet"], a[0], taraf)
            ekle(ad, "Hayır", "set_almaz", None, o["Hayır"], a[1], taraf)
    return [s for s in sec if s["oran"] is not None], oranlar


def _adil(*oranlar):
    if any(o is None for o in oranlar):
        return [np.nan] * len(oranlar)
    t = [1 / o for o in oranlar]
    return [x / sum(t) for x in t]


# ================================================================ veritabanı
def _tablolar(con):
    setk = ", ".join(f"takim_s{i} REAL, rakip_s{i} REAL" for i in SETLER)
    con.execute(f"""CREATE TABLE IF NOT EXISTS fs_maclar (
        mac_id TEXT, takim_id TEXT, tarih TEXT, lig TEXT, saha TEXT, takim TEXT, rakip TEXT, rakip_id TEXT,
        takim_set REAL, rakip_set REAL, takim_sayi REAL, rakip_sayi REAL, set_sayisi INTEGER, {setk},
        PRIMARY KEY (mac_id, takim_id))""")
    con.execute("CREATE TABLE IF NOT EXISTS fs_takimlar (takim_id TEXT PRIMARY KEY, slug TEXT, takim TEXT, "
                "son_cekim TEXT)")


def _mac_satiri(k, tid):
    """Flashscore maç kaydı -> takım açısından satır."""
    ev_mi = k["ev_id"] == tid
    setler = [(a, b) if ev_mi else (b, a) for a, b in k["setler"]]
    oynanan = [(a, b) for a, b in setler if a is not None and b is not None]
    return {"mac_id": k["mac_id"], "takim_id": tid,
            "tarih": datetime.fromtimestamp(k["ts"] or 0, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "lig": k["lig"], "saha": "Ev" if ev_mi else "Deplasman",
            "takim": k["ev"] if ev_mi else k["dep"], "rakip": k["dep"] if ev_mi else k["ev"],
            "rakip_id": k["dep_id"] if ev_mi else k["ev_id"],
            "takim_set": k["ev_set"] if ev_mi else k["dep_set"], "rakip_set": k["dep_set"] if ev_mi else k["ev_set"],
            "takim_sayi": sum(a for a, _ in oynanan) if oynanan else None,
            "rakip_sayi": sum(b for _, b in oynanan) if oynanan else None,
            "set_sayisi": len(oynanan) or None,
            **{f"takim_s{i}": setler[i - 1][0] for i in SETLER},
            **{f"rakip_s{i}": setler[i - 1][1] for i in SETLER}}


def takimlari_guncelle(takimlar, yazdir=print, ilerleme=None, zorla=False):
    """takimlar: {flashscore takım id: slug}. Her takımın sonuçlar sayfasından (takım başına 1 istek) son maçlarını
    voleybol.db'ye yazar. Son TAZE_SAAT saatte çekilen takımlar atlanır; var olan maçlar tekrar yazılmaz."""
    import flashscore as fs
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        taze = pd.read_sql("SELECT * FROM fs_takimlar", con).set_index("takim_id")
        mevcut = pd.read_sql("SELECT mac_id, takim_id FROM fs_maclar", con)
    mevcut = set(zip(mevcut["mac_id"], mevcut["takim_id"]))
    simdi = datetime.now()
    yapilacak = []
    for tid, slug in takimlar.items():
        if not tid or not slug:
            continue
        if not zorla and tid in taze.index:
            son = pd.to_datetime(taze.loc[tid, "son_cekim"], errors="coerce")
            if pd.notna(son) and simdi - son < timedelta(hours=TAZE_SAAT):
                continue
        yapilacak.append((tid, slug))
    yazdir(f"Takım verisi: {len(takimlar)} takımdan {len(yapilacak)} tanesi Flashscore'dan güncellenecek "
           f"(takım başına 1 istek).")
    yeni_toplam, t0 = 0, time.time()
    for i, (tid, slug) in enumerate(yapilacak, 1):
        try:
            maclar = fs.takim_sonuclari(slug, tid)
        except fs.FlashscoreEngel as hata:
            yazdir(f"⚠ {hata}")
            break
        except Exception as hata:
            yazdir(f"    Flashscore hatası ({slug}): {hata}")
            continue
        satirlar = [_mac_satiri(k, tid) for k in maclar if tid in (k["ev_id"], k["dep_id"])][-SON_N:]
        yeni = [x for x in satirlar if (x["mac_id"], tid) not in mevcut]
        with sqlite3.connect(DB) as con:
            if yeni:
                pd.DataFrame(yeni).to_sql("fs_maclar", con, if_exists="append", index=False)
            con.execute("INSERT OR REPLACE INTO fs_takimlar VALUES (?, ?, ?, ?)",
                        (tid, slug, satirlar[-1]["takim"] if satirlar else None, simdi.strftime("%Y-%m-%d %H:%M")))
        yeni_toplam += len(yeni)
        if ilerleme:
            ilerleme(i, len(yapilacak))
        if i % 10 == 0 or i == len(yapilacak):
            kalan = (time.time() - t0) / i * (len(yapilacak) - i)
            yazdir(f"  [{i}/{len(yapilacak)}] takım · {yeni_toplam} yeni maç · kalan ≈ {kalan / 60:.0f} dk")
    return yeni_toplam


BULTEN_KOLONLAR = ["event_id", "lig", "ulke", "tarih", "ev_iddaa", "dep_iddaa", "ev", "dep", "ev_id", "dep_id",
                   "fs_id", "ms1", "ms2", "toplam_sayi", "ts_alt", "ts_ust", "set3", "set4", "set5",
                   "hnd", "hnd1", "hnd2", "cekilme"]
SECENEK_KOLONLAR = ["event_id", "pazar", "secenek", "tur", "cizgi", "oran", "adil", "taraf"]


def guncelle(gun=0, ligler="hepsi", yazdir=print, takim_verisi=True, ilerleme=None):
    """iddaa voleybol bültenini ve oranlarını çeker; maçlar Flashscore'un günlük listesiyle eşleştirilir ve
    takımların son maçları Flashscore'dan gelir. (maç, seçenek) sayısı döner."""
    import flashscore as fs
    import sofascore as sofa  # yalnızca ad/saat eşleştirme fonksiyonu için (SofaScore'a istek gönderilmez)
    olaylar, adlar = olaylar_al(), yarismalar()
    simdi, sinir = ib.zaman_araligi(gun)
    secili = [e for e in olaylar if simdi.timestamp() < e.get("d", 0) <= sinir.timestamp()]
    if ligler != "hepsi" and ligler:
        kume = {int(c) for c in ligler}
        secili = [e for e in secili if e.get("ci") in kume]
    secili = ib.kupalari_cikar(secili, adlar, yazdir)
    secili.sort(key=lambda e: e.get("d", 0))
    donem = "bugün" if gun <= 0 else f"önümüzdeki {gun} günde"
    yazdir(f"iddaa'da {len(olaylar)} voleybol maçı açık; {donem} seçili liglerden {len(secili)} maç taranacak.")
    cekilme = simdi.strftime("%Y-%m-%d %H:%M")

    detaylar = ib._detaylari_cek(secili, yazdir) if secili else {}
    eslesme, tk = {}, pd.DataFrame()
    if secili and takim_verisi:
        try:
            # Yalnızca maçı olan günlerin listesi (gün başına 1 istek)
            gunler = sorted({(datetime.fromtimestamp(e["d"], ib.TZ).date() - simdi.date()).days for e in secili})
            tk = pd.concat([fs.gunluk("voleybol", g) for g in gunler if g >= 0], ignore_index=True)
            eslesme = sofa.eslestir(secili, tk) if len(tk) else {}
            yazdir(f"{len(eslesme)}/{len(secili)} maç Flashscore'da eşleşti.")
        except fs.FlashscoreEngel as hata:
            yazdir(f"⚠ {hata}")
        except Exception as hata:
            yazdir(f"Flashscore'a bağlanılamadı, takım verisi güncellenmedi: {hata}")

    bulten, secenekler = [], []
    for e in secili:
        sec, oranlar = secenekleri_cikar(detaylar.get(e["i"]) or e)
        s = eslesme.get(e["i"])
        ci = e.get("ci")
        bulten.append({"event_id": e["i"], "lig": adlar.get(ci, (f"Yarışma {ci}",))[0],
                       "ulke": adlar.get(ci, (None, None))[1],
                       "tarih": datetime.fromtimestamp(e["d"], ib.TZ).strftime("%Y-%m-%d %H:%M"),
                       "ev_iddaa": e.get("hn"), "dep_iddaa": e.get("an"),
                       "ev": s["ev"] if s else None, "dep": s["dep"] if s else None,
                       "ev_id": s["ev_id"] if s else None, "dep_id": s["dep_id"] if s else None,
                       "fs_id": s["sofa_id"] if s else None, **oranlar, "cekilme": cekilme})
        secenekler.extend({"event_id": e["i"], **x} for x in sec)

    with sqlite3.connect(DB) as con:
        _tablolar(con)
        pd.DataFrame(bulten, columns=BULTEN_KOLONLAR).to_sql("bulten", con, if_exists="replace", index=False)
        pd.DataFrame(secenekler, columns=SECENEK_KOLONLAR).to_sql("secenekler", con, if_exists="replace", index=False)
        con.execute("DROP TABLE IF EXISTS meta")
        con.execute("CREATE TABLE meta (cekilme, gun, mac)")
        con.execute("INSERT INTO meta VALUES (?, ?, ?)", (cekilme, gun, len(bulten)))
    yazdir(f"{len(bulten)} maç ve {len(secenekler)} oran kaydedildi.")

    if eslesme:
        takimlar = {}
        for s_ in eslesme.values():
            takimlar[s_["ev_id"]] = s_.get("ev_slug")
            takimlar[s_["dep_id"]] = s_.get("dep_slug")
        try:
            takimlari_guncelle(takimlar, yazdir, ilerleme)
        except Exception as hata:
            yazdir(f"Takım verisi tamamlanamadı: {hata}")
    eksik = [f"{b['lig']}: {b['ev_iddaa']} – {b['dep_iddaa']}" for b in bulten if b["ev_id"] is None]
    if eksik and eslesme:
        yazdir(f"Flashscore'da eşleşmeyen {len(eksik)} maç (geçmiş verisi yok):\n  " + "\n  ".join(eksik[:30]))
    yazdir(f"Bitti: {len(bulten)} maç, {len(secenekler)} oran (Flashscore'a {fs.istek_sayisi()} istek).")
    return len(bulten), len(secenekler)


# ================================================================ analiz
def son_guncelleme():
    try:
        with sqlite3.connect(DB) as con:
            r = con.execute("SELECT cekilme, gun, mac FROM meta").fetchone()
    except sqlite3.Error:
        return None
    return dict(zip(["cekilme", "gun", "mac"], r)) if r else None


def verileri_yukle():
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        try:
            bulten = pd.read_sql("SELECT * FROM bulten", con)
            sec = pd.read_sql("SELECT * FROM secenekler", con)
        except Exception:
            bulten, sec = pd.DataFrame(columns=BULTEN_KOLONLAR), pd.DataFrame(columns=SECENEK_KOLONLAR)
        maclar = pd.read_sql("SELECT * FROM fs_maclar", con)
    metin = ("lig", "ulke", "tarih", "ev_iddaa", "dep_iddaa", "ev", "dep", "cekilme", "ev_id", "dep_id", "fs_id")
    for k in BULTEN_KOLONLAR:
        if k not in bulten:
            bulten[k] = None
        if k not in metin:
            bulten[k] = pd.to_numeric(bulten[k], errors="coerce")
    for k in ("ev_id", "dep_id"):  # Flashscore id'leri metin (eski SofaScore sayısal id'leri eşleşmez)
        bulten[k] = bulten[k].map(lambda v: v if isinstance(v, str) else None)
    for k in ("cizgi", "oran", "adil"):
        sec[k] = pd.to_numeric(sec[k], errors="coerce")
    bulten["tarih"] = pd.to_datetime(bulten["tarih"])
    maclar["tarih"] = pd.to_datetime(maclar["tarih"])
    return bulten, sec, maclar.sort_values("tarih")


class Seriler:
    def __init__(self, maclar, n):
        self.g = {k: v.tail(n) for k, v in maclar.groupby("takim_id", sort=False)} if len(maclar) else {}

    def maclar(self, tid):
        if not isinstance(tid, str):
            return None
        m = self.g.get(tid)
        return m if m is not None and len(m) else None


def _tutar(m, tur, cizgi, bakis):
    """Bir seçeneğin tuttuğu maçlar (bool Series). bakis: 'ev' -> m ev sahibinin maçları, 'dep' -> deplasmanın.
    Takımla ilgili seçeneklerde deplasmanın maçları ters açıdan okunur."""
    toplam = m["takim_sayi"] + m["rakip_sayi"]
    fark = m["takim_sayi"] - m["rakip_sayi"]
    kazandi = m["takim_set"] > m["rakip_set"]
    if tur == "sayi_ust":
        return toplam > cizgi
    if tur == "sayi_alt":
        return toplam < cizgi
    if tur == "set_ust":
        return m["set_sayisi"] > cizgi
    if tur == "set_alt":
        return m["set_sayisi"] < cizgi
    raise ValueError(tur)


def _tutar_taraf(m, tur, cizgi, taraf, bakis):
    """Takıma bağlı seçenekler. taraf: seçeneğin ilgili olduğu takım (ev/dep); bakis: m kimin maçları."""
    ayni = taraf == bakis  # m, seçeneğin takımının maçları mı (yoksa rakibininkiler mi)
    ts, rs = (m["takim_set"], m["rakip_set"]) if ayni else (m["rakip_set"], m["takim_set"])
    fark = (m["takim_sayi"] - m["rakip_sayi"]) * (1 if ayni else -1)
    if tur == "ms":
        return ts > rs
    if tur == "set_alir":
        return ts >= 1
    if tur == "set_almaz":
        return ts < 1
    if tur == "hnd_ev":   # ev sahibi (h) handikapla: fark + h > 0 (taraf = ev)
        return fark + cizgi > 0
    if tur == "hnd_dep":  # deplasman (−h) handikapla: fark − h > 0 (taraf = dep)
        return fark - cizgi > 0
    raise ValueError(tur)


def _oran_al(m, sec, bakis):
    if m is None:
        return np.nan, 0
    if sec.taraf in ("ev", "dep"):
        s = _tutar_taraf(m, sec.tur, sec.cizgi, sec.taraf, bakis)
    else:
        s = _tutar(m, sec.tur, sec.cizgi, bakis)
    return float(s.mean()), len(s)


def _p_ust(mu, sd, c):
    if pd.isna(mu) or pd.isna(sd) or sd <= 0:
        return np.nan
    return 0.5 * (1 - math.erf((c - mu) / (sd * math.sqrt(2))))


def secenek_tablosu(bulten, sec, maclar, n=10):
    """Her iddaa seçeneği için: ev sahibinin ve deplasmanın son n maçında tutma oranı, olasılık, değer."""
    S = Seriler(maclar, n)
    b = sec.merge(bulten, on="event_id", how="inner")
    satirlar = []
    for r in b.itertuples(index=False):
        me, md = S.maclar(r.ev_id), S.maclar(r.dep_id)
        f_ev, n_ev = _oran_al(me, r, "ev")
        f_dep, n_dep = _oran_al(md, r, "dep")
        birlesik = np.nanmean([f_ev, f_dep]) if not (np.isnan(f_ev) and np.isnan(f_dep)) else np.nan
        model = birlesik
        if r.tur in ("sayi_ust", "sayi_alt") and me is not None and md is not None:
            te, td = me["takim_sayi"] + me["rakip_sayi"], md["takim_sayi"] + md["rakip_sayi"]
            mu, sd = (te.mean() + td.mean()) / 2, max(np.nanmean([te.std(), td.std()]), 8.0)
            p = _p_ust(mu, sd, r.cizgi)
            model = p if r.tur == "sayi_ust" else 1 - p
        takim = {"ev": r.ev_iddaa, "dep": r.dep_iddaa}.get(r.taraf, "Maç")
        satirlar.append({
            "event_id": r.event_id, "Lig": r.lig, "Tarih": r.tarih, "Maç": f"{r.ev_iddaa} – {r.dep_iddaa}",
            "Ev": r.ev_iddaa, "Dep": r.dep_iddaa, "ev_id": r.ev_id, "dep_id": r.dep_id,
            "Pazar": r.pazar, "Seçenek": r.secenek, "Takım": takim, "tur": r.tur, "cizgi": r.cizgi,
            "taraf": r.taraf, "Oran": r.oran, "Ev L": f_ev, "Dep L": f_dep, "Birleşik": birlesik,
            "Model": model, "Adil": r.adil,
            "Değer": model * r.oran - 1 if pd.notna(model) else np.nan,
            "Ev n": n_ev, "Dep n": n_dep,
        })
    return pd.DataFrame(satirlar)


def mac_tablosu(bulten, maclar, n=10):
    """Maç başına özet: galibiyet, set ve sayı istatistikleri (son n maç) ve iddaa ana oranları."""
    S = Seriler(maclar, n)
    satirlar = []
    for r in bulten.itertuples(index=False):
        oz = {}
        for taraf, tid in (("ev", r.ev_id), ("dep", r.dep_id)):
            m = S.maclar(tid)
            if m is None:
                oz[taraf] = None
                continue
            kaz = m["takim_set"] > m["rakip_set"]
            top = m["takim_sayi"] + m["rakip_sayi"]
            oz[taraf] = {"n": len(m), "g": kaz.mean(), "set_al": m["takim_set"].mean(),
                         "set_ver": m["rakip_set"].mean(), "sayi": m["takim_sayi"].mean(),
                         "sayi_ye": m["rakip_sayi"].mean(), "top": top.mean(),
                         "s3": (m["set_sayisi"] == 3).mean(), "s5": (m["set_sayisi"] == 5).mean(),
                         "ts_ust": (top > r.toplam_sayi).mean() if pd.notna(r.toplam_sayi) else np.nan,
                         "fark": (m["takim_sayi"] - m["rakip_sayi"]).mean(),
                         "form": "".join(np.where(kaz, "G", "M")[-5:])}
        oe, od = oz["ev"], oz["dep"]

        def g(o, k):
            return o[k] if o else np.nan

        satirlar.append({
            "event_id": r.event_id, "Tarih": r.tarih, "Lig": r.lig, "Maç": f"{r.ev_iddaa} – {r.dep_iddaa}",
            "Ev": r.ev_iddaa, "Dep": r.dep_iddaa, "ev_id": r.ev_id, "dep_id": r.dep_id,
            "MS1": r.ms1, "MS2": r.ms2, "Adil 1": _adil(r.ms1, r.ms2)[0] if pd.notna(r.ms1) and pd.notna(r.ms2) else np.nan,
            "Ev G": g(oe, "g"), "Dep G": g(od, "g"),
            "Model 1": np.nanmean([g(oe, "g"), 1 - g(od, "g")]) if (oe and od) else np.nan,
            "Toplam sayı": r.toplam_sayi, "TS Ü oran": r.ts_ust,
            "TS Ü ev": g(oe, "ts_ust"), "TS Ü dep": g(od, "ts_ust"),
            "Ort. toplam": np.nanmean([g(oe, "top"), g(od, "top")]) if (oe or od) else np.nan,
            "3 set oran": r.set3, "5 set oran": r.set5,
            "3 set ev": g(oe, "s3"), "3 set dep": g(od, "s3"), "5 set ev": g(oe, "s5"), "5 set dep": g(od, "s5"),
            "Handikap": r.hnd, "Ev fark": g(oe, "fark"), "Dep fark": g(od, "fark"),
            "Ev set": g(oe, "set_al"), "Ev set ver": g(oe, "set_ver"),
            "Dep set": g(od, "set_al"), "Dep set ver": g(od, "set_ver"),
            "Ev form": oe["form"] if oe else "", "Dep form": od["form"] if od else "",
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
