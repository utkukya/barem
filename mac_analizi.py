"""
Maç istatistikleri ve tahmin arayüzü.
Çalıştırma:  streamlit run app.py
"""
import altair as alt
import pandas as pd
import streamlit as st

from ortak import veritabani_kontrol

from istatistikler import ISTATISTIKLER, PERIYOTLAR
from tahmin import DB, kolonlar, mevcut_istatistikler, tahmin_et, veri_yukle

MAVI, TURUNCU = "#2a78d6", "#eb6834"   # yaptığı / yediği



@st.cache_data
def yukle():
    return veri_yukle()


veritabani_kontrol()

df = yukle()
# Takımın ana ligi: en çok maç oynadığı lig (kupa maçları son maç olabilir)
ozet_takim = df.groupby("takim").agg(lig=("lig", lambda s: s.mode().iloc[0]), son=("tarih", "max"))
ozet_takim["lig_son"] = ozet_takim.groupby("lig")["son"].transform("max")
# Ligdeki son maçtan 60 günden eski verisi kalanlar küme düşmüş / ligden ayrılmış takımlar:
# yalnızca rakip olarak görünürler, seçim listesine alınmazlar (lig ortalamalarında kalırlar).
ozet_takim["guncel"] = (ozet_takim["lig_son"] - ozet_takim["son"]).dt.days <= 60

# ---------------- Kenar çubuğu: seçimler ----------------
with st.sidebar:
    st.header("Maç seçimi")
    TUMU = "Tüm ligler"
    ligler = [TUMU] + sorted(ozet_takim["lig"].unique())
    if st.session_state.get("ma_lig") not in ligler:
        st.session_state["ma_lig"] = ligler[1] if len(ligler) > 1 else TUMU
    lig = st.selectbox("Lig", ligler, key="ma_lig",
                       help="Farklı liglerden iki takımı karşılaştırmak için (ör. kupa maçı) 'Tüm ligler'i seçin.")
    if lig == TUMU:
        takimlar = sorted(ozet_takim[ozet_takim["guncel"]].index)
    else:
        takimlar = sorted(ozet_takim[(ozet_takim["lig"] == lig) & ozet_takim["guncel"]].index)
    # Varsayılanlar (Barem Tarayıcı'dan gelinirse orada seçilen maç kullanılır)
    if not takimlar:
        st.info("Henüz takım verisi yok. **Barem Tarayıcı** sayfasındaki **Baremleri güncelle** penceresinden "
                "güncellemeyi başlatın.")
        st.stop()
    if st.session_state.get("ma_ev") not in takimlar:
        st.session_state["ma_ev"] = takimlar[0]
    if st.session_state.get("ma_dep") not in takimlar:
        st.session_state["ma_dep"] = takimlar[1 if len(takimlar) > 1 else 0]
    ev = st.selectbox("Ev sahibi", takimlar, key="ma_ev")
    dep = st.selectbox("Deplasman", takimlar, key="ma_dep")

    st.header("İstatistik")
    periyot_ad = st.radio("Periyot", list(PERIYOTLAR), horizontal=True, key="ma_periyot")
    periyot = PERIYOTLAR[periyot_ad]
    secenekler = mevcut_istatistikler(df, periyot)
    if st.session_state.get("ma_stat") not in secenekler:
        st.session_state["ma_stat"] = "totalShotsOnGoal" if "totalShotsOnGoal" in secenekler else secenekler[0]
    anahtar = st.selectbox("İstatistik", secenekler,
                           format_func=lambda k: ISTATISTIKLER[k][0], key="ma_stat")

    st.header("Ayarlar")
    n = st.slider("Son kaç maç", 3, 10, 10)
    saha_ayrimi = st.checkbox("Ev sahibi için yalnızca ev, deplasman için yalnızca deplasman maçları",
                              help="Örneklem küçülür; 10 maçlık veride dikkatli kullanın.")
    cekim = st.slider("Küçük örneklem düzeltmesi", 0.0, 5.0, 2.0, 0.5,
                      help="Takım ortalamasını lig ortalamasına doğru çeker. "
                           "Değer = lig ortalamasına verilen 'sanal maç' sayısı. 0 = kapalı.")

if ev == dep:
    st.warning("Ev sahibi ve deplasman için farklı takımlar seçin.")
    st.stop()

etiket, tur = ISTATISTIKLER[anahtar]
baslik_ek = "" if periyot == "" else f" · {periyot_ad}"
st.title(f"{ev}  –  {dep}")
st.caption(f"{lig} · {etiket}{baslik_ek} · son {n} maç")

sonuc = tahmin_et(df, ev, dep, anahtar, periyot, n, saha_ayrimi, cekim)
if sonuc is None:
    st.warning("Seçilen takımlardan biri için bu istatistikte veri yok.")
    st.stop()


def yuzde(x):
    return f"%{100 * x:.0f}"


def sayi(x):
    return "–" if pd.isna(x) else (f"{x:.2f}" if tur != "sayac" or abs(x) < 10 else f"{x:.1f}")


# ---------------- Takımların son maçları ----------------
def takim_paneli(kolon, takim, maclar, ozet):
    yap, yen = kolonlar(anahtar, periyot)
    with kolon:
        st.subheader(takim)
        a, b, c = st.columns(3)
        a.metric(f"Ort. yaptığı", sayi(ozet["yaptigi"]))
        b.metric(f"Ort. rakibe izin verdiği", sayi(ozet["yedigi"]))
        c.metric("Maç", ozet["n"])

        m = maclar.copy()
        m["Maç"] = (m["tarih"].dt.strftime("%d.%m") + " " + m["rakip"]
                    + m["saha"].map({"Ev": " (E)", "Deplasman": " (D)"}))
        m["Skor"] = m["takim_gol"].astype(int).astype(str) + "-" + m["rakip_gol"].astype(int).astype(str)
        uzun = pd.concat([
            m.assign(Taraf=f"{takim}", Değer=m[yap]),
            m.assign(Taraf="Rakip", Değer=m[yen]),
        ])
        sira = list(m["Maç"])
        grafik = (
            alt.Chart(uzun)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Maç:N", sort=sira, title=None, axis=alt.Axis(labelAngle=-45, labelLimit=150, labelOverlap=False, labelFontSize=10)),
                xOffset=alt.XOffset("Taraf:N", sort=[takim, "Rakip"]),
                y=alt.Y("Değer:Q", title=etiket),
                color=alt.Color("Taraf:N", sort=[takim, "Rakip"],
                                scale=alt.Scale(range=[MAVI, TURUNCU]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=[alt.Tooltip("Maç:N"), alt.Tooltip("Skor:N"), alt.Tooltip("Taraf:N"),
                         alt.Tooltip("Değer:Q", title=etiket, format=".2f")],
            )
            .properties(height=300)
        )
        ort = (alt.Chart(pd.DataFrame({"y": [ozet["yaptigi"]]}))
               .mark_rule(color=MAVI, strokeDash=[4, 4], strokeWidth=2).encode(y="y:Q"))
        st.altair_chart(grafik + ort, width="stretch")

        with st.expander("Maç tablosu"):
            tablo = m[["tarih", "rakip", "saha", "Skor", yap, yen]].rename(columns={
                "tarih": "Tarih", "rakip": "Rakip", "saha": "Saha",
                yap: f"{takim}", yen: "Rakip değeri"})
            tablo["Tarih"] = tablo["Tarih"].dt.strftime("%d.%m.%Y")
            st.dataframe(tablo.iloc[::-1], hide_index=True, width="stretch")


sekme1, sekme2 = st.tabs([f"{etiket}", "Tüm istatistikler"])

with sekme1:
    k1, k2 = st.columns(2, gap="large")
    takim_paneli(k1, ev, sonuc["ev_maclar"], sonuc["ozet"]["ev"])
    takim_paneli(k2, dep, sonuc["dep_maclar"], sonuc["ozet"]["dep"])
    st.caption("Kesikli çizgi: takımın son maçlardaki ortalaması. (E) ev, (D) deplasman maçı.")

    # ---------------- Tahmin ----------------
    st.divider()
    st.header("Tahmin")
    a, b, c = st.columns(3)
    a.metric(f"{ev} beklenen", sayi(sonuc["bek_ev"]))
    b.metric(f"{dep} beklenen", sayi(sonuc["bek_dep"]))
    if tur == "sayac":
        lo, hi = sonuc["aralik_toplam"]
        c.metric("Maç toplamı", sayi(sonuc["bek_toplam"]))
        c.caption(f"Maçların %80'inde toplam {lo:.0f} ile {hi:.0f} arasında beklenir")

        st.subheader("Hangi taraf daha fazla?" if anahtar != "gol" else "Maç sonucu")
        x, y, z = st.columns(3)
        if anahtar == "gol":
            x.metric(f"1 · {ev}", yuzde(sonuc["p_ev_fazla"]))
            y.metric("X · Beraberlik", yuzde(sonuc["p_esit"]))
            z.metric(f"2 · {dep}", yuzde(sonuc["p_dep_fazla"]))
        else:
            x.metric(f"{ev} daha fazla", yuzde(sonuc["p_ev_fazla"]))
            y.metric("Eşit", yuzde(sonuc["p_esit"]))
            z.metric(f"{dep} daha fazla", yuzde(sonuc["p_dep_fazla"]))

        st.subheader("Alt / Üst olasılıkları")
        fmt = {"Çizgi": "{:.1f}", "Üst": lambda v: yuzde(v), "Alt": lambda v: yuzde(v)}
        t1, t2, t3 = st.columns(3)
        for kol, ad, tbl in ((t1, "Maç toplamı", sonuc["alt_ust_toplam"]),
                             (t2, ev, sonuc["alt_ust_ev"]), (t3, dep, sonuc["alt_ust_dep"])):
            kol.markdown(f"**{ad}**")
            kol.dataframe(tbl.style.format(fmt), hide_index=True, width="stretch")

        if sonuc["olasi_skorlar"] is not None:
            st.subheader("En olası sonuçlar")
            s = sonuc["olasi_skorlar"]
            st.dataframe(pd.DataFrame({"Sonuç": [f"{i}-{j}" for i, j in s.index],
                                       "Olasılık": [yuzde(v) for v in s.values]}).T,
                         width="stretch")
    else:
        c.metric("Fark", sayi(sonuc["bek_ev"] - sonuc["bek_dep"]))
        st.info("Bu istatistik sayılan bir olay değil (yüzde ya da ölçüm); "
                "tahmin iki takımın ortalamalarından hesaplandı, olasılık verilmedi.")

    with st.expander("Hesap nasıl yapıldı?"):
        L, e, d = sonuc["lig_ist"], sonuc["ozet"]["ev"], sonuc["ozet"]["dep"]
        st.markdown(f"""
**{lig} ortalaması ({etiket}):** genel {sayi(L['genel'])}, ev sahibi {sayi(L['ev'])}, deplasman {sayi(L['dep'])}

| | Yaptığı | Rakibe izin verdiği | Düzeltilmiş yaptığı | Düzeltilmiş izin verdiği |
|---|---|---|---|---|
| {ev} ({e['n']} maç) | {sayi(e['yaptigi'])} | {sayi(e['yedigi'])} | {sayi(e['yaptigi_duz'])} | {sayi(e['yedigi_duz'])} |
| {dep} ({d['n']} maç) | {sayi(d['yaptigi'])} | {sayi(d['yedigi'])} | {sayi(d['yaptigi_duz'])} | {sayi(d['yedigi_duz'])} |

- **{ev} beklenen** = lig ev ort. × ({ev} yaptığı ÷ lig ort.) × ({dep} izin verdiği ÷ lig ort.)
- **{dep} beklenen** = lig dep. ort. × ({dep} yaptığı ÷ lig ort.) × ({ev} izin verdiği ÷ lig ort.)
- Düzeltme: takım ortalaması, lig ortalamasına {cekim:g} sanal maç eklenerek hesaplandı.
- Olasılıklar: maçtan maça saçılım oranı {L['sacilim']:.2f} (1 = Poisson, üstü = negatif binom), 200.000 simülasyon.
- Yüzde istatistiklerinde iki tarafın ortalaması alınır; topa sahip olma gibi toplamı 100 olanlar 100'e ölçeklenir.
""")

# ---------------- Tüm istatistikler tablosu ----------------
with sekme2:
    st.caption(f"Her istatistik için aynı model: {ev} – {dep}, {periyot_ad.lower()}, son {n} maç.")
    satirlar = []
    for k in secenekler:
        s = tahmin_et(df, ev, dep, k, periyot, n, saha_ayrimi, cekim, simulasyon=0)
        if s is None:
            continue
        satirlar.append({
            "İstatistik": ISTATISTIKLER[k][0],
            f"{ev} ort.": s["ozet"]["ev"]["yaptigi"],
            f"{ev} izin verdiği": s["ozet"]["ev"]["yedigi"],
            f"{dep} ort.": s["ozet"]["dep"]["yaptigi"],
            f"{dep} izin verdiği": s["ozet"]["dep"]["yedigi"],
            f"{ev} beklenen": s["bek_ev"],
            f"{dep} beklenen": s["bek_dep"],
            "Toplam beklenen": s["bek_ev"] + s["bek_dep"] if s["tur"] == "sayac" else None,
        })
    tum = pd.DataFrame(satirlar)
    st.dataframe(tum.style.format(precision=2, na_rep="–"), hide_index=True,
                 width="stretch", height=min(38 * (len(tum) + 1), 1400))
    st.download_button("CSV olarak indir", tum.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                       file_name=f"{ev}_{dep}_tahmin.csv", mime="text/csv")
