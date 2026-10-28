"""Voleybol: iddaa'daki voleybol maçları ve oranlarının takımların son maçlarındaki karşılığı."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import voleybol as vb

TZ = ZoneInfo("Europe/Istanbul")
ISABET, ISKA, CIZGI = "#1baf7a", "#c3c2b7", "#eb6834"
YESIL_YAZI, SARI_ZEMIN, SARI_YAZI = "#0a7a3f", "#fdecc8", "#8a5a00"
AYARLAR = Path(__file__).resolve().parent / "ayarlar.json"


def ayar_oku():
    try:
        return json.loads(AYARLAR.read_text(encoding="utf-8")).get("voleybol", {})
    except Exception:
        return {}


def ayar_yaz(**k):
    try:
        tum = json.loads(AYARLAR.read_text(encoding="utf-8"))
    except Exception:
        tum = {}
    tum.setdefault("voleybol", {}).update(k)
    try:
        AYARLAR.write_text(json.dumps(tum, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


@st.cache_data(ttl=1800, show_spinner="iddaa'daki voleybol ligleri alınıyor…")
def lig_listesi(gun):
    return vb.lig_ozeti(gun)


@st.cache_data(show_spinner="Son maçlar oranlarla karşılaştırılıyor…")
def tablolar(damga, n):
    bulten, sec, maclar = vb.verileri_yukle()
    return vb.secenek_tablosu(bulten, sec, maclar, n), vb.mac_tablosu(bulten, maclar, n), maclar


def damga_al():
    son = vb.son_guncelleme()
    if not son:
        return ""
    with sqlite3.connect(vb.DB) as con:
        n = con.execute("SELECT COUNT(*) FROM maclar").fetchone()[0]
    return f"{son['cekilme']}|{n}"


# ---------------- Başlık ve güncelleme ----------------
ust_sol, ust_sag = st.columns([4, 1], vertical_alignment="bottom")
with ust_sol:
    st.title("Voleybol")
with ust_sag:
    from ortam import KAPALI_YARDIM, guncelleme_acik
    pencere = st.popover("Baremleri güncelle", icon="🔄", width="stretch", disabled=not guncelleme_acik(),
                         help=None if guncelleme_acik() else KAPALI_YARDIM)
with pencere:
    ayar = ayar_oku()
    secenek = {0: "Bugün", 1: "24 saat", 2: "2 gün", 3: "3 gün", 7: "7 gün"}
    gun = st.segmented_control("Hangi maçlar", list(secenek), format_func=secenek.get,
                               default=ayar.get("gun", 0) if ayar.get("gun", 0) in secenek else 0, key="vb_gun")
    gun = 0 if gun is None else gun
    tum_ligler = st.toggle("iddaa'daki tüm voleybol ligleri", ayar.get("tum_ligler", True), key="vb_tum")
    secili_ci = None
    if not tum_ligler:
        try:
            ozet = lig_listesi(gun)
        except Exception as e:
            ozet = None
            st.warning(f"iddaa'daki ligler alınamadı: {e}")
        if ozet is not None and len(ozet):
            kayitli = [c for c in ayar.get("ligler", []) if c in set(ozet["ci"])]
            ulke_mac = ozet.groupby("ulke_adi", sort=False)["mac"].sum()
            ulkeler = st.multiselect(
                "Ülkeler", list(ulke_mac.index), format_func=lambda u: f"{u} ({ulke_mac[u]} maç)",
                default=sorted(set(ozet.loc[ozet["ci"].isin(kayitli), "ulke_adi"]), key=list(ulke_mac.index).index),
                key="vb_ulkeler", placeholder="Ülke seçin (ör. Türkiye, Polonya)")
            aday = ozet[ozet["ulke_adi"].isin(ulkeler)] if ulkeler else ozet
            etiket = {r.ci: f"{r.ad} ({r.mac} maç)" for r in aday.itertuples()}
            if "vb_ligler" in st.session_state:
                st.session_state["vb_ligler"] = [c for c in st.session_state["vb_ligler"] if c in etiket]
                varsayilan = None
            else:
                varsayilan = [c for c in kayitli if c in etiket]
            secili_ligler = st.multiselect(
                "Ligler", list(etiket), default=varsayilan, format_func=etiket.get, key="vb_ligler",
                placeholder="Boş bırakılırsa seçili ülkelerin tüm ligleri" if ulkeler else "Lig seçin")
            secili_ci = list(secili_ligler) or (list(aday["ci"]) if ulkeler else [])
            st.caption(f"Seçim: {len(secili_ci)} lig · {int(ozet.loc[ozet['ci'].isin(secili_ci), 'mac'].sum())} maç"
                       if secili_ci else "Çekmek istediğiniz ülkeleri ya da ligleri seçin.")
        elif ozet is not None:
            st.info("Bu dönemde iddaa'da voleybol maçı yok.")
    takim_verisi = st.checkbox("Takımların son 10 maçını Flashscore'dan çek", ayar.get("takim_verisi", True),
                               key="vb_takim", help="Takım başına 1 istek. Son 12 saatte çekilen takımlar ve "
                                                    "veritabanında olan maçlar tekrar indirilmez.")
    from ortam import guncelleme_izni
    izin = guncelleme_izni("vb")
    if st.button("Güncellemeyi başlat", type="primary", width="stretch", key="vb_basla",
                 disabled=not izin or not tum_ligler and not secili_ci):
        ayar_yaz(gun=gun, tum_ligler=tum_ligler, takim_verisi=takim_verisi,
                 **({"ligler": [int(c) for c in secili_ci]} if secili_ci else {}))
        st.session_state["vb_guncelleme"] = {"gun": gun, "ligler": "hepsi" if tum_ligler else secili_ci,
                                             "takim_verisi": takim_verisi}

if "vb_guncelleme" in st.session_state:
    g = st.session_state.pop("vb_guncelleme")
    with st.status("iddaa'dan voleybol maçları çekiliyor…", expanded=True) as durum:
        kutu, cubuk, satirlar = st.empty(), st.progress(0.0), []

        def yaz(m):
            satirlar.append(m)
            kutu.code("\n".join(satirlar[-25:]), language=None)

        def ilerle(i, n_):
            cubuk.progress(i / max(n_, 1), text=f"Takım verisi: {i}/{n_}")

        try:
            mac, adet = vb.guncelle(g["gun"], g["ligler"], yazdir=yaz, takim_verisi=g["takim_verisi"],
                                    ilerleme=ilerle)
            import bulut_kayit
            bulut_kayit.guncellemeyi_kaydet(["voleybol.db"], "voleybol", yaz)
            durum.update(label=f"{mac} maç, {adet} oran güncellendi", state="complete", expanded=False)
        except Exception as e:
            durum.update(label=f"Çekilemedi: {e}", state="error")
    st.cache_data.clear()

damga = damga_al()
if not damga:
    st.info("Henüz voleybol verisi yok. Sağ üstteki **Baremleri güncelle** penceresinden güncellemeyi başlatın.")
    st.stop()

# ---------------- Filtreler ----------------
with st.sidebar:
    st.header("Filtreler")
    n = st.slider("Kaç maça bakılsın", 3, 10, 10, key="vb_n")
    st_t, mt, maclar = tablolar(damga, n)
    ligler = sorted(mt["Lig"].dropna().unique()) if len(mt) else []
    sec_lig = st.multiselect("Ligler", ligler, placeholder="Tüm ligler", key="vb_lig")
    zaman = st.segmented_control("Ne zaman", ["Tümü", "Bugün", "Yarın"], default="Tümü", key="vb_zaman")
    baslamamis = st.toggle("Yalnızca başlamamış maçlar", True, key="vb_bas")
    st.subheader("Oran listesi")
    pazarlar = ["Maç sonucu", "Toplam sayı", "Toplam set", "Sayı handikapı", "Ev sahibi set alır",
                "Deplasman set alır"]
    sec_pazar = st.multiselect("Pazar", pazarlar, default=pazarlar, key="vb_pazar")
    form_ar = st.slider(f"Birleşik L{n} (%)", 0, 100, (0, 100), step=10, key="vb_form",
                        help="Ev sahibinin ve deplasmanın son maçlarındaki tutma oranlarının ortalaması")
    oran_ar = st.slider("Oran", 1.0, 10.0, (1.3, 10.0), step=0.05, key="vb_oran")
    siralama = st.selectbox("Sırala", ["Birleşik", "Değer", "Oran", "Tarih"], key="vb_sirala")

simdi = datetime.now(TZ).replace(tzinfo=None)


def zaman_filtresi(d):
    if not len(d):
        return d
    if sec_lig:
        d = d[d["Lig"].isin(sec_lig)]
    if baslamamis:
        d = d[d["Tarih"] > simdi]
    if zaman == "Bugün":
        d = d[d["Tarih"].dt.date == simdi.date()]
    elif zaman == "Yarın":
        d = d[d["Tarih"].dt.date == (simdi + pd.Timedelta(days=1)).date()]
    return d


ara_kol, _ = st.columns([2, 3])
arama = ara_kol.text_input("Ara", placeholder="Takım, maç ya da lig ara", label_visibility="collapsed",
                           key="vb_arama")


def yuzde(v):
    return "–" if pd.isna(v) else f"{100 * v:3.0f}%"


def yuzde_n(v, k):
    """Oran ve kaç maçtan hesaplandığı: ' 70% (8)'. Veri yoksa '–'."""
    if pd.isna(v):
        return "–"
    return f"{100 * v:3.0f}% ({int(k) if pd.notna(k) else 0})"


def oran_m(v):
    return "–" if pd.isna(v) else f"{v:5.2f}"


def sayi_m(v, b=1):
    return "–" if pd.isna(v) else f"{v:.{b}f}"


def renk(v):
    if pd.isna(v):
        return ""
    if v >= 0.7:
        return f"color: {YESIL_YAZI}; font-weight: 600"
    if v <= 0.3:
        return f"background-color: {SARI_ZEMIN}; color: {SARI_YAZI}"
    return ""


sekme_mac, sekme_oran = st.tabs(["Maç listesi", "Oranlar ve son maçlar"])
secim = None

# ================= Maç listesi =================
with sekme_mac:
    m = zaman_filtresi(mt.copy()) if len(mt) else mt
    if arama and len(m):
        m = m[(m["Maç"] + " " + m["Lig"]).str.contains(arama, case=False, regex=False)]
    m = m.sort_values("Tarih").reset_index(drop=True) if len(m) else m
    if m.empty:
        st.info("Bu filtrelerle maç yok.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Maç", len(m))
        c2.metric("Lig", m["Lig"].nunique())
        c3.metric("İki takımın da verisi var", int(((m["Ev n"] > 0) & (m["Dep n"] > 0)).sum()))
        from ortam import sayfala
        m = sayfala(m, "vb_mac").reset_index(drop=True)
        gor = pd.DataFrame({
            "Saat": m["Tarih"].dt.strftime("%d.%m %H:%M"), "Lig": m["Lig"], "Maç": m["Maç"],
            "MS 1": m["MS1"].map(oran_m), "MS 2": m["MS2"].map(oran_m),
            f"Ev G L{n}": [yuzde_n(v, k) for v, k in zip(m["Ev G"], m["Ev n"])],
            f"Dep G L{n}": [yuzde_n(v, k) for v, k in zip(m["Dep G"], m["Dep n"])],
            "Toplam sayı": m["Toplam sayı"].map(lambda v: "–" if pd.isna(v) else f"{v:g}"),
            "Ü oran": m["TS Ü oran"].map(oran_m),
            "Ü ev L": m["TS Ü ev"].map(yuzde), "Ü dep L": m["TS Ü dep"].map(yuzde),
            "Ort. toplam": m["Ort. toplam"].map(sayi_m),
            "3 set oran": m["3 set oran"].map(oran_m),
            "3 set ev/dep": [f"{yuzde(a).strip()} / {yuzde(b).strip()}" for a, b in zip(m["3 set ev"], m["3 set dep"])],
            "5 set ev/dep": [f"{yuzde(a).strip()} / {yuzde(b).strip()}" for a, b in zip(m["5 set ev"], m["5 set dep"])],
            "Handikap": m["Handikap"].map(lambda v: "–" if pd.isna(v) else f"{v:+g}"),
            "Ort. fark ev/dep": [f"{sayi_m(a)} / {sayi_m(b)}" for a, b in zip(m["Ev fark"], m["Dep fark"])],
            "Set al-ver ev": [f"{sayi_m(a)}-{sayi_m(b)}" for a, b in zip(m["Ev set"], m["Ev set ver"])],
            "Set al-ver dep": [f"{sayi_m(a)}-{sayi_m(b)}" for a, b in zip(m["Dep set"], m["Dep set ver"])],
            "Ev form": [f"{f} ({int(k)})" if isinstance(f, str) and f and pd.notna(k) else "–" for f, k in zip(m["Ev form"], m["Ev n"])],
            "Dep form": [f"{f} ({int(k)})" if isinstance(f, str) and f and pd.notna(k) else "–" for f, k in zip(m["Dep form"], m["Dep n"])],
            "Veri": [f"{a}/{b}" for a, b in zip(m["Ev n"], m["Dep n"])],
        })

        def renkler(_):
            css = pd.DataFrame("", index=gor.index, columns=gor.columns)
            for kol, kaynak in ((f"Ev G L{n}", "Ev G"), (f"Dep G L{n}", "Dep G"),
                                ("Ü ev L", "TS Ü ev"), ("Ü dep L", "TS Ü dep")):
                css[kol] = m[kaynak].map(renk)
            return css

        secim = st.dataframe(
            gor.style.apply(renkler, axis=None), hide_index=True, width="stretch",
            height=min(36 * (len(gor) + 1) + 4, 640), on_select="rerun", selection_mode="single-row",
            key="vb_mac_tablo",
            column_config={
                "Maç": st.column_config.TextColumn(width="medium"),
                "Toplam sayı": st.column_config.TextColumn(help="iddaa'nın toplam sayı Alt/Üst baremi"),
                "Ü ev L": st.column_config.TextColumn(help=f"Ev sahibinin son {n} maçında toplam sayının bu baremi "
                                                           "geçme oranı"),
                "Ü dep L": st.column_config.TextColumn(help=f"Deplasmanın son {n} maçında toplam sayının bu baremi "
                                                            "geçme oranı"),
                "3 set ev/dep": st.column_config.TextColumn(help=f"Son {n} maçın kaçı 3 sette bitti (ev / dep)"),
                "5 set ev/dep": st.column_config.TextColumn(help=f"Son {n} maçın kaçı 5 sete gitti (ev / dep)"),
                "Handikap": st.column_config.TextColumn(help="iddaa'nın ev sahibine verdiği sayı handikapı"),
                "Ort. fark ev/dep": st.column_config.TextColumn(help="Maç başına sayı farkı (attığı − yediği)"),
                "Set al-ver ev": st.column_config.TextColumn(help="Maç başına aldığı – verdiği set"),
                "Ev form": st.column_config.TextColumn(help="Son 5 maç (eskiden yeniye): G galibiyet, M mağlubiyet"),
                "Veri": st.column_config.TextColumn(help="Değerlendirilen maç sayısı (ev / deplasman)"),
            })
        st.caption("Satıra tıklayınca altta iki takımın son maçları ve grafikleri açılır. "
                   "Yeşil: %70 ve üstü, sarı: %30 ve altı.")

# ================= Oranlar =================
with sekme_oran:
    t = zaman_filtresi(st_t.copy()) if len(st_t) else st_t
    if len(t):
        t = t[t["Pazar"].isin(sec_pazar)]
        t = t[t["Birleşik"].between(form_ar[0] / 100, form_ar[1] / 100) | t["Birleşik"].isna()]
        t = t[t["Oran"].between(*oran_ar)]
        if arama:
            t = t[(t["Maç"] + " " + t["Lig"]).str.contains(arama, case=False, regex=False)]
        sira = {"Birleşik": ["Birleşik", "Değer"], "Değer": ["Değer"], "Oran": ["Oran"],
                "Tarih": ["Tarih"]}[siralama]
        t = t.sort_values(sira, ascending=siralama == "Tarih", na_position="last").reset_index(drop=True)
    if t.empty:
        st.info("Bu filtrelerle oran yok.")
    else:
        st.caption(f"{len(t)} seçenek · {t['event_id'].nunique()} maç · Ev L / Dep L: seçeneğin ev sahibinin ve "
                   f"deplasmanın son {n} maçında tutma oranı (takıma bağlı seçeneklerde rakibin maçları ters açıdan "
                   "okunur: ör. MS 1 için deplasmanın kaybettiği maçlar)")
        from ortam import sayfala
        t = sayfala(t, "vb_oran").reset_index(drop=True)
        gb = pd.DataFrame({
            "Maç": t["Maç"], "Tarih": t["Tarih"].dt.strftime("%d.%m %H:%M"), "Pazar": t["Pazar"],
            "Seçenek": t["Seçenek"], "Oran": t["Oran"].map(oran_m),
            f"Ev L{n}": [yuzde_n(v, k) for v, k in zip(t["Ev L"], t["Ev n"])],
            f"Dep L{n}": [yuzde_n(v, k) for v, k in zip(t["Dep L"], t["Dep n"])],
            "Birleşik": t["Birleşik"].map(yuzde),
            "Model": t["Model"].map(yuzde),
            "Adil (oran)": t["Adil"].map(yuzde),
            "Değer": t["Değer"].map(lambda v: "–" if pd.isna(v) else f"{100 * v:+.0f}%"),
            "Veri": [f"{a}/{b}" for a, b in zip(t["Ev n"], t["Dep n"])],
        })

        def renkler_o(_):
            css = pd.DataFrame("", index=gb.index, columns=gb.columns)
            for kol, kaynak in ((f"Ev L{n}", "Ev L"), (f"Dep L{n}", "Dep L"), ("Birleşik", "Birleşik"),
                                ("Model", "Model")):
                css[kol] = t[kaynak].map(renk)
            css["Değer"] = t["Değer"].map(lambda v: f"color: {YESIL_YAZI}; font-weight: 600"
                                          if pd.notna(v) and v > 0.05 else "")
            return css

        st.dataframe(gb.style.apply(renkler_o, axis=None), hide_index=True, width="stretch",
                     height=min(36 * (len(gb) + 1) + 4, 640),
                     column_config={
                         "Maç": st.column_config.TextColumn(width="medium"),
                         "Model": st.column_config.TextColumn(
                             help="Toplam sayıda: iki takımın maç toplamlarından normal dağılım; diğer pazarlarda "
                                  "birleşik oran"),
                         "Adil (oran)": st.column_config.TextColumn(help="iddaa oranının marjı ayıklanmış olasılığı. "
                                                                         "Toplam set Alt/Üst 3.5 ve 4.5, iddaa'nın "
                                                                         "3/4/5 set oranlarından hesaplanır."),
                         "Değer": st.column_config.TextColumn(help="Model × oran − 1"),
                     })

# ================= Seçilen maçın ayrıntısı =================
satir = secim.selection.rows if secim is not None and secim.selection else []
if not satir or m.empty or satir[0] >= len(m):
    st.stop()
r = m.iloc[satir[0]]
st.divider()
st.subheader(r["Maç"])
st.caption(f"{r['Lig']} · {r['Tarih']:%d.%m.%Y %H:%M}")
S = vb.Seriler(maclar, n)

k = st.columns(4)
k[0].metric("Ev sahibi kazanır (son maçlar)", yuzde(r["Model 1"]).strip(),
            delta=None if pd.isna(r["Adil 1"]) else f"iddaa %{100 * r['Adil 1']:.0f}", delta_color="off",
            help="Ev sahibinin galibiyet oranı ile deplasmanın mağlubiyet oranının ortalaması")
k[1].metric("Ort. toplam sayı", sayi_m(r["Ort. toplam"]),
            delta=None if pd.isna(r["Toplam sayı"]) else f"barem {r['Toplam sayı']:g}", delta_color="off")
k[2].metric(f"{r['Ev']} set (al–ver)", f"{sayi_m(r['Ev set'])}–{sayi_m(r['Ev set ver'])}")
k[3].metric(f"{r['Dep']} set (al–ver)", f"{sayi_m(r['Dep set'])}–{sayi_m(r['Dep set ver'])}")


def grafik(baslik, tid, deger, cizgi, ust_etiket="Üst", alt_etiket="Alt", format_=".0f"):
    mm = S.maclar(tid)
    if mm is None:
        st.markdown(f"**{baslik}**")
        st.info("Bu takımın geçmiş maçı yok (Flashscore'da eşleşmedi).")
        return
    d = mm.assign(Değer=deger(mm).values)
    d["Maç"] = d["tarih"].dt.strftime("%d.%m") + " " + d["rakip"] + d["saha"].map({"Ev": " (E)", "Deplasman": " (D)"})
    if pd.isna(cizgi):
        d["Sonuç"], alan, renkler_ = "–", ["–"], [ISKA]
    else:
        d["Sonuç"] = np.where(d["Değer"] > cizgi, ust_etiket, alt_etiket)
        alan, renkler_ = [ust_etiket, alt_etiket], [ISABET, ISKA]
    sira = list(d["Maç"])
    katman = alt.Chart(d).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("Maç:N", sort=sira, title=None, axis=alt.Axis(labelAngle=-45, labelLimit=150, labelFontSize=10)),
        y=alt.Y("Değer:Q", title=None),
        color=alt.Color("Sonuç:N", scale=alt.Scale(domain=alan, range=renkler_), legend=alt.Legend(orient="top", title=None)),
        tooltip=["Maç:N", alt.Tooltip("Değer:Q", format=format_), "Sonuç:N"]) + \
        alt.Chart(d).mark_text(dy=-8, fontSize=11, color="#52514e").encode(
            x=alt.X("Maç:N", sort=sira), y="Değer:Q", text=alt.Text("Değer:Q", format=format_))
    ek = ""
    if pd.notna(cizgi):
        katman += alt.Chart(pd.DataFrame({"y": [cizgi]})).mark_rule(
            color=CIZGI, strokeDash=[5, 4], strokeWidth=2).encode(y="y:Q")
        ek = f" · {(d['Değer'] > cizgi).sum()}/{len(d)} maçta {ust_etiket.lower()}"
    st.markdown(f"**{baslik}**{ek}")
    st.altair_chart(katman.properties(height=230), width="stretch")


h = r["Handikap"]
g1, g2 = st.columns(2, gap="large")
with g1:
    grafik(f"{r['Ev']} — maç toplam sayısı", r["ev_id"], lambda x: x["takim_sayi"] + x["rakip_sayi"], r["Toplam sayı"])
    grafik(f"{r['Ev']} — set sayısı", r["ev_id"], lambda x: x["set_sayisi"], 3.5, "4-5 set", "3 set")
    grafik(f"{r['Ev']} — sayı farkı (attığı − yediği)", r["ev_id"], lambda x: x["takim_sayi"] - x["rakip_sayi"],
           -h if pd.notna(h) else np.nan, "Handikapı geçti", "Geçemedi")
with g2:
    grafik(f"{r['Dep']} — maç toplam sayısı", r["dep_id"], lambda x: x["takim_sayi"] + x["rakip_sayi"], r["Toplam sayı"])
    grafik(f"{r['Dep']} — set sayısı", r["dep_id"], lambda x: x["set_sayisi"], 3.5, "4-5 set", "3 set")
    grafik(f"{r['Dep']} — sayı farkı (attığı − yediği)", r["dep_id"], lambda x: x["takim_sayi"] - x["rakip_sayi"],
           h if pd.notna(h) else np.nan, "Handikapı geçti", "Geçemedi")
st.caption("Turuncu kesikli çizgi: iddaa baremi. Sayı farkı grafiğinde çizgi, handikabı geçmek için gereken fark "
           "(ev sahibi için −handikap, deplasman için +handikap). (E) ev, (D) deplasman maçı.")

st.markdown("#### Son maçlar")
t1, t2 = st.columns(2, gap="large")
for kol, ad, tid in ((t1, r["Ev"], r["ev_id"]), (t2, r["Dep"], r["dep_id"])):
    with kol:
        mm = S.maclar(tid)
        st.markdown(f"**{ad}**")
        if mm is None:
            st.info("Veri yok.")
            continue
        mm = mm.iloc[::-1]
        setler = [", ".join(f"{int(a)}-{int(b)}" for a, b in
                            ((x[f"takim_s{i}"], x[f"rakip_s{i}"]) for i in vb.SETLER) if pd.notna(a) and pd.notna(b))
                  for _, x in mm.iterrows()]
        tab = pd.DataFrame({
            "Tarih": mm["tarih"].dt.strftime("%d.%m.%y"), "Turnuva": mm["lig"],
            "Rakip": mm["rakip"] + mm["saha"].map({"Ev": " (E)", "Deplasman": " (D)"}),
            "Set": mm["takim_set"].astype("Int64").astype(str) + "-" + mm["rakip_set"].astype("Int64").astype(str),
            "Setler": setler, "Toplam": (mm["takim_sayi"] + mm["rakip_sayi"]).astype("Int64"),
            "S": np.where(mm["takim_set"] > mm["rakip_set"], "G", "M"),
        })
        st.dataframe(tab.style.map(lambda v: {"G": f"color: {YESIL_YAZI}; font-weight: 600",
                                              "M": "color: #b3261e; font-weight: 600"}.get(v, ""), subset=["S"]),
                     hide_index=True, width="stretch", height=36 * (len(tab) + 1) + 4)
