"""Maç İstatistikleri sayfasının hesaplamaları (arayüzden bağımsız; ön hesaplama da bunları kullanır)."""
import sqlite3

import numpy as np
import pandas as pd

from tahmin import DB, son_maclar, tahmin_et, veri_yukle

MAX_GOL = 10


def bulten_yukle():
    with sqlite3.connect(DB) as con:
        b = pd.read_sql("SELECT * FROM bulten", con)
    b["tarih"] = pd.to_datetime(b["tarih"])
    return b


def skor_matrisi(l1, l2):
    k = np.arange(MAX_GOL + 1)
    from math import factorial
    f = np.array([factorial(int(i)) for i in k], dtype=float)
    p1 = np.exp(-l1) * l1 ** k / f
    p2 = np.exp(-l2) * l2 ** k / f
    m = np.outer(p1, p2)
    return m / m.sum()


def matris_olasiliklari(m):
    i, j = np.indices(m.shape)
    return {"p1": m[i > j].sum(), "p0": m[i == j].sum(), "p2": m[i < j].sum(),
            "ust15": m[i + j > 1.5].sum(), "ust25": m[i + j > 2.5].sum(), "ust35": m[i + j > 3.5].sum(),
            "kg": m[(i > 0) & (j > 0)].sum()}


def takim_ozeti(df, takim, n):
    if not isinstance(takim, str):
        return None
    m = son_maclar(df, takim, n, None, "gol")
    if m.empty:
        return None
    at, ye = m["takim_gol"], m["rakip_gol"]
    sonuc = np.where(at > ye, "G", np.where(at == ye, "B", "M"))
    return {"n": len(m), "at": at.mean(), "ye": ye.mean(), "ust25": ((at + ye) > 2.5).mean(),
            "kg": ((at > 0) & (ye > 0)).mean(), "form": "".join(sonuc[-5:]),
            "puan": (3 * (sonuc == "G").sum() + (sonuc == "B").sum()) / len(m)}


def adil(*oranlar):
    """Marjı ayıklanmış olasılıklar (oranlardan biri yoksa hepsi NaN)."""
    if any(pd.isna(o) for o in oranlar):
        return [np.nan] * len(oranlar)
    t = [1 / o for o in oranlar]
    return [x / sum(t) for x in t]


def ozet_tablosu(b, df, n):
    """Bültendeki her maç: oranlar, gol modeli olasılıkları, takımların son n maç formu."""
    bilinen = set(df["takim"])
    satirlar = []
    for r in b.itertuples(index=False):
        ev = r.ev if isinstance(r.ev, str) and r.ev in bilinen else None
        dep = r.dep if isinstance(r.dep, str) and r.dep in bilinen else None
        oe, od = takim_ozeti(df, ev, n), takim_ozeti(df, dep, n)
        p = {}
        bek = (np.nan, np.nan)
        if oe and od:
            mdl = tahmin_et(df, ev, dep, "gol", "", n, False, 2.0, simulasyon=0)
            if mdl is not None:
                bek = (max(mdl["bek_ev"], 0.05), max(mdl["bek_dep"], 0.05))
                p = matris_olasiliklari(skor_matrisi(*bek))
        a1, a0, a2 = adil(r.ms1, r.ms0, r.ms2)
        a_ust25 = adil(r.au25_ust, r.au25_alt)[0]
        a_kg = adil(r.kg_var, r.kg_yok)[0]
        # Değer: model olasılığı × oran − 1 (en yüksek seçenek)
        secenekler = [("MS 1", p.get("p1"), r.ms1), ("MS 0", p.get("p0"), r.ms0), ("MS 2", p.get("p2"), r.ms2),
                      ("2.5 Üst", p.get("ust25"), r.au25_ust),
                      ("2.5 Alt", 1 - p["ust25"] if p else None, r.au25_alt),
                      ("KG Var", p.get("kg"), r.kg_var), ("KG Yok", 1 - p["kg"] if p else None, r.kg_yok)]
        degerler = [(ad, pr * o - 1) for ad, pr, o in secenekler if pr is not None and pd.notna(o)]
        en_iyi = max(degerler, key=lambda x: x[1]) if degerler else (None, np.nan)
        form_ust = np.nanmean([o["ust25"] for o in (oe, od) if o]) if (oe or od) else np.nan
        form_kg = np.nanmean([o["kg"] for o in (oe, od) if o]) if (oe or od) else np.nan
        satirlar.append({
            "event_id": r.event_id, "Tarih": r.tarih, "Lig": r.lig,
            "Maç": f"{r.ev_iddaa} – {r.dep_iddaa}", "ev_db": ev, "dep_db": dep,
            "Ev": r.ev_iddaa, "Dep": r.dep_iddaa,
            "1": r.ms1, "0": r.ms0, "2": r.ms2,
            "M1": p.get("p1", np.nan), "M0": p.get("p0", np.nan), "M2": p.get("p2", np.nan),
            "A1": a1, "A0": a0, "A2": a2,
            "Bek ev": bek[0], "Bek dep": bek[1],
            "Ü2.5": r.au25_ust, "A2.5": r.au25_alt, "M Ü2.5": p.get("ust25", np.nan), "Adil Ü2.5": a_ust25,
            "Form Ü2.5": form_ust,
            "KG": r.kg_var, "KG yok": r.kg_yok, "M KG": p.get("kg", np.nan), "Adil KG": a_kg, "Form KG": form_kg,
            "Ev form": oe["form"] if oe else "", "Dep form": od["form"] if od else "",
            "Ev n": oe["n"] if oe else 0, "Dep n": od["n"] if od else 0,
            "Ev at": oe["at"] if oe else np.nan, "Ev ye": oe["ye"] if oe else np.nan,
            "Dep at": od["at"] if od else np.nan, "Dep ye": od["ye"] if od else np.nan,
            "En iyi": en_iyi[0], "Değer": en_iyi[1],
            "Barem": r.barem_sayisi, "İst. pazarı": bool(r.istatistik_pazari),
        })
    return pd.DataFrame(satirlar)


