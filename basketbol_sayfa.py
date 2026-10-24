"""Basketbol: iddaa'daki basketbol maçları, galibiyet oranları ve Alt/Üst baremlerinin son maçlardaki isabeti."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import basketbol as bk

TZ = ZoneInfo("Europe/Istanbul")
ISABET, ISKA, CIZGI = "#1baf7a", "#c3c2b7", "#eb6834"
YESIL_YAZI, SARI_ZEMIN, SARI_YAZI = "#0a7a3f", "#fdecc8", "#8a5a00"
AYARLAR = Path(__file__).resolve().parent / "ayarlar.json"


def ayar_oku():
    try:
        return json.loads(AYARLAR.read_text(encoding="utf-8")).get("basketbol", {})
    except Exception:
        return {}


def ayar_yaz(**k):
    try:
        tum = json.loads(AYARLAR.read_text(encoding="utf-8"))
    except Exception:
        tum = {}
    tum.setdefault("basketbol", {}).update(k)
    try:
        AYARLAR.write_text(json.dumps(tum, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


@st.cache_data(ttl=1800, show_spinner="iddaa'daki basketbol ligleri alınıyor…")
def lig_listesi(gun):
    return bk.lig_ozeti(gun)


@st.cache_data(show_spinner="Son maçlar baremlerle karşılaştırılıyor…")
def tablolar(damga, n):
    bulten, baremler, maclar = bk.verileri_yukle()
    bt = bk.barem_tablosu(bulten, baremler, maclar, n)
    mt = bk.mac_tablosu(bulten, bt, maclar, n)
    return bt, mt, maclar


def damga_al():
    son = bk.son_guncelleme()
    if not son:
        return ""
    import sqlite3
    with sqlite3.connect(bk.DB) as con:
        n = con.execute("SELECT COUNT(*) FROM maclar").fetchone()[0]
    return f"{son['cekilme']}|{n}"


# ---------------- Başlık ve güncelleme ----------------
ust_sol, ust_sag = st.columns([4, 1], vertical_alignment="bottom")
with ust_sol:
    st.title("Basketbol")
with ust_sag:
    from ortam import KAPALI_YARDIM, guncelleme_acik
    pencere = st.popover("Baremleri güncelle", icon="🔄", width="stretch", disabled=not guncelleme_acik(),
                         help=None if guncelleme_acik() else KAPALI_YARDIM)
with pencere:
    ayar = ayar_oku()
    secenek = {0: "Bugün", 1: "24 saat", 2: "2 gün", 3: "3 gün", 7: "7 gün"}
    gun = st.segmented_control("Hangi maçlar", list(secenek), format_func=secenek.get,
                               default=ayar.get("gun", 0) if ayar.get("gun", 0) in secenek else 0,
                               key="bb_gun")
    gun = 0 if gun is None else gun
    tum_ligler = st.toggle("iddaa'daki tüm basketbol ligleri", ayar.get("tum_ligler", True), key="bb_tum")
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
                key="bb_ulkeler", placeholder="Ülke seçin (ör. İtalya, İspanya)")
            aday = ozet[ozet["ulke_adi"].isin(ulkeler)] if ulkeler else ozet
            etiket = {r.ci: f"{r.ad} ({r.mac} maç)" for r in aday.itertuples()}
            if "bb_ligler" in st.session_state:
                st.session_state["bb_ligler"] = [c for c in st.session_state["bb_ligler"] if c in etiket]
                varsayilan = None
            else:
                varsayilan = [c for c in kayitli if c in etiket]
            secili_ligler = st.multiselect(
                "Ligler", list(etiket), default=varsayilan, format_func=etiket.get, key="bb_ligler",
                placeholder="Boş bırakılırsa seçili ülkelerin tüm ligleri" if ulkeler else "Lig seçin")
            secili_ci = list(secili_ligler) or (list(aday["ci"]) if ulkeler else [])
            if secili_ci:
                st.caption(f"Seçim: {len(secili_ci)} lig · "
                           f"{int(ozet.loc[ozet['ci'].isin(secili_ci), 'mac'].sum())} maç")
            else:
                st.caption("Çekmek istediğiniz ülkeleri ya da ligleri seçin.")
        elif ozet is not None:
            st.info("Bu dönemde iddaa'da basketbol maçı yok.")
    takim_verisi = st.checkbox("Takımların son 10 maçını Maçkolik'ten çek", ayar.get("takim_verisi", True),
                               key="bb_takim", help="Maçkolik'in günlük sonuçları arşivlenir: ilk seferde son 45 gün (gün başına 1 "
                                                    "istek), sonra yalnızca bugün ve dün.")
    if st.button("Güncellemeyi başlat", type="primary", width="stretch", key="bb_basla",
                 disabled=not tum_ligler and not secili_ci):
        ayar_yaz(gun=gun, tum_ligler=tum_ligler, takim_verisi=takim_verisi,
                 **({"ligler": [int(c) for c in secili_ci]} if secili_ci else {}))
        st.session_state["bb_guncelleme"] = {"gun": gun, "ligler": "hepsi" if tum_ligler else secili_ci,
                                             "takim_verisi": takim_verisi}

if "bb_guncelleme" in st.session_state:
    g = st.session_state.pop("bb_guncelleme")
    with st.status("iddaa'dan basketbol maçları çekiliyor…", expanded=True) as durum:
        kutu, cubuk, satirlar = st.empty(), st.progress(0.0), []

        def yaz(m):
            satirlar.append(m)
            kutu.code("\n".join(satirlar[-25:]), language=None)

        def ilerle(i, n_):
            cubuk.progress(i / max(n_, 1), text=f"Takım verisi: {i}/{n_}")

        try:
            mac, barem = bk.guncelle(g["gun"], g["ligler"], yazdir=yaz, takim_verisi=g["takim_verisi"],
                                     ilerleme=ilerle)
            durum.update(label=f"{mac} maç, {barem} barem güncellendi", state="complete", expanded=False)
        except Exception as e:
            durum.update(label=f"Çekilemedi: {e}", state="error")
    st.cache_data.clear()

damga = damga_al()
if not damga:
    st.info("Henüz basketbol verisi yok. Sağ üstteki **Baremleri güncelle** penceresinden güncellemeyi başlatın.")
    st.stop()

# ---------------- Filtreler ----------------
with st.sidebar:
    st.header("Filtreler")
    n = st.slider("Kaç maça bakılsın", 3, 10, 10, key="bb_n")
    bt, mt, maclar = tablolar(damga, n)
    ligler = sorted(mt["Lig"].dropna().unique()) if len(mt) else []
    sec_lig = st.multiselect("Ligler", ligler, placeholder="Tüm ligler", key="bb_lig")
    zaman = st.segmented_control("Ne zaman", ["Tümü", "Bugün", "Yarın"], default="Tümü", key="bb_zaman")
    baslamamis = st.toggle("Yalnızca başlamamış maçlar", True, key="bb_bas")
    st.subheader("Barem listesi")
    yon = st.segmented_control("Alt / Üst", ["İkisi", "Üst", "Alt"], default="İkisi", key="bb_yon")
    kapsam = st.multiselect("Kapsam", ["Maç toplamı", "Takım toplamı"], default=["Maç toplamı", "Takım toplamı"],
                            key="bb_kapsam")
    periyot = st.multiselect("Periyot", ["Maç", "İlk yarı"], default=["Maç"], key="bb_periyot")
    sadece_ana = st.toggle("Yalnızca ana barem", True, key="bb_ana",
                           help="iddaa her pazarda birkaç çizgi açar (ör. 169.5, 170.5, 171.5). Ana barem: Alt ve Üst "
                                "oranları birbirine en yakın olan çizgi.")
    form_ar = st.slider(f"Form L{n} (%)", 0, 100, (0, 100), step=10, key="bb_form")
    rakip_ar = st.slider(f"Rakip form L{n} (%)", 0, 100, (0, 100), step=10, key="bb_rakip")
    oran_ar = st.slider("Oran", 1.0, 5.0, (1.3, 5.0), step=0.05, key="bb_oran")
    siralama = st.selectbox("Sırala", ["Form", "Birleşik (form + rakip)", "Model", "Değer", "Oran", "Tarih"],
                            key="bb_sirala")

simdi = datetime.now(TZ).replace(tzinfo=None)


def zaman_filtresi(d):
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
                           key="bb_arama")


def yuzde(v):
    return "–" if pd.isna(v) else f"{100 * v:3.0f}%"


def yuzde_n(v, k):
    """Oran ve kaç maçtan hesaplandığı: ' 70% (8)'. Veri yoksa '–'."""
    if pd.isna(v):
        return "–"
    return f"{100 * v:3.0f}% ({int(k) if pd.notna(k) else 0})"


def oran_m(v):
    return "–" if pd.isna(v) else f"{v:5.2f}"


def sayi_m(v):
    return "–" if pd.isna(v) else f"{v:5.1f}"


def renk(v):
    if pd.isna(v):
        return ""
    if v >= 0.7:
        return f"color: {YESIL_YAZI}; font-weight: 600"
    if v <= 0.3:
        return f"background-color: {SARI_ZEMIN}; color: {SARI_YAZI}"
    return ""


sekme_mac, sekme_barem = st.tabs(["Maç listesi", "Baremler"])
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
        gor = pd.DataFrame({
            "Saat": m["Tarih"].dt.strftime("%d.%m %H:%M"), "Lig": m["Lig"], "Maç": m["Maç"],
            "MS 1": m["MS1"].map(oran_m), "MS 2": m["MS2"].map(oran_m),
            f"Ev G L{n}": [yuzde_n(v, k) for v, k in zip(m["Ev G"], m["Ev n"])],
            f"Dep G L{n}": [yuzde_n(v, k) for v, k in zip(m["Dep G"], m["Dep n"])],
            "Model 1": m["Model 1"].map(yuzde),
            "Toplam": m["Toplam"].map(lambda v: "–" if pd.isna(v) else f"{v:g}"),
            "Ü oran": m["Toplam Ü oran"].map(oran_m),
            "Ü ev L": m["Toplam Ev L"].map(yuzde), "Ü dep L": m["Toplam Dep L"].map(yuzde),
            "Bek. toplam": m["Bek. toplam"].map(sayi_m),
            "Ev barem": m["Ev barem"].map(lambda v: "–" if pd.isna(v) else f"{v:g}"),
            "Ev Ü L": m["Ev barem L"].map(yuzde), "Dep izin Ü": m["Ev barem rakip"].map(yuzde),
            "Dep barem": m["Dep barem"].map(lambda v: "–" if pd.isna(v) else f"{v:g}"),
            "Dep Ü L": m["Dep barem L"].map(yuzde), "Ev izin Ü": m["Dep barem rakip"].map(yuzde),
            "Ev form": [f"{f} ({int(k)})" if isinstance(f, str) and f and pd.notna(k) else "–" for f, k in zip(m["Ev form"], m["Ev n"])],
            "Dep form": [f"{f} ({int(k)})" if isinstance(f, str) and f and pd.notna(k) else "–" for f, k in zip(m["Dep form"], m["Dep n"])],
            "Veri": [f"{a}/{b}" for a, b in zip(m["Ev n"], m["Dep n"])],
        })

        def renkler(_):
            css = pd.DataFrame("", index=gor.index, columns=gor.columns)
            for kol, kaynak in ((f"Ev G L{n}", "Ev G"), (f"Dep G L{n}", "Dep G"), ("Model 1", "Model 1"),
                                ("Ü ev L", "Toplam Ev L"), ("Ü dep L", "Toplam Dep L"),
                                ("Ev Ü L", "Ev barem L"), ("Dep izin Ü", "Ev barem rakip"),
                                ("Dep Ü L", "Dep barem L"), ("Ev izin Ü", "Dep barem rakip")):
                css[kol] = m[kaynak].map(renk)
            return css

        secim = st.dataframe(
            gor.style.apply(renkler, axis=None), hide_index=True, width="stretch",
            height=min(36 * (len(gor) + 1) + 4, 640), on_select="rerun", selection_mode="single-row",
            key="bb_mac_tablo",
            column_config={
                "Maç": st.column_config.TextColumn(width="medium"),
                f"Ev G L{n}": st.column_config.TextColumn(help=f"Ev sahibinin son {n} maçında galibiyet oranı"),
                "Model 1": st.column_config.TextColumn(help="Basit modele göre ev sahibinin kazanma olasılığı"),
                "Toplam": st.column_config.TextColumn(help="iddaa'nın ana toplam sayı baremi (uzatmalar dahil)"),
                "Ü ev L": st.column_config.TextColumn(help=f"Ev sahibinin son {n} maçında maç toplamının bu baremi "
                                                           "geçme oranı"),
                "Ü dep L": st.column_config.TextColumn(help=f"Deplasmanın son {n} maçında maç toplamının bu baremi "
                                                            "geçme oranı"),
                "Ev barem": st.column_config.TextColumn(help="iddaa'nın ev sahibi takım sayısı ana baremi"),
                "Ev Ü L": st.column_config.TextColumn(help=f"Ev sahibinin son {n} maçta bu baremin üstünde sayı "
                                                           "attığı maç oranı"),
                "Dep izin Ü": st.column_config.TextColumn(help=f"Deplasmanın son {n} maçta rakiplerine bu baremin "
                                                               "üstünde sayı yediği maç oranı"),
                "Ev form": st.column_config.TextColumn(help="Son 5 maç (eskiden yeniye): G galibiyet, M mağlubiyet"),
                "Veri": st.column_config.TextColumn(help="Veritabanındaki maç sayısı (ev / deplasman)"),
            })
        st.caption("Satıra tıklayınca altta iki takımın son maçları ve barem grafikleri açılır. "
                   "Yeşil: %70 ve üstü, sarı: %30 ve altı.")

# ================= Baremler =================
with sekme_barem:
    t = zaman_filtresi(bt.copy()) if len(bt) else bt
    if len(t):
        if yon in ("Üst", "Alt"):
            t = t[t["Yön"] == yon]
        t = t[t["Kapsam"].isin(kapsam) & t["Periyot"].isin(periyot)]
        if sadece_ana:
            t = t[t["Ana"]]
        t = t[t["Form"].between(form_ar[0] / 100, form_ar[1] / 100) | t["Form"].isna()]
        t = t[t["Rakip form"].between(rakip_ar[0] / 100, rakip_ar[1] / 100) | t["Rakip form"].isna()]
        t = t[t["Oran"].between(*oran_ar)]
        if arama:
            t = t[(t["Maç"] + " " + t["Lig"]).str.contains(arama, case=False, regex=False)]
        sira = {"Form": ["Form", "Rakip form"], "Birleşik (form + rakip)": ["Birleşik", "Form"],
                "Model": ["Model"],
                "Değer": ["Değer"], "Oran": ["Oran"], "Tarih": ["Tarih"]}[siralama]
        t = t.sort_values(sira, ascending=siralama == "Tarih", na_position="last").reset_index(drop=True)
    if t.empty:
        st.info("Bu filtrelerle barem yok.")
    else:
        st.caption(f"{len(t)} barem · {t['event_id'].nunique()} maç · Form: takımın son {n} maçında baremi tutturma "
                   "oranı (maç toplamında ev sahibinin maçları, rakip formda deplasmanın maçları)")
        gb = pd.DataFrame({
            "Takım": t["Takım"], "Maç": t["Maç"], "Tarih": t["Tarih"].dt.strftime("%d.%m %H:%M"),
            "Barem": t["Barem"],
            "Kapsam": t["Kapsam"] + t["Periyot"].map({"Maç": "", "İlk yarı": " · İY"}),
            "Oran": t["Oran"].map(oran_m),
            f"Form L{n}": [yuzde_n(v, k) for v, k in zip(t["Form"], t["Maç sayısı"])],
            f"Rakip L{n}": [yuzde_n(v, k) for v, k in zip(t["Rakip form"], t["Rakip maç sayısı"])],
            "Model": t["Model"].map(yuzde),
            "Adil (oran)": t["Adil"].map(yuzde),
            "Değer": t["Değer"].map(lambda v: "–" if pd.isna(v) else f"{100 * v:+.0f}%"),
            "Ort.": t["Ort."].map(sayi_m), "Rakip ort.": t["Rakip ort."].map(sayi_m),
            "Bek.": t["Beklenen"].map(sayi_m),
        })

        def renkler_b(_):
            css = pd.DataFrame("", index=gb.index, columns=gb.columns)
            for kol, kaynak in ((f"Form L{n}", "Form"), (f"Rakip L{n}", "Rakip form"), ("Model", "Model")):
                css[kol] = t[kaynak].map(renk)
            css["Değer"] = t["Değer"].map(lambda v: f"color: {YESIL_YAZI}; font-weight: 600"
                                          if pd.notna(v) and v > 0.05 else "")
            return css

        st.dataframe(gb.style.apply(renkler_b, axis=None), hide_index=True, width="stretch",
                     height=min(36 * (len(gb) + 1) + 4, 640),
                     column_config={
                         f"Rakip L{n}": st.column_config.TextColumn(
                             help="Takım toplamında: rakibin son maçlarda rakiplerine bu baremin üstünde/altında "
                                  "sayı yedirme oranı. Maç toplamında: deplasmanın kendi maç toplamları."),
                         "Model": st.column_config.TextColumn(help="Basit model: takımın attığı ile rakibin yediğinin "
                                                                   "ortalaması, son maçlardaki saçılımla normal dağılım"),
                         "Adil (oran)": st.column_config.TextColumn(help="iddaa oranının marjı ayıklanmış olasılığı"),
                         "Değer": st.column_config.TextColumn(help="Model olasılığı × oran − 1"),
                     })

# ================= Seçilen maçın ayrıntısı =================
satir = secim.selection.rows if secim is not None and secim.selection else []
if not satir or m.empty or satir[0] >= len(m):
    st.stop()
r = m.iloc[satir[0]]
st.divider()
st.subheader(r["Maç"])
st.caption(f"{r['Lig']} · {r['Tarih']:%d.%m.%Y %H:%M}")
S = bk.Seriler(maclar, n)

k = st.columns(4)
k[0].metric("Ev sahibi kazanır (model)", yuzde(r["Model 1"]).strip(),
            delta=None if pd.isna(r["Adil 1"]) else f"iddaa %{100 * r['Adil 1']:.0f}", delta_color="off")
k[1].metric("Beklenen toplam", sayi_m(r["Bek. toplam"]).strip(),
            delta=None if pd.isna(r["Toplam"]) else f"barem {r['Toplam']:g}", delta_color="off")
k[2].metric(f"{r['Ev']} ort. (attı / yedi)",
            "–" if pd.isna(r["Ev at"]) else f"{r['Ev at']:.1f} / {r['Ev ye']:.1f}")
k[3].metric(f"{r['Dep']} ort. (attı / yedi)",
            "–" if pd.isna(r["Dep at"]) else f"{r['Dep at']:.1f} / {r['Dep ye']:.1f}")


def grafik(baslik, tid, tur, cizgi):
    mm = S.maclar(tid)
    if mm is None:
        st.markdown(f"**{baslik}**")
        st.info("Bu takımın geçmiş maçı yok (Maçkolik'te eşleşmedi ya da arşivde maçı yok).")
        return
    s = S.seri(tid, tur)
    d = mm.assign(Değer=s.values)
    d["Maç"] = d["tarih"].dt.strftime("%d.%m") + " " + d["rakip"] + d["saha"].map({"Ev": " (E)", "Deplasman": " (D)"})
    if pd.isna(cizgi):
        d["Sonuç"] = "Barem yok"
        alan, renkler_ = ["Barem yok"], [ISKA]
    else:
        d["Sonuç"] = np.where(d["Değer"] > cizgi, "Üst", "Alt")
        alan, renkler_ = ["Üst", "Alt"], [ISABET, ISKA]
    sira = list(d["Maç"])
    bar = alt.Chart(d).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("Maç:N", sort=sira, title=None, axis=alt.Axis(labelAngle=-45, labelLimit=150, labelFontSize=10)),
        y=alt.Y("Değer:Q", title=None, scale=alt.Scale(zero=False)),
        color=alt.Color("Sonuç:N", scale=alt.Scale(domain=alan, range=renkler_), legend=alt.Legend(orient="top", title=None)),
        tooltip=["Maç:N", alt.Tooltip("Değer:Q", format=".0f"), "Sonuç:N"])
    etiket = alt.Chart(d).mark_text(dy=-8, fontSize=11, color="#52514e").encode(
        x=alt.X("Maç:N", sort=sira), y="Değer:Q", text=alt.Text("Değer:Q", format=".0f"))
    katman = bar + etiket
    ust = ""
    if pd.notna(cizgi):
        katman += alt.Chart(pd.DataFrame({"y": [cizgi]})).mark_rule(
            color=CIZGI, strokeDash=[5, 4], strokeWidth=2).encode(y="y:Q")
        ust = f" · barem {cizgi:g}: {(d['Değer'] > cizgi).sum()}/{len(d)} maçta üst"
    st.markdown(f"**{baslik}**{ust}")
    st.altair_chart(katman.properties(height=240), width="stretch")


g1, g2 = st.columns(2, gap="large")
with g1:
    grafik(f"{r['Ev']} — maç toplamları", r["ev_id"], "toplam", r["Toplam"])
    grafik(f"{r['Ev']} — attığı sayı", r["ev_id"], "atti", r["Ev barem"])
    grafik(f"{r['Ev']} — yediği sayı", r["ev_id"], "yedi", r["Dep barem"])
with g2:
    grafik(f"{r['Dep']} — maç toplamları", r["dep_id"], "toplam", r["Toplam"])
    grafik(f"{r['Dep']} — attığı sayı", r["dep_id"], "atti", r["Dep barem"])
    grafik(f"{r['Dep']} — yediği sayı", r["dep_id"], "yedi", r["Ev barem"])
st.caption("Turuncu kesikli çizgi: iddaa'nın ana baremi (uzatmalar dahil). Yediği sayı grafiğinde rakip takımın "
           "baremi gösterilir. (E) ev, (D) deplasman maçı.")

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
        tab = pd.DataFrame({
            "Tarih": mm["tarih"].dt.strftime("%d.%m.%y"), "Turnuva": mm["lig"],
            "Rakip": mm["rakip"] + mm["saha"].map({"Ev": " (E)", "Deplasman": " (D)"}),
            "Skor": mm["takim_sayi"].astype("Int64").astype(str) + "-" + mm["rakip_sayi"].astype("Int64").astype(str)
                    + np.where(mm["uzatma"] == 1, " (uz.)", ""),
            "İY": mm["takim_iy"].astype("Int64").astype(str) + "-" + mm["rakip_iy"].astype("Int64").astype(str),
            "Toplam": (mm["takim_sayi"] + mm["rakip_sayi"]).astype("Int64"),
            "S": np.where(mm["takim_sayi"] > mm["rakip_sayi"], "G", "M"),
        })
        st.dataframe(tab.style.map(lambda v: {"G": f"color: {YESIL_YAZI}; font-weight: 600",
                                              "M": "color: #b3261e; font-weight: 600"}.get(v, ""), subset=["S"]),
                     hide_index=True, width="stretch", height=36 * (len(tab) + 1) + 4)
