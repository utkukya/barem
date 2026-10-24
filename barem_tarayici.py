"""Barem Tarayıcı: iddaa baremlerinin takımların son maçlarındaki isabet oranları."""
from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from ortak import veritabani_kontrol

from barem import PAZAR_ETIKET, SURUM, baremleri_yukle, izin_serisi, seriler, tablo_olustur
from tahmin import DB, kolonlar, son_maclar, veri_yukle

TZ = ZoneInfo("Europe/Istanbul")
ISABET, ISKA, CIZGI = "#1baf7a", "#c3c2b7", "#eb6834"
YESIL_YAZI, SARI_ZEMIN, SARI_YAZI = "#0a7a3f", "#fdecc8", "#8a5a00"


@st.cache_data(show_spinner=False)
def mac_verisi():
    return veri_yukle()


@st.cache_data(show_spinner="Son maçlar baremlerle karşılaştırılıyor…")
def isabet_tablosu(n, cekim, damga, surum):
    # surum: barem.py'deki hesaplama sürümü; dosya güncellenince önbellek kendiliğinden yenilenir
    return tablo_olustur(mac_verisi(), baremleri_yukle(), n, cekim)


def damga_al():
    b = baremleri_yukle()
    return "" if b.empty else str(b["cekilme"].max())


veritabani_kontrol()

# ---------------- Başlık ve güncelleme ----------------
import json
from pathlib import Path

AYARLAR = Path(__file__).resolve().parent / "ayarlar.json"


def ayar_oku():
    try:
        return json.loads(AYARLAR.read_text(encoding="utf-8"))
    except Exception:
        return {}


def ayar_yaz(**k):
    a = ayar_oku()
    a.update(k)
    try:
        AYARLAR.write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


@st.cache_data(ttl=1800, show_spinner="iddaa'daki ligler alınıyor…")
def lig_listesi(gun):
    from iddaa_baremler import lig_ozeti
    return lig_ozeti(gun)


ust_sol, ust_sag = st.columns([4, 1], vertical_alignment="bottom")
with ust_sol:
    st.title("Barem Tarayıcı")
with ust_sag:
    pencere = st.popover("Baremleri güncelle", icon="🔄", width="stretch")
with pencere:
    from iddaa_baremler import LIGLER, VARSAYILAN_LIGLER, guncelle
    ayar = ayar_oku()
    secenek = {0: "Bugün", 1: "24 saat", 2: "2 gün", 3: "3 gün", 7: "7 gün"}
    gun = st.segmented_control("Hangi maçlar", list(secenek), format_func=secenek.get,
                               default=ayar.get("gun", 0) if ayar.get("gun", 0) in secenek else 0, key="gn_gun")
    gun = 0 if gun is None else gun
    tum_ligler = st.toggle("iddaa'daki tüm ligler", ayar.get("tum_ligler", True), key="gn_tum",
                           help="Bültendeki tüm futbol maçlarını tarar.")
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
                default=sorted(set(ozet.loc[ozet["ci"].isin(kayitli), "ulke_adi"]),
                               key=list(ulke_mac.index).index),
                key="gn_ulkeler", placeholder="Ülke seçin (ör. İngiltere, Almanya)")
            aday = ozet[ozet["ulke_adi"].isin(ulkeler)] if ulkeler else ozet
            etiket = {r.ci: f"{r.ad} ({r.mac} maç)" for r in aday.itertuples()}
            # Ülke seçimi değişince listede olmayan ligleri seçimden çıkar (Streamlit hata vermesin)
            if "gn_ligler" in st.session_state:
                st.session_state["gn_ligler"] = [c for c in st.session_state["gn_ligler"] if c in etiket]
                varsayilan = None
            else:
                varsayilan = [c for c in kayitli if c in etiket]
            secili_ligler = st.multiselect(
                "Ligler", list(etiket), default=varsayilan, format_func=etiket.get, key="gn_ligler",
                placeholder="Boş bırakılırsa seçili ülkelerin tüm ligleri" if ulkeler else "Lig seçin",
                help="Ülke seçip ligleri boş bırakırsanız o ülkelerin iddaa'daki tüm ligleri çekilir "
                     "(ör. İngiltere → Premier Lig, Championship, League One...).")
            secili_ci = list(secili_ligler) or (list(aday["ci"]) if ulkeler else [])
            if secili_ci:
                mac = int(ozet.loc[ozet["ci"].isin(secili_ci), "mac"].sum())
                st.caption(f"Seçim: {len(secili_ci)} lig · {mac} maç")
            else:
                st.caption("Çekmek istediğiniz ülkeleri ya da ligleri seçin.")
    modlar = {"akilli": "Akıllı", "skor": "Yalnızca skor", "tam": "Tam istatistik", "yok": "Çekme"}
    eski = ayar.get("takim_verisi", "akilli")
    eski = "akilli" if eski is True else "yok" if eski is False else eski
    takim_verisi = st.segmented_control(
        "Takım verisi", list(modlar), format_func=modlar.get,
        default=eski if eski in modlar else "akilli", key="gn_takim",
        help="Goller ve ilk yarı skorları tüm maçlar için Maçkolik'in günlük sonuç arşivinden gelir (ilk seferde "
             "son 45 gün, gün başına 1 istek; sonra yalnızca bugün ve dün). "
             "Yalnızca skor: Ofsayt'a hiç istek gitmez. "
             "Akıllı: iddaa'nın korner/kart/şut pazarı açtığı maçlarda ve büyük liglerde istatistikler Ofsayt.com'dan "
             "çekilir. Tam: tüm maçlarda Ofsayt istatistikleri (uzun sürer). "
             "Son 12 saatte çekilen takımlar tekrar indirilmez.") or "akilli"
    if st.button("Güncellemeyi başlat", type="primary", width="stretch", key="gn_basla",
                 disabled=not tum_ligler and not secili_ci):
        ayar_yaz(gun=gun, tum_ligler=tum_ligler, takim_verisi=takim_verisi,
                 **({"ligler": [int(c) for c in secili_ci]} if secili_ci else {}))
        st.session_state["guncelleme"] = {"gun": gun, "ligler": "hepsi" if tum_ligler else secili_ci,
                                          "takim_verisi": takim_verisi}

if "guncelleme" in st.session_state:
    g = st.session_state.pop("guncelleme")
    with st.status("iddaa.com'dan baremler çekiliyor…", expanded=True) as durum:
        kutu = st.empty()
        satirlar = []

        def yaz(m):
            satirlar.append(m)
            kutu.code("\n".join(satirlar[-25:]), language=None)

        cubuk = st.progress(0.0)

        def ilerle(_asama, i, n_):
            cubuk.progress(i / max(n_, 1), text=f"Takım verisi: {i}/{n_}")

        try:
            adet, eslesmeyen, _ = guncelle(g["gun"], yazdir=yaz, ligler=g["ligler"] or None,
                                           takim_verisi=g["takim_verisi"], ilerleme=ilerle)
            durum.update(label=f"{adet} barem güncellendi", state="complete", expanded=bool(eslesmeyen))
        except Exception as e:
            durum.update(label=f"Çekilemedi: {e}", state="error")
    st.cache_data.clear()  # maç verisi de değişmiş olabilir

damga = damga_al()
if not damga:
    from iddaa_baremler import son_guncelleme
    son = son_guncelleme()
    if son is None:
        st.info("Henüz barem verisi yok. Sağ üstteki **Baremleri güncelle** penceresinden ligleri seçip "
                "güncellemeyi başlatın.")
    else:
        st.info(f"Son güncelleme ({son['cekilme']}): önümüzdeki {son['gun']} günde seçili liglerde barem bulunamadı "
                f"(5 büyük lig: {son['lig_mac']} maç, diğer ligler: {son['kupa_mac']} maç).\n\n"
                "Avrupa kupası haftalarında iddaa, hafta sonunun lig maçlarını genellikle salı-çarşamba "
                "gününe kadar bültene eklemiyor. Birkaç gün sonra tekrar güncelleyin.")
    st.stop()

# ---------------- Filtreler ----------------
with st.sidebar:
    st.header("Filtreler")
    n = st.slider("Kaç maça bakılsın", 3, 10, 10, key="bt_n")
    tum = isabet_tablosu(n, 2.0, damga, SURUM)
    if tum.empty:
        st.warning("Eşleşen barem yok.")
        st.stop()

    ligler = sorted(tum["Lig"].unique())
    sec_lig = st.multiselect("Ligler", ligler, placeholder="Tüm ligler", key="bt_lig")
    zaman = st.segmented_control("Ne zaman", ["Tümü", "Bugün", "Yarın"], default="Tümü", key="bt_zaman")

    st.subheader("İsabet oranı")
    yon = st.segmented_control("Alt / Üst", ["İkisi", "Üst", "Alt"], default="İkisi", key="bt_yon")
    form_ar = st.slider(f"Form L{n} (%)", 0, 100, (0, 100), step=10, key="bt_form")
    rakip_ar = st.slider(f"Rakip form L{n} (%)", 0, 100, (0, 100), step=10, key="bt_rakip",
                         help="Rakibin son maçlarında rakiplerine bu baremi geçirme oranı "
                              "(Alt için: altında tutma oranı). Maç toplamında rakibin kendi maç toplamları.")
    st.subheader("Pazar")
    kapsam = st.multiselect("Kapsam", ["Takım toplamı", "Maç toplamı"],
                            default=["Takım toplamı", "Maç toplamı"], key="bt_kapsam")
    periyot = st.multiselect("Periyot", ["Maç", "İlk yarı", "İkinci yarı"], default=["Maç"], key="bt_periyot")
    kaynak = st.segmented_control("Barem kaynağı", ["iddaa", "Sistem", "Tümü"], default="iddaa", key="bt_kaynak",
                                  help="iddaa: iddaa'nın açtığı baremler ve oranları. Şut, isabetli şut, taç, faul, "
                                       "aut ve kurtarışta iddaa 'Toplam 31+' gibi eşik pazarı açar; bunlar Üst 30.5 "
                                       "olarak listelenir (Alt tarafı satılmaz). Bu pazarlar genelde yalnızca büyük "
                                       "maçlarda açılır. "
                                       "Sistem: iddaa'nın pazar açmadığı maçlar için beklentinin çevresinde "
                                       "üretilen baremler (oranı yok).")
    oran_ar = st.slider("Oran (iddaa baremleri)", 1.0, 10.0, (1.3, 10.0), step=0.05, key="bt_oran")

    st.subheader("Sıralama")
    siralama = st.selectbox("Sırala", ["Form", "Birleşik (form + rakip)", "Model", "Değer", "Oran", "Tarih"],
                            key="bt_sirala")

# ---------------- Arama ve pazar sekmeleri ----------------
ara_kol, _ = st.columns([2, 3])
arama = ara_kol.text_input("Ara", placeholder="Takım ya da maç ara", label_visibility="collapsed", key="bt_arama")
pazarlar = ["Tüm pazarlar"] + [p for p in PAZAR_ETIKET.values() if p in set(tum["Pazar"])]
pazar = st.segmented_control("Pazar", pazarlar, default="Tüm pazarlar", label_visibility="collapsed",
                             key="bt_pazar")

t = tum.copy()
if sec_lig:
    t = t[t["Lig"].isin(sec_lig)]
bugun = datetime.now(TZ).date()
if zaman == "Bugün":
    t = t[t["Tarih"].dt.date == bugun]
elif zaman == "Yarın":
    t = t[t["Tarih"].dt.date == bugun + pd.Timedelta(days=1)]
if yon in ("Üst", "Alt"):
    t = t[t["Yön"] == yon]
t = t[t["Form"].between(form_ar[0] / 100, form_ar[1] / 100)]
t = t[t["Rakip form"].between(rakip_ar[0] / 100, rakip_ar[1] / 100) | t["Rakip form"].isna()]
t = t[t["Kapsam"].isin(kapsam) & t["Periyot"].isin(periyot)]
t = t[t["Oran"].between(*oran_ar) | t["Oran"].isna()]
t = t[t["Oran"].round(2) != 1.00]  # oranı 1.00 olan seçenekler tabloda gösterilmez
if kaynak in ("iddaa", "Sistem"):
    t = t[t["Kaynak"] == kaynak]
if pazar and pazar != "Tüm pazarlar":
    t = t[t["Pazar"] == pazar]
if arama:
    t = t[t["Maç"].str.contains(arama, case=False, regex=False)]

sira_kolonu = {"Form": ["Form", "Rakip form"], "Birleşik (form + rakip)": ["Birleşik", "Form"],
               "Model": ["Model"], "Değer": ["Değer"], "Oran": ["Oran"], "Tarih": ["Tarih"]}[siralama]
t = t.sort_values(sira_kolonu, ascending=siralama == "Tarih", na_position="last").reset_index(drop=True)

if t.empty:
    if kaynak == "iddaa" and pazar and pazar != "Tüm pazarlar":
        st.info(f"Bu filtrelerle iddaa'nın açtığı **{pazar}** baremi yok. iddaa şut, isabetli şut, taç, faul, "
                "aut ve kurtarış pazarlarını genellikle yalnızca büyük maçlarda açar. "
                "Diğer maçlar için sol menüden **Barem kaynağı: Sistem** seçebilirsiniz.")
    else:
        st.info("Bu filtrelerle eşleşen barem yok.")
    st.stop()

st.caption(f"{len(t)} barem · {t['event_id'].nunique()} maç · iddaa verisi: {damga} · "
           f"Form: takımın son {n} maçında baremi tutturma oranı")

# ---------------- Tablo ----------------
def yuzde_metin(v):
    # Boşluk dolgulu ("  0%", " 90%", "100%"): başlığa tıklayınca metin sıralaması da doğru olur; "–" en sona düşer
    return "–" if pd.isna(v) else f"{100 * v:3.0f}%"


def yuzde_n(v, k):
    """Oran ve kaç maçtan hesaplandığı: ' 70% (8)'. Veri yoksa '–'."""
    if pd.isna(v):
        return "–"
    return f"{100 * v:3.0f}% ({int(k) if pd.notna(k) else 0})"


def sayi_metin(v):
    return "–" if pd.isna(v) else f"{v:5.1f}"


def deger_metin(v):
    return "–" if pd.isna(v) else f"{100 * v:+.0f}%"


gorunum = pd.DataFrame({
    "Takım": t["Takım"],
    "Maç": t["Maç"],
    "Tarih": t["Tarih"].dt.strftime("%d.%m %H:%M"),
    "Barem": t["Barem"],
    "Pazar": t["Pazar"],
    "Kapsam": t["Kapsam"] + t["Periyot"].map({"Maç": "", "İlk yarı": " · İY", "İkinci yarı": " · 2Y"}),
    "Oran": t["Oran"].map(lambda v: "–" if pd.isna(v) else f"{v:5.2f}"),
    f"Form L{n}": [yuzde_n(v, k) for v, k in zip(t["Form"], t["Maç sayısı"])],
    # Kupa maçlarında rakip veritabanında olmayabilir: bu sütunlar metin, boşlar "–"
    f"Rakip form L{n}": [yuzde_n(v, k) for v, k in zip(t["Rakip form"], t["Rakip maç sayısı"])],
    "Model": t["Model"].map(yuzde_metin),
    "Adil (oran)": t["Adil"].map(yuzde_metin),
    "Model oranı": t["Model oranı"].map(lambda v: "–" if pd.isna(v) else f"{v:5.2f}"),
    "Değer": t["Değer"].map(deger_metin),
    "Ort.": t["Ort."],
    "Rakip izin ort.": t["Rakip izin ort."].map(sayi_metin),
    "Kaynak": t["Kaynak"],
})


def renk(v):
    if pd.isna(v):
        return ""
    if v >= 0.7:
        return f"color: {YESIL_YAZI}; font-weight: 600"
    if v <= 0.5:
        return f"background-color: {SARI_ZEMIN}; color: {SARI_YAZI}"
    return ""


def deger_renk(v):
    if pd.isna(v):
        return ""
    return f"color: {YESIL_YAZI}; font-weight: 600" if v > 0.05 else ("color: #9a9890" if v < -0.05 else "")


def renkler(_):
    # Renkler metne değil, t'deki sayısal değerlere göre
    css = pd.DataFrame("", index=gorunum.index, columns=gorunum.columns)
    for kol, kaynak in ((f"Form L{n}", "Form"), (f"Rakip form L{n}", "Rakip form"), ("Model", "Model")):
        css[kol] = t[kaynak].map(renk)
    css["Değer"] = t["Değer"].map(deger_renk)
    return css


stil = (gorunum.style
        .format({"Ort.": "{:.1f}"})
        .apply(renkler, axis=None))

secim = st.dataframe(
    stil, hide_index=True, width="stretch", height=min(36 * (len(gorunum) + 1) + 4, 640),
    on_select="rerun", selection_mode="single-row", key="bt_tablo",
    column_config={
        "Takım": st.column_config.TextColumn(width="medium"),
        "Maç": st.column_config.TextColumn(width="medium"),
        "Oran": st.column_config.TextColumn(help="iddaa oranı. Sistem baremlerinde oran yok (–)."),
        "Model oranı": st.column_config.TextColumn(help="Model olasılığına göre adil oran (1 ÷ olasılık). "
                                                        "Başka bir sitede bundan yüksek oran varsa değerli olabilir."),
        "Kaynak": st.column_config.TextColumn(help="iddaa: iddaa'nın baremi · Sistem: bizim ürettiğimiz barem"),
        f"Form L{n}": st.column_config.TextColumn(help=f"Takımın son {n} maçında bu baremi tutturma oranı. "
                                                       "Parantez içi: verisi olan maç sayısı."),
        f"Rakip form L{n}": st.column_config.TextColumn(help="Rakibin son maçlarında bu barem kadar kullandırtma oranı. "
                                                             "Parantez içi: verisi olan maç sayısı."),
        "Model": st.column_config.Column(help="Tahmin modelinin olasılığı (takım gücü × rakip zaafı, lig ortalamasına göre)"),
        "Adil (oran)": st.column_config.Column(help="iddaa oranının ima ettiği olasılık. Alt/Üst pazarlarında marj "
                                                    "ayıklanmış; tek taraflı eşik pazarlarında (N+) 1 ÷ oran."),
        "Değer": st.column_config.Column(help="Model olasılığı × oran − 1. Pozitifse oran, modele göre yüksek."),
        "Ort.": st.column_config.Column(help="Takımın son maçlardaki ortalaması (maç toplamında maç toplamı)"),
    },
)
st.caption("Satıra tıklayınca altta son maçların barem karşısındaki grafiği açılır. "
           "Yeşil: %70 ve üstü, sarı: %50 ve altı.")

# ---------------- Seçilen satırın ayrıntısı ----------------
satir_sec = secim.selection.rows if secim and secim.selection else []
if not satir_sec:
    st.stop()
r = t.iloc[satir_sec[0]]
df = mac_verisi()
st.divider()
st.subheader(f"{r['Takım'] if r['kapsam'] != 'toplam' else r['Maç']} · {r['Pazar']} "
             f"{'Üst' if r['Yön'] == 'Üst' else 'Alt'} {r['Çizgi']:g}")
kaynak_metin = (r["iddaa pazarı"] if r["Kaynak"] == "iddaa"
                else "Sistem baremi (iddaa bu pazarı açmıyor)")
oran_metin = "" if pd.isna(r["Oran"]) else f" · oran {r['Oran']:.2f}"
st.caption(f"{r['Maç']} · {r['Tarih']:%d.%m.%Y %H:%M} · {r['Kapsam']} · {r['Periyot']} · "
           f"{kaynak_metin}{oran_metin}")

m1, m2, m3, m4 = st.columns(4)
m1.metric(f"Form L{n}", f"%{100 * r['Form']:.0f}", delta=f"{int(r['Maç sayısı'])} maç", delta_color="off")
m2.metric(f"Rakip form L{n}", "–" if pd.isna(r["Rakip form"]) else f"%{100 * r['Rakip form']:.0f}",
          delta=None if pd.isna(r["Rakip form"]) else f"{int(r['Rakip maç sayısı'])} maç", delta_color="off")
m3.metric("Model", "–" if pd.isna(r["Model"]) else f"%{100 * r['Model']:.0f}",
          help=f"Beklenen değer: {r['Beklenen']:.2f}")
if pd.notna(r["Adil"]):
    tek_tarafli = "+" in str(r["iddaa pazarı"])
    m4.metric("iddaa olasılığı (1 ÷ oran)" if tek_tarafli else "iddaa adil olasılık", f"%{100 * r['Adil']:.0f}",
              delta=None if pd.isna(r["Değer"]) else f"değer {100 * r['Değer']:+.0f}%",
              delta_color="normal")
else:
    m4.metric("Model oranı", "–" if pd.isna(r["Model oranı"]) else f"{r['Model oranı']:.2f}",
              help="iddaa bu pazarı açmadığı için oran yok; modelin olasılığına göre adil oran.")


def barem_grafigi(baslik, takim, seri, maclar, cizgi, yon):
    d = maclar.assign(Değer=seri.values)
    d["Maç"] = (d["tarih"].dt.strftime("%d.%m") + " " + d["rakip"]
                + d["saha"].map({"Ev": " (E)", "Deplasman": " (D)"}))
    d["Sonuç"] = (d["Değer"] > cizgi) if yon == "Üst" else (d["Değer"] < cizgi)
    d["Sonuç"] = d["Sonuç"].map({True: "Tuttu", False: "Tutmadı"})
    sira = list(d["Maç"])
    bar = (alt.Chart(d).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
           .encode(x=alt.X("Maç:N", sort=sira, title=None,
                           axis=alt.Axis(labelAngle=-45, labelLimit=150, labelOverlap=False, labelFontSize=10)),
                   y=alt.Y("Değer:Q", title=None),
                   color=alt.Color("Sonuç:N", scale=alt.Scale(domain=["Tuttu", "Tutmadı"], range=[ISABET, ISKA]),
                                   legend=alt.Legend(orient="top", title=None)),
                   tooltip=["Maç:N", alt.Tooltip("Değer:Q", format=".2f"), "Sonuç:N"]))
    etiket = (alt.Chart(d).mark_text(dy=-8, fontSize=11, color="#52514e")
              .encode(x=alt.X("Maç:N", sort=sira), y="Değer:Q", text=alt.Text("Değer:Q", format=".0f")))
    kural = (alt.Chart(pd.DataFrame({"y": [cizgi]}))
             .mark_rule(color=CIZGI, strokeDash=[5, 4], strokeWidth=2).encode(y="y:Q"))
    tutan = (d["Sonuç"] == "Tuttu").sum()
    st.markdown(f"**{baslik}** · {tutan}/{len(d)} maçta tuttu")
    st.altair_chart((bar + etiket + kural).properties(height=260), width="stretch")


if r["stat"] == "goalkeeperSaves":
    etiket_yaptigi = "kalecisinin kurtarışları"
    etiket_izin = "hücumunun rakip kalecilere yaptırdığı kurtarış"
else:
    etiket_yaptigi, etiket_izin = "yaptığı", "rakiplerine izin verdiği"
g1, g2 = st.columns(2, gap="large")
def _db(v):
    return v if isinstance(v, str) and v else None


ev_db, dep_db, rakip_db = _db(r["ev_db"]), _db(r["dep_db"]), _db(r["rakip_db"])
takim_db = ev_db if r["kapsam"] == "ev" else dep_db if r["kapsam"] == "dep" else (ev_db or dep_db)
kendi, kendi_m, _ = seriler(df, takim_db, r["stat"], r["periyot"], n, r["kapsam"])
with g1:
    barem_grafigi(f"{r['Takım']} — {'maç toplamları' if r['kapsam'] == 'toplam' else etiket_yaptigi}",
                  r["Takım"], kendi, kendi_m, r["Çizgi"], r["Yön"])
with g2:
    if rakip_db:
        rakip_m = son_maclar(df, rakip_db, n, None, r["stat"], r["periyot"])
        izin = izin_serisi(df, rakip_db, r["stat"], r["periyot"], n, r["kapsam"])
        barem_grafigi(f"{r['Rakip']} — "
                      f"{'maç toplamları' if r['kapsam'] == 'toplam' else etiket_izin}",
                      r["Rakip"], izin, rakip_m, r["Çizgi"], r["Yön"])
    else:
        st.markdown(f"**{r['Rakip']}**")
        st.info("Bu takımın geçmiş maçı veritabanında yok (Maçkolik/Ofsayt'ta bulunamadı ya da takım verisi "
                "çekilmedi); rakip formu ve model hesaplanamadı.")
st.caption("Turuncu kesikli çizgi: barem. (E) ev, (D) deplasman maçı. "
           "Kupa maçlarında takımların son lig maçlarına bakılır.")


if ev_db and dep_db and st.button("Bu maçı Maç Analizi'nde aç", icon="📊"):
    from tahmin import ana_lig
    l1, l2 = ana_lig(df, ev_db), ana_lig(df, dep_db)
    st.session_state["ma_lig"] = l1 if l1 == l2 else "Tüm ligler"
    st.session_state["ma_ev"] = ev_db
    st.session_state["ma_dep"] = dep_db
    st.session_state["ma_stat"] = r["stat"]
    st.session_state["ma_periyot"] = {"": "Tüm maç", "IY_": "İlk yarı", "2Y_": "İkinci yarı"}[r["periyot"]]
    st.switch_page("mac_analizi.py")
