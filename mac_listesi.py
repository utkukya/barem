"""Maç İstatistikleri: iddaa'da açık tüm maçlar, oranlar ve takımların son maçlarına dayalı istatistikler."""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from istatistikler import ISTATISTIKLER
from ortak import veritabani_kontrol
import mac_ozeti as mo
from mac_ozeti import adil, matris_olasiliklari, skor_matrisi, takim_ozeti
from tahmin import DB, kolonlar, son_maclar, tahmin_et, veri_yukle

TZ = ZoneInfo("Europe/Istanbul")
MODEL, IDDAA, FORM = "#2a78d6", "#eb6834", "#9a9890"
YESIL_YAZI, SARI_ZEMIN, SARI_YAZI = "#0a7a3f", "#fdecc8", "#8a5a00"

veritabani_kontrol()


def damga_al():
    try:
        with sqlite3.connect(DB) as con:
            c = con.execute("SELECT MAX(cekilme) FROM bulten").fetchone()[0]
            n = con.execute("SELECT COUNT(*) FROM maclar").fetchone()[0]
        return f"{c}|{n}" if c else ""
    except sqlite3.Error:
        return ""


@st.cache_data(show_spinner=False)
def veriler(damga):
    return mo.bulten_yukle(), veri_yukle()


@st.cache_data(show_spinner="Maç istatistikleri hesaplanıyor…")
def ozet_tablosu(damga, n):
    import hazir
    return hazir.futbol_ozet(n)


# ---------------------------------------------------------------- sayfa
damga = damga_al()
st.title("Maç İstatistikleri")
if not damga:
    st.info("Henüz bülten verisi yok. **Barem Tarayıcı** sayfasındaki **Baremleri güncelle** penceresinden "
            "(\"iddaa'daki tüm ligler\" açık) ya da komut satırından `python iddaa_baremler.py` ile maçları çekin.")
    st.stop()

with st.sidebar:
    st.header("Filtreler")
    n = st.slider("Kaç maça bakılsın", 3, 10, 10, key="ml_n")
    tum = ozet_tablosu(damga, n)
    ligler = sorted(tum["Lig"].dropna().unique())
    sec_lig = st.multiselect("Ligler", ligler, placeholder="Tüm ligler", key="ml_lig")
    zaman = st.segmented_control("Ne zaman", ["Tümü", "Bugün", "Yarın"], default="Tümü", key="ml_zaman")
    baslamamis = st.toggle("Yalnızca başlamamış maçlar", True, key="ml_bas")
    veri = st.segmented_control("Takım verisi", ["Hepsi", "İki takım da", "Hiçbiri"], default="Hepsi",
                                key="ml_veri", help="İki takım da: iki takımın da geçmiş maçı veritabanında var "
                                                    "(model olasılıkları yalnızca bunlarda hesaplanır).")
    min_mac = st.slider("En az maç sayısı (iki takım)", 0, 10, 0, key="ml_min")
    siralama = st.selectbox("Sırala", ["Saat", "Değer", "Model 2.5 Üst", "Model KG Var", "Ev kazanır (model)",
                                       "Deplasman kazanır (model)", "Beklenen gol"], key="ml_sirala")

ara_kol, _ = st.columns([2, 3])
arama = ara_kol.text_input("Ara", placeholder="Takım, maç ya da lig ara", label_visibility="collapsed",
                           key="ml_arama")

t = tum.copy()
if sec_lig:
    t = t[t["Lig"].isin(sec_lig)]
simdi = datetime.now(TZ).replace(tzinfo=None)
if baslamamis:
    t = t[t["Tarih"] > simdi]
if zaman == "Bugün":
    t = t[t["Tarih"].dt.date == simdi.date()]
elif zaman == "Yarın":
    t = t[t["Tarih"].dt.date == (simdi + pd.Timedelta(days=1)).date()]
iki = t["ev_db"].notna() & t["dep_db"].notna()
if veri == "İki takım da":
    t = t[iki]
elif veri == "Hiçbiri":
    t = t[t["ev_db"].isna() & t["dep_db"].isna()]
if min_mac:
    t = t[(t["Ev n"] >= min_mac) & (t["Dep n"] >= min_mac)]
if arama:
    t = t[(t["Maç"] + " " + t["Lig"]).str.contains(arama, case=False, regex=False)]

anahtar = {"Saat": ("Tarih", True), "Değer": ("Değer", False), "Model 2.5 Üst": ("M Ü2.5", False),
           "Model KG Var": ("M KG", False), "Ev kazanır (model)": ("M1", False),
           "Deplasman kazanır (model)": ("M2", False)}
if siralama == "Beklenen gol":
    t = t.assign(_bek=t["Bek ev"] + t["Bek dep"]).sort_values("_bek", ascending=False, na_position="last")
else:
    k, artan = anahtar[siralama]
    t = t.sort_values([k, "Tarih"], ascending=[artan, True], na_position="last")
t = t.reset_index(drop=True)

# Üst özet
c1, c2, c3, c4 = st.columns(4)
c1.metric("Maç", len(t))
c2.metric("Lig", t["Lig"].nunique())
c3.metric("İki takımın da verisi var", f"{int((t['ev_db'].notna() & t['dep_db'].notna()).sum())}")
c4.metric("İstatistik pazarı açık", int(t["İst. pazarı"].sum()),
          help="iddaa'nın korner, kart, şut gibi pazar açtığı maçlar (Barem Tarayıcı'da görünür)")

if t.empty:
    st.info("Bu filtrelerle maç yok.")
    st.stop()
from ortam import sayfala
t = sayfala(t, "ml").reset_index(drop=True)


def yuzde(v):
    return "–" if pd.isna(v) else f"{100 * v:3.0f}%"


def oran(v):
    return "–" if pd.isna(v) else f"{v:5.2f}"


def uclu(a, b, c):
    return "–" if pd.isna(a) else f"{100 * a:.0f} / {100 * b:.0f} / {100 * c:.0f}"


gorunum = pd.DataFrame({
    "Saat": t["Tarih"].dt.strftime("%d.%m %H:%M"),
    "Lig": t["Lig"],
    "Maç": t["Maç"],
    "1": t["1"].map(oran), "0": t["0"].map(oran), "2": t["2"].map(oran),
    "Model 1/0/2 %": [uclu(a, b, c) for a, b, c in zip(t["M1"], t["M0"], t["M2"])],
    "Bek. skor": [("–" if pd.isna(a) else f"{a:.1f} – {b:.1f}") for a, b in zip(t["Bek ev"], t["Bek dep"])],
    "2.5Ü oran": t["Ü2.5"].map(oran),
    "2.5Ü model": t["M Ü2.5"].map(yuzde),
    f"2.5Ü L{n}": t["Form Ü2.5"].map(yuzde),
    "KG oran": t["KG"].map(oran),
    "KG model": t["M KG"].map(yuzde),
    f"KG L{n}": t["Form KG"].map(yuzde),
    "Ev form": [f"{f} ({int(k)})" if isinstance(f, str) and f and pd.notna(k) else "–" for f, k in zip(t["Ev form"], t["Ev n"])],
    "Dep form": [f"{f} ({int(k)})" if isinstance(f, str) and f and pd.notna(k) else "–" for f, k in zip(t["Dep form"], t["Dep n"])],
    "En iyi değer": [("–" if pd.isna(v) else f"{ad} {100 * v:+.0f}%") for ad, v in zip(t["En iyi"], t["Değer"])],
    "Veri": [f"{a}/{b}" for a, b in zip(t["Ev n"], t["Dep n"])],
})


def renkler(_):
    css = pd.DataFrame("", index=gorunum.index, columns=gorunum.columns)
    for kol, kaynak in (("2.5Ü model", "M Ü2.5"), ("KG model", "M KG"),
                        (f"2.5Ü L{n}", "Form Ü2.5"), (f"KG L{n}", "Form KG")):
        css[kol] = t[kaynak].map(lambda v: "" if pd.isna(v) else
                                 f"color: {YESIL_YAZI}; font-weight: 600" if v >= 0.65 else
                                 f"background-color: {SARI_ZEMIN}; color: {SARI_YAZI}" if v <= 0.35 else "")
    css["En iyi değer"] = t["Değer"].map(lambda v: f"color: {YESIL_YAZI}; font-weight: 600"
                                         if pd.notna(v) and v > 0.05 else "")
    return css


secim = st.dataframe(
    gorunum.style.apply(renkler, axis=None), hide_index=True, width="stretch",
    height=min(36 * (len(gorunum) + 1) + 4, 640), on_select="rerun", selection_mode="single-row", key="ml_tablo",
    column_config={
        "Maç": st.column_config.TextColumn(width="medium"),
        "Lig": st.column_config.TextColumn(width="small"),
        "Model 1/0/2 %": st.column_config.TextColumn(help="Poisson modeli: takımların son maçlarındaki attığı/yediği "
                                                          "goller, lig ortalamasına göre"),
        "Bek. skor": st.column_config.TextColumn(help="Modelin beklediği gol sayıları (ev – deplasman)"),
        f"2.5Ü L{n}": st.column_config.TextColumn(help=f"İki takımın son {n} maçında toplam golün 2.5'i geçme "
                                                       "oranlarının ortalaması"),
        f"KG L{n}": st.column_config.TextColumn(help=f"İki takımın son {n} maçında karşılıklı gol oranlarının ortalaması"),
        "Ev form": st.column_config.TextColumn(help="Son 5 maç (eskiden yeniye): G galibiyet, B beraberlik, M mağlubiyet. "
                                                    "Parantez içi: verisi olan maç sayısı (en fazla son N)."),
        "En iyi değer": st.column_config.TextColumn(help="Model olasılığı × iddaa oranı − 1 en yüksek olan seçenek. "
                                                         "Model basit bir gol modelidir; az maçlı takımlarda temkinli olun."),
        "Veri": st.column_config.TextColumn(help="Veritabanındaki maç sayısı (ev / deplasman)"),
    },
)
st.caption("Satıra tıklayınca altta maçın ayrıntılı istatistikleri açılır. Yeşil: %65 ve üstü, sarı: %35 ve altı.")

satir = secim.selection.rows if secim and secim.selection else []
if not satir or satir[0] >= len(t):
    st.stop()
r = t.iloc[satir[0]]
b, df = veriler(damga)
st.divider()
st.subheader(r["Maç"])
st.caption(f"{r['Lig']} · {r['Tarih']:%d.%m.%Y %H:%M}")

# ---- olasılık karşılaştırması
k = st.columns(5)
etiketler = [("Ev kazanır", "M1", "A1"), ("Beraberlik", "M0", "A0"), ("Deplasman kazanır", "M2", "A2"),
             ("2.5 Üst", "M Ü2.5", "Adil Ü2.5"), ("KG Var", "M KG", "Adil KG")]
for kol, (ad, m_, a_) in zip(k, etiketler):
    kol.metric(ad, yuzde(r[m_]).strip(),
               delta=None if pd.isna(r[m_]) or pd.isna(r[a_]) else f"iddaa %{100 * r[a_]:.0f}",
               delta_color="off", help="Üstte model olasılığı, altta iddaa oranının marjı ayıklanmış olasılığı")

grafik = []
for ad, m_, a_ in etiketler:
    grafik.append({"Seçenek": ad, "Kaynak": "Model", "Olasılık": r[m_]})
    grafik.append({"Seçenek": ad, "Kaynak": "iddaa (adil)", "Olasılık": r[a_]})
for ad, kol_ in (("2.5 Üst", "Form Ü2.5"), ("KG Var", "Form KG")):
    grafik.append({"Seçenek": ad, "Kaynak": f"Son {n} maç", "Olasılık": r[kol_]})
g = pd.DataFrame(grafik).dropna()
sol, sag = st.columns([3, 2], gap="large")
with sol:
    st.markdown("**Olasılıklar: model, iddaa ve son maçlar**")
    if g.empty:
        st.info("İki takımın da verisi olmadığı için model hesaplanamadı.")
    else:
        sira = [e[0] for e in etiketler]
        kaynaklar = ["Model", "iddaa (adil)", f"Son {n} maç"]
        st.altair_chart(
            alt.Chart(g).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("Seçenek:N", sort=sira, title=None, axis=alt.Axis(labelAngle=0)),
                xOffset=alt.XOffset("Kaynak:N", sort=kaynaklar),
                y=alt.Y("Olasılık:Q", axis=alt.Axis(format="%"), title=None, scale=alt.Scale(domain=[0, 1])),
                color=alt.Color("Kaynak:N", scale=alt.Scale(domain=kaynaklar, range=[MODEL, IDDAA, FORM]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=["Seçenek:N", "Kaynak:N", alt.Tooltip("Olasılık:Q", format=".0%")],
            ).properties(height=280), width="stretch")
with sag:
    st.markdown("**Skor olasılıkları (model)**")
    if pd.isna(r["Bek ev"]):
        st.info("Model yok.")
    else:
        m = skor_matrisi(r["Bek ev"], r["Bek dep"])[:6, :6]
        hm = pd.DataFrame([{"Ev": i, "Dep": j, "p": m[i, j]} for i in range(6) for j in range(6)])
        taban = alt.Chart(hm).encode(x=alt.X("Dep:O", title=f"{r['Dep']} golü"),
                                     y=alt.Y("Ev:O", title=f"{r['Ev']} golü"))
        st.altair_chart(
            (taban.mark_rect(stroke="white", strokeWidth=2).encode(
                color=alt.Color("p:Q", scale=alt.Scale(scheme="blues"), legend=None),
                tooltip=[alt.Tooltip("Ev:O", title="Ev"), alt.Tooltip("Dep:O", title="Dep"),
                         alt.Tooltip("p:Q", format=".1%", title="Olasılık")])
             + taban.mark_text(fontSize=10).encode(
                text=alt.Text("p:Q", format=".0%"),
                color=alt.condition(alt.datum.p > 0.08, alt.value("white"), alt.value("#3d3c38"))))
            .properties(height=280), width="stretch")


# ---- takımların son maçları
def son_mac_tablosu(takim):
    mm = son_maclar(df, takim, n, None, "gol").iloc[::-1]
    return pd.DataFrame({
        "Tarih": mm["tarih"].dt.strftime("%d.%m.%y"),
        "Turnuva": mm["lig"],
        "Rakip": mm["rakip"] + mm["saha"].map({"Ev": " (E)", "Deplasman": " (D)"}).fillna(""),
        "Skor": mm["takim_gol"].astype("Int64").astype(str) + "-" + mm["rakip_gol"].astype("Int64").astype(str),
        "İY": (mm["IY_takim_gol"].astype("Int64").astype(str) + "-" + mm["IY_rakip_gol"].astype("Int64").astype(str))
        if "IY_takim_gol" in mm else "",
        "S": np.where(mm["takim_gol"] > mm["rakip_gol"], "G",
                      np.where(mm["takim_gol"] == mm["rakip_gol"], "B", "M")),
    }), mm


def sonuc_renk(v):
    return {"G": f"color: {YESIL_YAZI}; font-weight: 600", "M": "color: #b3261e; font-weight: 600"}.get(v, "")


st.markdown("#### Son maçlar")
k1, k2 = st.columns(2, gap="large")
for kol, ad, db_ad, at_, ye_ in ((k1, r["Ev"], r["ev_db"], "Ev at", "Ev ye"), (k2, r["Dep"], r["dep_db"], "Dep at", "Dep ye")):
    with kol:
        if not isinstance(db_ad, str):
            st.markdown(f"**{ad}**")
            st.info("Bu takımın geçmiş maçı veritabanında yok (Maçkolik/Ofsayt'ta bulunamadı ya da takım verisi çekilmedi).")
            continue
        tab, mm = son_mac_tablosu(db_ad)
        st.markdown(f"**{ad}** · attığı {r[at_]:.2f} · yediği {r[ye_]:.2f} (maç başı, son {len(tab)} maç)")
        st.dataframe(tab.style.map(sonuc_renk, subset=["S"]), hide_index=True, width="stretch",
                     height=36 * (len(tab) + 1) + 4)

# ---- istatistik karşılaştırması
GOSTER = ["gol", "expectedGoals", "totalShotsOnGoal", "shotsOnGoal", "cornerKicks", "ballPossession",
          "kart", "fouls", "offsides", "goalkeeperSaves"]
satirlar = []
for stat in GOSTER:
    yap, yen = kolonlar(stat)
    if yap not in df.columns:
        continue
    deger = {}
    for taraf, db_ad in (("ev", r["ev_db"]), ("dep", r["dep_db"])):
        if not isinstance(db_ad, str):
            continue
        mm = son_maclar(df, db_ad, n, None, stat)
        if len(mm):
            deger[taraf] = (mm[yap].mean(), mm[yen].mean(), len(mm))
    if not deger:
        continue
    bicim = "{:.0f}" if stat == "ballPossession" else "{:.2f}"

    def h(taraf, i):
        return bicim.format(deger[taraf][i]) if taraf in deger else "–"

    satirlar.append({"İstatistik": ISTATISTIKLER[stat][0],
                     f"{r['Ev']} yaptığı": h("ev", 0), f"{r['Ev']} yediği": h("ev", 1),
                     f"{r['Dep']} yaptığı": h("dep", 0), f"{r['Dep']} yediği": h("dep", 1),
                     "Maç": "/".join(str(deger[x][2]) if x in deger else "0" for x in ("ev", "dep"))})
if satirlar:
    st.markdown(f"#### İstatistik ortalamaları (son {n} maç)")
    st.dataframe(pd.DataFrame(satirlar), hide_index=True, width="stretch")
    st.caption("Maç: ortalamaya giren maç sayısı (ev/dep). İstatistikler yalnızca Ofsayt'tan çekilen maçlarda var; "
               "o takımlarda yalnızca gol satırı görünür.")

b1, b2, _ = st.columns([1, 1, 2])
if b1.button("Baremlerini göster", icon="🎯", disabled=not r["Barem"]):
    st.session_state["bt_arama"] = r["Ev"]
    st.switch_page("barem_tarayici.py")
if isinstance(r["ev_db"], str) and isinstance(r["dep_db"], str) and b2.button("Maç Analizi'nde aç", icon="📊"):
    from tahmin import ana_lig
    l1, l2 = ana_lig(df, r["ev_db"]), ana_lig(df, r["dep_db"])
    st.session_state["ma_lig"] = l1 if l1 == l2 else "Tüm ligler"
    st.session_state["ma_ev"] = r["ev_db"]
    st.session_state["ma_dep"] = r["dep_db"]
    st.switch_page("mac_analizi.py")
