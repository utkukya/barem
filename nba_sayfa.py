"""NBA: oyuncu istatistikleri, pozisyon eşleşmesi (hücum × savunma) ve pozisyona göre takım sıralamaları."""
import html as _html
import sqlite3
from datetime import datetime

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import nba

EV_RENK, DEP_RENK = "#e23b4e", "#f2c200"
ISABET, ISKA, CIZGI = "#1baf7a", "#c3c2b7", "#eb6834"
YESIL, BEYAZ, KIRMIZI = (31, 122, 109), (238, 236, 230), (201, 72, 62)


def damga_al():
    if not nba.DB.exists():
        return ""
    try:
        with sqlite3.connect(nba.DB) as con:
            n = con.execute("SELECT COUNT(*) FROM nba_kutu").fetchone()[0]
            m = con.execute("SELECT COUNT(DISTINCT mac_id) FROM nba_kutu").fetchone()[0]
            try:
                b = con.execute("SELECT deger FROM nba_meta WHERE anahtar = 'bulten'").fetchone()
            except sqlite3.Error:
                b = None
            try:
                p = con.execute("SELECT MAX(cekilme), COUNT(*) FROM nba_prop").fetchone()
                b = ((b[0] if b else "") + f"|{p[0]}|{p[1]}",)
            except sqlite3.Error:
                pass
    except sqlite3.Error:
        return ""
    return f"{n}|{m}|{b[0] if b else ''}" if n else ""


@st.cache_data(show_spinner="NBA verisi hazırlanıyor…")
def veri(damga):
    return nba.verileri_yukle()


@st.cache_data(show_spinner=False)
def poz_tablosu(damga, stat, n):
    _, kutu, _, _ = veri(damga)
    return nba.pozisyon_tablosu(kutu, stat, n)


# ---------------- Başlık ve güncelleme ----------------
ust_sol, ust_sag = st.columns([4, 1], vertical_alignment="bottom")
with ust_sol:
    st.title("NBA")
with ust_sag:
    from ortam import KAPALI_YARDIM, guncelleme_acik
    pencere = st.popover("NBA verisini güncelle", icon="🔄", width="stretch", disabled=not guncelleme_acik(),
                         help=None if guncelleme_acik() else KAPALI_YARDIM)
with pencere:
    st.caption("Kaynak Maçkolik: yeni maçların kutu skorları (maç başına 1 istek), takım kadroları ve pozisyonlar "
               "(3 günde bir, takım başına 1 istek). Daha önce çekilen maçlar tekrar indirilmez.")
    bulten_gun = st.segmented_control("iddaa bülteni", [0, 1, 2], default=1,
                                      format_func={0: "Bugün", 1: "24 saat", 2: "2 gün"}.get, key="nba_gun")
    hazirlik = st.checkbox("Hazırlık maçlarını da al", False, key="nba_hazirlik",
                           help=f"{nba.SEZON_BASLANGIC} öncesi NBA maçları (hazırlık) varsayılan olarak alınmaz.")
    kadro_zorla = st.checkbox("Kadroları şimdi yenile", False, key="nba_kadro")
    prop_al = st.checkbox("Oyuncu baremlerini global piyasadan çek", True, key="nba_prop_al",
                          help="Oynanacak maçlarda oyuncuların sayı, ribaund, asist, 3 sayılık... Alt/Üst çizgileri ve "
                               "oranları; Kambi'nin (Unibet altyapısı) ücretsiz oran servisinden. Maç başına 1 istek.")
    if st.button("Güncellemeyi başlat", type="primary", width="stretch", key="nba_basla"):
        st.session_state["nba_guncelleme"] = {"gun": 1 if bulten_gun is None else bulten_gun,
                                              "hazirlik": hazirlik, "kadro": kadro_zorla, "prop": prop_al}

if "nba_guncelleme" in st.session_state:
    g = st.session_state.pop("nba_guncelleme")
    with st.status("NBA verisi güncelleniyor…", expanded=True) as durum:
        kutu_, cubuk, satirlar = st.empty(), st.progress(0.0), []

        def yaz(m):
            satirlar.append(m)
            kutu_.code("\n".join(satirlar[-25:]), language=None)

        def ilerle(asama, i, n_):
            cubuk.progress(i / max(n_, 1), text=f"{'Kadro' if asama == 'kadro' else 'Kutu skor'}: {i}/{n_}")

        try:
            nba.guncelle(yazdir=yaz, ilerleme=ilerle, hazirlik=g["hazirlik"], kadro_zorla=g["kadro"])
            try:
                nba.bulten_guncelle(g["gun"], yazdir=yaz)
            except Exception as e:
                yaz(f"iddaa bülteni alınamadı: {e}")
            if g.get("prop"):
                try:
                    nba.baremleri_guncelle(yazdir=yaz)
                except Exception as e:
                    yaz(f"Oyuncu baremleri alınamadı: {e}")
            durum.update(label="NBA verisi güncellendi", state="complete", expanded=False)
        except Exception as e:
            durum.update(label=f"Güncellenemedi: {e}", state="error")
    st.cache_data.clear()

damga = damga_al()
if not damga:
    st.info("Henüz NBA verisi yok. Sağ üstteki **NBA verisini güncelle** penceresinden başlatın "
            "(ilk seferde sezonun tüm maçlarının kutu skorları ve 30 takımın kadrosu çekilir).")
    st.stop()

maclar, kutu, kadro, bulten = veri(damga)

with st.sidebar:
    st.header("Filtreler")
    donem_ad = {0: "Tüm sezon", 5: "Son 5 maç", 10: "Son 10 maç", 15: "Son 15 maç"}
    donem = st.segmented_control("Dönem", list(donem_ad), default=0, format_func=donem_ad.get, key="nba_donem")
    n = (donem or None)
    stat = st.selectbox("Pozisyon tabloları için istatistik", ["sayi", "rib", "ast", "uc_i", "pra"],
                        format_func=lambda k: nba.STATLAR[k][0], key="nba_stat")
    st.caption(f"{kutu['mac_id'].nunique()} maç · {kutu['oyuncu_id'].nunique()} oyuncu · "
               f"son maç {kutu['tarih'].max():%d.%m.%Y}")
    tahmini = kutu.loc[kutu["poz_tahmini"], "oyuncu"].nunique()
    if tahmini:
        st.caption(f"{tahmini} oyuncu kadroda bulunamadı; pozisyonu istatistiklerinden tahmin edildi.")

pt = poz_tablosu(damga, stat, n)
stat_ad = nba.STATLAR[stat][0]

sekme_tarama, sekme_kart, sekme_deger, sekme_eslesme, sekme_sira, sekme_oyuncu = st.tabs(
    ["🎯 Barem tarayıcı", "📈 Oyuncu kartı", "💰 Değer (EV)", "⚔️ Pozisyon eşleşme", "🏆 Pozisyon sıralaması",
     "👤 Oyuncular"])


@st.cache_data(show_spinner="Oyuncu baremleri değerlendiriliyor…")
def deger(damga, n, agirlik):
    _, kutu_, _, _ = veri(damga)
    return nba.deger_tablosu(nba.props_yukle(), kutu_, n or 10, agirlik)


# ================================================================ yardımcılar
def sira_renk(sira, toplam=30):
    """1 = yeşil, orta = açık, son = kırmızı."""
    if pd.isna(sira):
        return "#eeeeee"
    t = (sira - 1) / max(toplam - 1, 1)
    a, b, u = (YESIL, BEYAZ, t * 2) if t <= 0.5 else (BEYAZ, KIRMIZI, (t - 0.5) * 2)
    r, g_, b_ = (int(a[i] + (b[i] - a[i]) * u) for i in range(3))
    return f"rgb({r},{g_},{b_})"


def yazi_renk(sira, toplam=30):
    t = (sira - 1) / max(toplam - 1, 1) if pd.notna(sira) else 0.5
    return "#ffffff" if t < 0.18 or t > 0.82 else "#1d1d1d"


def e(s):
    return _html.escape(str(s))


def takim_id(ad):
    t = kutu.drop_duplicates("takim_id").set_index("takim")["takim_id"]
    return t.get(ad)


def eslesme_karti(ev, dep, baslik):
    """Hücum × savunma kartı (HTML)."""
    toplam = len(pt)
    ev_id, dep_id = takim_id(ev), takim_id(dep)

    def satirlar(hucum_takim, savunma_takim, renk_h, renk_s, h_sol):
        out = []
        for p in nba.POZLAR:
            hv, hs = pt.loc[hucum_takim, ("hucum", p)], pt.loc[hucum_takim, ("hucum_sira", p)]
            sv, ss = pt.loc[savunma_takim, ("savunma", p)], pt.loc[savunma_takim, ("savunma_sira", p)]
            avantaj = (ss - hs) / max(toplam - 1, 1)  # >0: hücum üstün, <0: savunma üstün
            gen = f"{min(abs(avantaj), 1) * 100:.0f}%"
            hucum_bar = f'<div class="bar" style="width:{gen};background:{renk_h}"></div>' if avantaj > 0 else ""
            sav_bar = f'<div class="bar" style="width:{gen};background:{renk_s}"></div>' if avantaj < 0 else ""
            sol = (f'<div class="deger">{hv:.1f}</div><div class="sira">#{hs:.0f}</div>', hucum_bar)
            sag = (f'<div class="sira">#{ss:.0f}</div><div class="deger">{sv:.1f}</div>', sav_bar)
            if not h_sol:
                sol, sag = (f'<div class="deger">{sv:.1f}</div><div class="sira">#{ss:.0f}</div>', sav_bar), \
                           (f'<div class="sira">#{hs:.0f}</div><div class="deger">{hv:.1f}</div>', hucum_bar)
            out.append(f'<div class="satir"><div class="uc sol">{sol[0]}</div>'
                       f'<div class="iz sol-iz">{sol[1]}</div><div class="poz">{p}</div>'
                       f'<div class="iz">{sag[1]}</div><div class="uc sag">{sag[0]}</div></div>')
        return "".join(out)

    def kayit(tid):
        return (f"<b>{nba.kayit(kutu, tid)}</b> &nbsp;EVİNDE {nba.kayit(kutu, tid, 'Ev')} · "
                f"DEPLASMANDA {nba.kayit(kutu, tid, 'Dep')}")

    css = f"""
    <style>
    .nba-kart {{background:#0d0d0f;color:#f2f2f2;border-radius:10px;overflow:hidden;font-family:system-ui,
      -apple-system,'Segoe UI',sans-serif;max-width:980px}}
    .nba-kart .ust {{display:flex;justify-content:space-between;padding:8px 16px;background:#fff;color:#111;
      font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}}
    .nba-kart .bas {{display:grid;grid-template-columns:1fr 64px 1fr;align-items:center}}
    .nba-kart .ev {{background:{EV_RENK};padding:18px 20px;clip-path:polygon(0 0,100% 0,92% 100%,0 100%)}}
    .nba-kart .dep {{background:#14284b;padding:18px 20px;text-align:right;
      clip-path:polygon(8% 0,100% 0,100% 100%,0 100%)}}
    .nba-kart .etiket {{font-size:11px;letter-spacing:.15em;opacity:.85;text-transform:uppercase}}
    .nba-kart .ad {{font-size:34px;font-weight:900;font-style:italic;line-height:1.05;text-transform:uppercase}}
    .nba-kart .dep .ad {{color:{DEP_RENK}}}
    .nba-kart .kayit {{font-size:12px;letter-spacing:.06em;opacity:.9;margin-top:4px}}
    .nba-kart .vs {{width:52px;height:52px;border-radius:50%;background:#111;border:3px solid #fff;display:flex;
      align-items:center;justify-content:center;font-weight:900;font-style:italic;margin:auto}}
    .nba-kart .govde {{padding:6px 18px 14px}}
    .nba-kart .alt-baslik {{font-size:11px;letter-spacing:.2em;color:#9a9a9a;margin:14px 0 4px;
      text-transform:uppercase}}
    .nba-kart .grup {{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid #333;
      padding:6px 0}}
    .nba-kart .grup .b {{font-size:21px;font-weight:900;font-style:italic;text-transform:uppercase}}
    .nba-kart .grup .k {{font-size:10px;letter-spacing:.18em;color:#9a9a9a;text-transform:uppercase}}
    .nba-kart .satir {{display:grid;grid-template-columns:150px 1fr 70px 1fr 150px;align-items:center;
      border-bottom:1px solid #1f1f22;padding:5px 0}}
    .nba-kart .uc {{display:flex;align-items:center;gap:10px}}
    .nba-kart .uc.sag {{justify-content:flex-end}}
    .nba-kart .deger {{font-size:28px;font-weight:800}}
    .nba-kart .sira {{background:#26262b;color:#cfcfcf;font-size:12px;font-weight:700;padding:2px 7px;
      border-radius:4px}}
    .nba-kart .iz {{height:14px;background:#1b1b1f;margin:0 8px;display:flex}}
    .nba-kart .sol-iz {{justify-content:flex-end}}
    .nba-kart .bar {{height:100%}}
    .nba-kart .poz {{background:#fff;color:#111;font-weight:900;font-style:italic;font-size:20px;text-align:center;
      transform:skewX(-10deg);padding:3px 0}}
    .nba-kart .not {{font-size:11px;color:#8d8d8d;margin-top:10px}}
    </style>"""
    ev_r, dep_r = EV_RENK, DEP_RENK
    govde = f"""
    <div class="nba-kart">
      <div class="ust"><span>{e(baslik)}</span><span>{e(stat_ad)} · {e(donem_ad[donem or 0])}</span></div>
      <div class="bas">
        <div class="ev"><div class="etiket">Ev sahibi</div><div class="ad">{e(ev)}</div>
          <div class="kayit">{kayit(ev_id)}</div></div>
        <div class="vs">VS</div>
        <div class="dep"><div class="etiket">Deplasman</div><div class="ad">{e(dep)}</div>
          <div class="kayit">{kayit(dep_id)}</div></div>
      </div>
      <div class="govde">
        <div class="alt-baslik">Pozisyon pozisyon eşleşme · maç başı {e(stat_ad.lower())} ve lig sırası</div>
        <div class="grup"><div><div class="b" style="color:{ev_r}">{e(ev)} hücumu</div>
          <div class="k">o pozisyondan ürettiği</div></div>
          <div style="text-align:right"><div class="b" style="color:{dep_r}">{e(dep)} savunması</div>
          <div class="k">o pozisyona yedirdiği</div></div></div>
        {satirlar(ev, dep, ev_r, dep_r, True)}
        <div class="grup" style="margin-top:12px"><div><div class="b" style="color:{ev_r}">{e(ev)} savunması</div>
          <div class="k">o pozisyona yedirdiği</div></div>
          <div style="text-align:right"><div class="b" style="color:{dep_r}">{e(dep)} hücumu</div>
          <div class="k">o pozisyondan ürettiği</div></div></div>
        {satirlar(dep, ev, dep_r, ev_r, False)}
        <div class="not">#1: hücumda o pozisyondan en çok üreten, savunmada o pozisyona en az yediren takım
          ({toplam} takım). Çubuk: sıralar arasındaki fark — hücum tarafındaysa hücum, savunma tarafındaysa savunma
          üstün. Pozisyonlar: ilk 5'e kadro pozisyonlarına göre PG/SG/SF/PF/C atanır, yedekler kendi
          pozisyonunda sayılır.</div>
      </div>
    </div>"""
    return css + govde


# ================================================================ 1) Pozisyon eşleşme
with sekme_eslesme:
    takimlar = sorted(pt.index)
    secenekler = []
    if len(bulten):
        b = bulten.copy()
        b["ev_ad"] = b["ev_id"].map(lambda t: kutu.loc[kutu["takim_id"] == t, "takim"].iloc[0]
                                    if (kutu["takim_id"] == t).any() else None)
        b["dep_ad"] = b["dep_id"].map(lambda t: kutu.loc[kutu["takim_id"] == t, "takim"].iloc[0]
                                      if (kutu["takim_id"] == t).any() else None)
        b = b.dropna(subset=["ev_ad", "dep_ad"])
        secenekler = [(r.ev_ad, r.dep_ad, r) for r in b.itertuples()]
    kaynak = st.segmented_control("Maç", ["iddaa bülteni", "Elle seç"],
                                  default="iddaa bülteni" if secenekler else "Elle seç", key="nba_kaynak")
    bilgi = None
    if kaynak == "iddaa bülteni" and secenekler:
        i = st.selectbox("iddaa'daki NBA maçları", range(len(secenekler)), key="nba_mac",
                         format_func=lambda i: f"{secenekler[i][2].tarih[5:]:s}  {secenekler[i][0]} – "
                                               f"{secenekler[i][1]}")
        ev, dep, bilgi = secenekler[i]
    else:
        if kaynak == "iddaa bülteni":
            st.caption("iddaa bülteninde NBA maçı yok; takımları elle seçin.")
        c1, c2 = st.columns(2)
        ev = c1.selectbox("Ev sahibi", takimlar, key="nba_ev")
        dep = c2.selectbox("Deplasman", [t for t in takimlar if t != ev], key="nba_dep")
    if ev not in pt.index or dep not in pt.index:
        st.warning("Bu takımların kutu skor verisi henüz yok.")
    else:
        tarih = bilgi.tarih if bilgi is not None else datetime.now().strftime("%Y-%m-%d %H:%M")
        st.html(eslesme_karti(ev, dep, f"NBA 2026-27 · {datetime.fromisoformat(tarih):%d.%m.%Y %H:%M}"))

        # Maç bilgisi: iddaa çizgileri ve son maç ortalamaları
        tm = nba.takim_maclari(kutu)
        ev_id, dep_id = takim_id(ev), takim_id(dep)

        def son(tid, k=5):
            return tm[tm["takim_id"] == tid].tail(k)

        k1, k2, k3, k4 = st.columns(4)
        es, ds = son(ev_id), son(dep_id)
        k1.metric(f"{ev} son 5 maç sayı ort.", f"{es['sayi'].mean():.1f}", f"yediği {es['yenen'].mean():.1f}",
                  delta_color="off")
        k2.metric(f"{dep} son 5 maç sayı ort.", f"{ds['sayi'].mean():.1f}", f"yediği {ds['yenen'].mean():.1f}",
                  delta_color="off")
        bek = (es["sayi"].mean() + ds["yenen"].mean()) / 2 + (ds["sayi"].mean() + es["yenen"].mean()) / 2
        k3.metric("Beklenen toplam (son 5)", f"{bek:.1f}",
                  None if bilgi is None or pd.isna(bilgi.toplam) else f"iddaa çizgisi {bilgi.toplam:g}",
                  delta_color="off")
        if bilgi is not None and pd.notna(bilgi.ms1):
            k4.metric("iddaa MS oranı", f"{bilgi.ms1:.2f} – {bilgi.ms2:.2f}")

        # Oyuncu bahisleri için: iki takımın oyuncuları ve rakip savunmasının rolüne göre sırası
        st.markdown("#### Oyuncular ve rakip savunması")
        st.caption("Her oyuncunun bu dönemdeki ortalamaları; 'Rakip sav.' rakibin, oyuncunun en sık oynadığı role "
                   f"karşı maç başı yedirdiği {stat_ad.lower()} ve sırası (#1 en az yediren, #{len(pt)} en çok).")
        oz = nba.oyuncu_ozeti(kutu, n)
        rol_sik = kutu.groupby("oyuncu_id")["rol"].agg(lambda s: s.value_counts().index[0])
        satir = []
        for tid, rakip in ((ev_id, dep), (dep_id, ev)):
            o = oz[oz["takim_id"] == tid].copy()
            o = o[o["Dk"] >= 8]
            for oid, r in o.iterrows():
                rol = rol_sik.get(oid)
                satir.append({"Takım": r["takim"], "Oyuncu": r["oyuncu"], "Rol": rol, "Maç": int(r["Maç"]),
                              "Dk": r["Dk"], "Sayı": r["Sayı"], "Rib": r["Rib"], "Ast": r["Ast"], "3S": r["3S"],
                              "SRA": r["SRA"], "SG%": r["SG%"],
                              "Rakip sav.": f"{pt.loc[rakip, ('savunma', rol)]:.1f} "
                                            f"(#{pt.loc[rakip, ('savunma_sira', rol)]:.0f})" if rol else "–"})
        if satir:
            st.dataframe(pd.DataFrame(satir).style.format({"Dk": "{:.1f}", "Sayı": "{:.1f}", "Rib": "{:.1f}",
                                                           "Ast": "{:.1f}", "3S": "{:.1f}", "SRA": "{:.1f}",
                                                           "SG%": "{:.0%}"}, na_rep="–"),
                         hide_index=True, width="stretch")


# ================================================================ 2) Pozisyon sıralaması
with sekme_sira:
    taraf = st.segmented_control("Tablo", ["hucum", "savunma"], default="hucum",
                                 format_func={"hucum": "Hücum (üretilen)", "savunma": "Savunma (yenen)"}.get,
                                 key="nba_taraf") or "hucum"
    goster = st.segmented_control("Hücrelerde", ["sira", "deger"], default="sira",
                                  format_func={"sira": "Sıra", "deger": "Maç başı değer"}.get, key="nba_goster")
    sira_k = f"{taraf}_sira"
    t = pt.sort_values((sira_k, "Toplam"))
    toplam = len(t)
    baslik = ("HÜCUM" if taraf == "hucum" else "SAVUNMA") + " " + stat_ad.upper()
    alt_yazi = ("Pozisyona göre maç başı üretilen " if taraf == "hucum" else "Pozisyona göre maç başı yenen ") + \
        stat_ad.lower() + " · sıralama"
    aciklama = ("#1 = o pozisyondan en çok üreten" if taraf == "hucum" else "#1 = o pozisyona en az yediren")
    hucreler = []
    for takim, r in t.iterrows():
        tds = [f'<td class="ad">{e(takim)}</td><td class="top">{r[(taraf, "Toplam")]:.1f}</td>']
        for p in nba.POZLAR + ["Toplam"]:
            s = r[(sira_k, p)]
            yazi = f"{s:.0f}" if goster != "deger" or p == "Toplam" else f"{r[(taraf, p)]:.1f}"
            sinif = "hucre son" if p == "Toplam" else "hucre"
            tds.append(f'<td class="{sinif}" style="background:{sira_renk(s, toplam)};'
                       f'color:{yazi_renk(s, toplam)}">{yazi}</td>')
        hucreler.append("<tr>" + "".join(tds) + "</tr>")
    tablo_html = f"""
    <style>
    .nba-sira {{background:#f1f0eb;padding:22px 26px;border-radius:8px;font-family:system-ui,'Segoe UI',sans-serif;
      color:#16202a;max-width:900px}}
    .nba-sira .ust {{color:#b06b12;font-size:12px;font-weight:700;letter-spacing:.18em;text-transform:uppercase}}
    .nba-sira h2 {{font-size:52px;font-weight:900;margin:2px 0 0;line-height:1;letter-spacing:-.01em}}
    .nba-sira h2 span {{color:#c27a1d}}
    .nba-sira .acik {{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:3px solid #16202a;
      padding:8px 0;color:#4b5560;font-size:14px}}
    .nba-sira .acik .sag {{font-size:11px;text-align:right}}
    .nba-sira table {{border-collapse:separate;border-spacing:4px 4px;width:100%;margin-top:8px}}
    .nba-sira th {{font-size:12px;letter-spacing:.08em;color:#16202a;font-weight:800;text-align:center}}
    .nba-sira th.sol {{text-align:left;color:#7a828a;font-weight:600;font-size:10px}}
    .nba-sira td.ad {{background:#fbfbf8;font-weight:800;text-transform:uppercase;padding:6px 10px;font-size:14px}}
    .nba-sira td.top {{background:#fbfbf8;color:#7a828a;text-align:right;padding:6px 10px;font-size:13px}}
    .nba-sira td.hucre {{text-align:center;font-weight:800;font-size:16px;width:12%;padding:6px 0}}
    .nba-sira td.son {{border-left:6px solid #f1f0eb}}
    .nba-sira .alt {{display:flex;justify-content:space-between;font-size:11px;color:#7a828a;margin-top:10px}}
    .nba-sira .lej {{display:inline-block;width:150px;height:9px;vertical-align:middle;margin:0 6px;
      background:linear-gradient(90deg,{sira_renk(1)},{sira_renk(15.5)},{sira_renk(30)})}}
    </style>
    <div class="nba-sira">
      <div class="ust">NBA 2026-27 · {e(donem_ad[donem or 0])} · pozisyon sıralaması</div>
      <h2>{e(baslik.split(' ')[0])} <span>{e(' '.join(baslik.split(' ')[1:]))}</span></h2>
      <div class="acik"><div>{e(alt_yazi)}</div><div class="sag">{e(aciklama)}<br>Sıralama: maç başı toplama göre</div></div>
      <table><tr><th class="sol">TAKIM · TOPLAM</th><th></th>{''.join(f'<th>{p}</th>' for p in nba.POZLAR)}<th>TOPLAM</th></tr>
      {''.join(hucreler)}</table>
      <div class="alt"><div>İYİ<span class="lej"></span>KÖTÜ</div>
        <div>{kutu['mac_id'].nunique()} maç · Maçkolik kutu skorları · pozisyonlar rol bazlı atandı</div></div>
    </div>"""
    st.html(tablo_html)


# ================================================================ 3) Oyuncular
with sekme_oyuncu:
    oz = nba.oyuncu_ozeti(kutu, n)
    f1, f2, f3, f4 = st.columns([2, 2, 1.2, 2])
    tk = f1.multiselect("Takım", sorted(oz["takim"].dropna().unique()), key="nba_o_takim", placeholder="Tümü")
    pz = f2.multiselect("Pozisyon (kadro)", nba.POZLAR, key="nba_o_poz", placeholder="Tümü")
    en_az = f3.number_input("En az maç", 1, 82, 2, key="nba_o_mac")
    ara = f4.text_input("Oyuncu ara", key="nba_o_ara")
    g = oz[oz["Maç"] >= en_az]
    if tk:
        g = g[g["takim"].isin(tk)]
    if pz:
        g = g[g["poz_ad"].isin(pz)]
    if ara:
        g = g[g["oyuncu"].str.contains(ara, case=False, na=False)]
    gor = g.rename(columns={"oyuncu": "Oyuncu", "takim": "Takım", "poz_ad": "Poz"})[
        ["Oyuncu", "Takım", "Poz", "Maç", "İlk 5", "Dk", "Sayı", "Rib", "Ast", "SRA", "3S", "3SD", "TÇ", "Blok", "TK",
         "SG%", "2S%", "3S%", "SA%"]]
    fmt = {k: "{:.1f}" for k in ["Dk", "Sayı", "Rib", "Ast", "SRA", "3S", "3SD", "TÇ", "Blok", "TK"]}
    fmt.update({k: "{:.0%}" for k in ["SG%", "2S%", "3S%", "SA%"]})
    secim = st.dataframe(gor.style.format(fmt, na_rep="–"), hide_index=True, width="stretch", height=430,
                         on_select="rerun", selection_mode="single-row", key="nba_o_tablo",
                         column_config={"SRA": st.column_config.Column(help="Sayı + Ribaund + Asist"),
                                        "3SD": st.column_config.Column(help="3 sayılık deneme"),
                                        "SG%": st.column_config.Column(help="Saha içi isabet (2 + 3 sayılık)"),
                                        "SA%": st.column_config.Column(help="Serbest atış isabeti")})
    st.caption(f"{len(gor)} oyuncu · ortalamalar {donem_ad[donem or 0].lower()} · satıra tıklayınca maç maç "
               "dökümü ve barem isabeti açılır.")
    sec = secim.selection.rows if secim and secim.selection else []
    if sec and sec[0] < len(gor):
        oid = gor.index[sec[0]]
        r = g.loc[oid]
        log = kutu[kutu["oyuncu_id"] == oid].sort_values("ts")
        if n:
            log = log.tail(n)
        st.markdown(f"### {r['oyuncu']} · {r['takim']} · {r['poz_ad']}")
        c1, c2, c3 = st.columns([1.4, 1, 1])
        s_k = c1.selectbox("İstatistik", list(nba.STATLAR), format_func=lambda k: nba.STATLAR[k][0], key="nba_o_stat")
        ort = log[s_k].mean()
        piyasa = nba.props_esle(nba.props_yukle(), kutu)
        piyasa = piyasa[(piyasa["oyuncu_id"] == oid) & (piyasa["stat"] == s_k)] if len(piyasa) else piyasa
        varsayilan = (float(piyasa["cizgi"].iloc[0]) if len(piyasa)
                      else float(np.floor(ort) + 0.5) if pd.notna(ort) else 10.5)
        cizgi = c2.number_input("Barem" + (" (piyasa)" if len(piyasa) else ""), value=varsayilan, step=1.0,
                                key=f"nba_o_cizgi_{oid}_{s_k}")
        oran = nba.barem_isabeti(log[s_k], cizgi)
        c3.metric(f"Üst {cizgi:g} isabeti", "–" if pd.isna(oran) else f"%{100 * oran:.0f}",
                  f"{int((log[s_k] > cizgi).sum())}/{len(log)} maç", delta_color="off")
        log = log.assign(Üst=np.where(log[s_k] > cizgi, "Üst", "Alt"), Etiket=log["tarih"].dt.strftime("%d.%m") +
                         " " + np.where(log["saha"] == "Ev", "vs ", "@ ") + log["rakip"].fillna(""))
        bar = alt.Chart(log).mark_bar().encode(
            x=alt.X("Etiket:N", sort=None, title=None), y=alt.Y(f"{s_k}:Q", title=nba.STATLAR[s_k][0]),
            color=alt.Color("Üst:N", scale=alt.Scale(domain=["Üst", "Alt"], range=[ISABET, ISKA]), legend=None),
            tooltip=["Etiket", s_k, "dk"])
        kural = alt.Chart(pd.DataFrame({"c": [cizgi]})).mark_rule(color=CIZGI, strokeDash=[6, 4], size=2).encode(y="c:Q")
        st.altair_chart((bar + kural).properties(height=260), width="stretch")
        dokum = log[["tarih", "saha", "rakip", "rol", "ilk5", "dk", "sayi", "rib", "ast", "uc_i", "uc_a", "iki_i",
                     "iki_a", "sa_i", "sa_a", "tc", "blok", "tk", "faul"]].copy()
        dokum["tarih"] = dokum["tarih"].dt.strftime("%d.%m")
        dokum["ilk5"] = np.where(dokum["ilk5"] == 1, "✓", "")
        dokum["3S"] = dokum["uc_i"].astype(str) + "/" + dokum["uc_a"].astype(str)
        dokum["2S"] = dokum["iki_i"].astype(str) + "/" + dokum["iki_a"].astype(str)
        dokum["SA"] = dokum["sa_i"].astype(str) + "/" + dokum["sa_a"].astype(str)
        dokum = dokum.rename(columns={"tarih": "Tarih", "saha": "Saha", "rakip": "Rakip", "rol": "Rol",
                                      "ilk5": "İlk 5", "dk": "Dk", "sayi": "Sayı", "rib": "Rib", "ast": "Ast",
                                      "tc": "TÇ", "blok": "Blok", "tk": "TK", "faul": "Faul"})
        st.dataframe(dokum[["Tarih", "Saha", "Rakip", "Rol", "İlk 5", "Dk", "Sayı", "Rib", "Ast", "2S", "3S", "SA",
                            "TÇ", "Blok", "TK", "Faul"]].iloc[::-1].style.format({"Dk": "{:.0f}"}),
                     hide_index=True, width="stretch")


# ================================================================ 0) Oyuncu baremleri (değer)
with sekme_deger:
    props = nba.props_yukle()
    if props.empty:
        st.info("Henüz oyuncu baremi yok. **NBA verisini güncelle** penceresinde 'Oyuncu baremlerini global piyasadan "
                "çek' seçiliyken güncellemeyi başlatın.")
    else:
        c1, c2, c3, c4 = st.columns([1.2, 1.4, 1.6, 1.2])
        n_d = c1.selectbox("Kaç maça bakılsın", [5, 10, 15, 20], index=1, key="nba_d_n")
        agirlik = c2.slider("Rakip savunması etkisi", 0.0, 1.0, 0.5, 0.1, key="nba_d_agirlik",
                            help="0: yalnızca oyuncunun kendi ortalaması. 1: rakibin, oyuncunun rolüne karşı lig "
                                 "ortalamasından ne kadar fazla/az yedirdiği tam olarak yansıtılır.")
        esik = c3.slider("Değerli sayılacak en az beklenen getiri", 0.0, 0.30, 0.08, 0.01, key="nba_d_esik",
                         format="%.2f")
        yalniz = c4.toggle("Yalnızca değerliler", True, key="nba_d_yalniz")
        d = deger(damga, n_d, agirlik)
        if d.empty:
            st.warning("Baremlerdeki oyuncular veritabanındaki oyuncularla eşleşmedi (kutu skor verisi eksik olabilir).")
        else:
            f1, f2, f3 = st.columns(3)
            mac_sec = f1.multiselect("Maç", sorted(d["Maç"].unique()), key="nba_d_mac", placeholder="Tümü")
            stat_sec = f2.multiselect("İstatistik", sorted(d["İstatistik"].unique()), key="nba_d_stat",
                                      placeholder="Tümü")
            en_az_mac = f3.number_input("Oyuncunun en az maç sayısı", 1, 20, 4, key="nba_d_enaz")
            g = d[d["Maç sayısı"] >= en_az_mac]
            if mac_sec:
                g = g[g["Maç"].isin(mac_sec)]
            if stat_sec:
                g = g[g["İstatistik"].isin(stat_sec)]
            degerli = g["En iyi EV"] >= esik
            if yalniz:
                g = g[degerli.loc[g.index]]
            m1, m2, m3 = st.columns(3)
            m1.metric("Oyuncu baremi", len(d))
            m2.metric("Değerli görünen", int((d["En iyi EV"] >= esik).sum()))
            son_cekim = props["cekilme"].max()
            m3.metric("Oranların çekildiği an", str(son_cekim)[11:16] if isinstance(son_cekim, str) else "–",
                      str(son_cekim)[:10] if isinstance(son_cekim, str) else None, delta_color="off")
            gor = g[["Maç", "Oyuncu", "Takım", "Rol", "İstatistik", "Barem", "Ort.", "Beklenen", "Üst isabeti",
                     "Maç sayısı", "Piyasa Üst", "Model Üst", "Üst oran", "Alt oran", "Öneri", "En iyi EV",
                     "EV Üst", "EV Alt", "Rakip etkisi", "Dk"]]

            def renk(v):
                if pd.isna(v):
                    return ""
                if v >= esik:
                    return "background-color:#d3f0e2;color:#0a6a3a;font-weight:600"
                if v >= 0:
                    return "background-color:#f3f6ef"
                return "color:#9a9a9a"

            stil = (gor.style
                    .format({"Barem": "{:g}", "Ort.": "{:.1f}", "Beklenen": "{:.1f}", "Üst isabeti": "{:.0%}",
                             "Piyasa Üst": "{:.0%}", "Model Üst": "{:.0%}", "Üst oran": "{:.2f}",
                             "Alt oran": "{:.2f}", "En iyi EV": "{:+.0%}", "EV Üst": "{:+.0%}", "EV Alt": "{:+.0%}",
                             "Rakip etkisi": "{:+.0%}", "Dk": "{:.0f}"}, na_rep="–")
                    .map(renk, subset=["En iyi EV"]))
            st.dataframe(stil, hide_index=True, width="stretch", height=520, column_config={
                "Ort.": st.column_config.Column(help=f"Oyuncunun son {n_d} maç ortalaması"),
                "Beklenen": st.column_config.Column(help="Ortalama × rakip savunması düzeltmesi; az maçlık oyuncularda "
                                                          "piyasa çizgisine doğru çekilir (5 maç ağırlığında)"),
                "Üst isabeti": st.column_config.Column(help="Son maçlarda baremin üstüne çıktığı maç oranı"),
                "Piyasa Üst": st.column_config.Column(help="Oranlardan çıkan Üst olasılığı (bahis şirketi payı "
                                                           "ayıklanmış)"),
                "Model Üst": st.column_config.Column(help="Beklenen değer ve oyuncunun maçtan maça dağılımıyla "
                                                          "hesaplanan Üst olasılığı"),
                "Öneri": st.column_config.Column(help="Beklenen getirisi yüksek olan taraf"),
                "En iyi EV": st.column_config.Column(help="Önerilen tarafa 1 birim yatırılırsa beklenen kazanç "
                                                          "(model olasılığı × oran − 1)"),
                "Rakip etkisi": st.column_config.Column(help="Rakibin bu role karşı lig ortalamasından fazla (+) ya "
                                                             "da az (−) yedirmesinin beklenene etkisi"),
            })
            st.caption("Kaynak: Kambi (Unibet altyapısı) oyuncu baremleri. Model yalnızca oyuncunun son maçlarına ve "
                       "rakibin pozisyon savunmasına bakar; sakatlık, dinlendirme, süre değişikliği ve maç temposunu "
                       "bilmez. Değerli görünen satırlar inceleme listesidir, kesin tahmin değildir.")


# ================================================================ Oyuncu kartı
@st.cache_data(show_spinner=False)
def savunma(damga, stat_):
    _, kutu_, _, _ = veri(damga)
    return nba.savunma_ozeti(kutu_, stat_)


@st.cache_data(show_spinner=False)
def props_eslesmis(damga):
    _, kutu_, _, _ = veri(damga)
    return nba.props_esle(nba.props_yukle(), kutu_)


KART_STATLAR = ["sayi", "ast", "rib", "uc_i", "pa", "pra", "pr", "ra", "tc", "blok", "uc_a"]

with sekme_kart:
    pe = props_eslesmis(damga)
    ad_harita = kutu.drop_duplicates("takim_id").set_index("takim_id")["takim"].to_dict()
    son_bilgi = kutu.sort_values("ts").groupby("oyuncu_id")[["oyuncu", "takim_id", "takim", "poz_ad"]].last()
    dk_ort = kutu.groupby("oyuncu_id")["dk"].mean()
    # Sıradaki maçı olan (bülten ya da piyasa) oyuncular önce, dakikaya göre
    oynayacak = set()
    if len(bulten):
        oynayacak |= set(bulten["ev_id"]) | set(bulten["dep_id"])
    if len(pe):
        oynayacak |= set(pe["takim_id"])
    sirali = son_bilgi.assign(dk=dk_ort, oyn=son_bilgi["takim_id"].isin(oynayacak)) \
        .sort_values(["oyn", "dk"], ascending=False)
    ust1, ust2 = st.columns([2.2, 3])
    oid = ust1.selectbox("Oyuncu", list(sirali.index), key="nba_k_oyuncu",
                         format_func=lambda o: f"{sirali.loc[o, 'oyuncu']} · {sirali.loc[o, 'takim']}"
                                               + ("  🗓" if sirali.loc[o, "oyn"] else ""))
    stat_k = ust2.segmented_control("İstatistik", KART_STATLAR, default="sayi", key="nba_k_stat",
                                    format_func=lambda k: nba.STATLAR[k][0]) or "sayi"
    oy = son_bilgi.loc[oid]
    log_tum = kutu[kutu["oyuncu_id"] == oid].sort_values("ts")
    rol = log_tum["rol"].value_counts().index[0]
    rakip_bilgi = nba.sonraki_rakip(oy["takim_id"], bulten, pe)
    if rakip_bilgi is None:
        rakip_id = st.selectbox("Rakip (bültende maçı bulunamadı)", [t for t in ad_harita if t != oy["takim_id"]],
                                format_func=ad_harita.get, key="nba_k_rakip")
        saha_s, tarih_s = None, ""
    else:
        rakip_id, saha_s, tarih_s = rakip_bilgi
    rakip_ad = ad_harita.get(rakip_id, "?")

    # Piyasa çizgisi
    piyasa = pe[(pe["oyuncu_id"] == oid) & (pe["stat"] == stat_k)] if len(pe) else pe
    ort10 = log_tum[stat_k].tail(10).mean()
    varsayilan = float(piyasa["cizgi"].iloc[0]) if len(piyasa) else float(np.floor(ort10) + 0.5)
    k1, k2, _ = st.columns([1, 1.4, 3])
    cizgi = k1.number_input("Barem" + (" (piyasa)" if len(piyasa) else ""), value=varsayilan, step=1.0,
                            key=f"nba_k_cizgi_{oid}_{stat_k}")
    if len(piyasa):
        k2.markdown(f"<div style='padding-top:30px;font-size:14px'>Üst <b>{piyasa['ust_oran'].iloc[0]:.2f}</b> · "
                    f"Alt <b>{piyasa['alt_oran'].iloc[0]:.2f}</b></div>", unsafe_allow_html=True)

    sv = savunma(damga, stat_k)
    toplam_t = len(sv)
    dvp = sv.loc[rakip_id, f"{rol}_sira"] if rakip_id in sv.index else np.nan
    def_sira = sv.loc[rakip_id, "Toplam_sira"] if rakip_id in sv.index else np.nan
    harf = nba.not_harfi(dvp, toplam_t)

    def isabet(seri):
        seri = seri.dropna()
        return (float((seri > cizgi).mean()), len(seri)) if len(seri) else (np.nan, 0)

    sezon = isabet(log_tum[stat_k])
    h2h = isabet(log_tum.loc[log_tum["rakip_id"] == rakip_id, stat_k])
    l5, l10, l20 = (isabet(log_tum[stat_k].tail(k)) for k in (5, 10, 20))

    def yuzde(v):
        return "–" if pd.isna(v[0]) else f"%{100 * v[0]:.0f}"

    def ok(v, iyi=True):
        if pd.isna(v):
            return ""
        return f'<span style="color:{"#2bd98a" if iyi else "#ff5a5f"}">{"▲" if iyi else "▼"}</span> '

    renk_harf = "#2bd98a" if harf[0] in "AB" else "#f2c200" if harf[0] == "C" else "#ff5a5f"
    saha_yazi = "" if saha_s is None else ("vs " if saha_s == "Ev" else "@ ")
    son_yazi = f"{saha_yazi}{rakip_ad}" + (f" · {str(tarih_s)[5:16]}" if tarih_s else "")
    css = """
    <style>
    .ok-kart{background:#121214;color:#eee;border-radius:10px;padding:18px 22px;font-family:system-ui,'Segoe UI',sans-serif}
    .ok-ust{display:grid;grid-template-columns:1.6fr repeat(3,1fr) 110px;gap:14px;align-items:center}
    .ok-ad{font-size:28px;font-weight:800}
    .ok-alt{color:#aaa;font-size:14px;margin-top:2px}
    .ok-k{font-size:11px;color:#9a9a9a;letter-spacing:.06em;text-transform:uppercase}
    .ok-v{font-size:17px;font-weight:700;margin-bottom:10px}
    .ok-not{border:1px solid #333;border-radius:8px;text-align:center;padding:10px 0;font-size:12px;color:#ccc}
    .ok-not b{display:block;font-size:26px;margin-bottom:4px}
    .ok-oranlar{display:grid;grid-template-columns:repeat(6,1fr);background:#1b1b1f;border-radius:30px;margin-top:16px;
      padding:6px}
    .ok-oranlar div{text-align:center;font-size:12px;color:#ccc;padding:4px 0;border-radius:24px}
    .ok-oranlar b{display:block;font-size:15px}
    .ok-oranlar .sec{background:#2e2e33}
    .ok-cip{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
    .ok-cip span{border:1px solid #333;border-radius:20px;padding:5px 12px;font-size:13px}
    </style>"""
    def oran_kutu(ad, v, sec=False):
        r = "#2bd98a" if not pd.isna(v[0]) and v[0] >= .5 else "#ff7a7a"
        return (f'<div class="{"sec" if sec else ""}">{ad} <span style="color:#777">({v[1]})</span>'
                f'<b style="color:{r}">{yuzde(v)}</b></div>')
    dvp_iyi = pd.notna(dvp) and dvp <= toplam_t / 2
    sezon_ozet = log_tum.tail(10)
    ust_html = f"""{css}
    <div class="ok-kart">
      <div class="ok-ust">
        <div><div class="ok-ad">{e(oy['oyuncu'])}</div>
          <div class="ok-alt">{e(oy['takim'])} · {e(oy['poz_ad'])} (bu sezon en sık rolü {e(rol)})</div></div>
        <div><div class="ok-k">Son 10 ort.</div><div class="ok-v">{ok(ort10, ort10 > cizgi)}{ort10:.1f}</div>
             <div class="ok-k">İsabet (son 10)</div><div class="ok-v">{ok(l10[0], l10[0] >= .5)}{yuzde(l10)}</div></div>
        <div><div class="ok-k">Barem</div><div class="ok-v">{cizgi:g}</div>
             <div class="ok-k">Rakibin {e(rol)} savunması</div>
             <div class="ok-v">{ok(dvp, dvp_iyi)}{'–' if pd.isna(dvp) else f'#{dvp:.0f}'} / {toplam_t}</div></div>
        <div><div class="ok-k">İstatistik</div><div class="ok-v">{e(nba.STATLAR[stat_k][0])}</div>
             <div class="ok-k">Rakip</div><div class="ok-v">{e(son_yazi)}</div></div>
        <div class="ok-not"><b style="color:{renk_harf}">{harf}</b>Eşleşme</div>
      </div>
      <div class="ok-oranlar">{oran_kutu('Sezon', sezon)}{oran_kutu('Rakibe karşı', h2h)}{oran_kutu('Son 5', l5)}
        {oran_kutu('Son 10', l10, True)}{oran_kutu('Son 20', l20)}
        <div>Ortalama<b>{log_tum[stat_k].mean():.1f}</b></div></div>
      <div class="ok-cip">
        <span>Rakip {e(rol)} savunması <b style="color:{'#2bd98a' if dvp_iyi else '#ff7a7a'}">#{'–' if pd.isna(dvp) else f'{dvp:.0f}'}</b></span>
        <span>Rakip genel savunma <b>#{'–' if pd.isna(def_sira) else f'{def_sira:.0f}'}</b></span>
        <span>Dakika <b>{sezon_ozet['dk'].mean():.1f}</b></span>
        <span>Şut denemesi <b>{sezon_ozet['sg_a'].mean():.1f}</b></span>
        <span>3 sayılık deneme <b>{sezon_ozet['uc_a'].mean():.1f}</b></span>
        <span>Serbest atış denemesi <b>{sezon_ozet['sa_a'].mean():.1f}</b></span>
      </div>
    </div>"""

    sol, sag = st.columns([2.4, 1])
    with sol:
        st.html(ust_html)
        g = log_tum.tail(20).copy()
        g["Etiket"] = g["tarih"].dt.strftime("%d.%m") + " " + np.where(g["saha"] == "Ev", "", "@") + \
            g["rakip"].fillna("").str.slice(0, 12)
        g["Sonuç"] = np.where(g[stat_k] > cizgi, "Üst", "Alt")
        g["deger"] = g[stat_k]
        taban = alt.Chart(g).encode(x=alt.X("Etiket:N", sort=None, title=None,
                                            axis=alt.Axis(labelAngle=0, labelColor="#bbb", labelFontSize=11)))
        bar = taban.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            y=alt.Y("deger:Q", title=None, axis=alt.Axis(labelColor="#bbb", gridColor="#2a2a2a")),
            color=alt.Color("Sonuç:N", scale=alt.Scale(domain=["Üst", "Alt"], range=["#2bd98a", "#ff5a5f"]),
                            legend=None),
            tooltip=[alt.Tooltip("Etiket:N", title="Maç"), alt.Tooltip("deger:Q", title=nba.STATLAR[stat_k][0]),
                     alt.Tooltip("dk:Q", title="Dakika", format=".0f")])
        yazi = taban.mark_text(dy=-8, color="#eee", fontWeight="bold").encode(y="deger:Q", text="deger:Q")
        kural = alt.Chart(pd.DataFrame({"c": [cizgi]})).mark_rule(color="#ffffff", strokeDash=[5, 4]).encode(y="c:Q")
        st.altair_chart((bar + yazi + kural).properties(height=330, background="#121214")
                        .configure_view(strokeWidth=0), width="stretch")
        st.caption("Yeşil: baremin üstü, kırmızı: altı. Son 20 maç; @ deplasman. Eşleşme notu rakibin, oyuncunun "
                   f"rolüne ({rol}) karşı maç başı yedirdiği {nba.STATLAR[stat_k][0].lower()} sırasından gelir: "
                   "#1 en çok yediren (Üst için en elverişli) → A+, son sıra → F.")

    with sag:
        tm = nba.takim_maclari(kutu)
        r_m = tm[tm["takim_id"] == rakip_id].tail(5).iloc[::-1]
        gecmis = "".join(
            f'<div class="sk"><div>{e(("vs " if x.saha == "Ev" else "@ ") + str(x.rakip))}</div>'
            f'<div><b style="color:{"#2bd98a" if x.G else "#ff5a5f"}">{"G" if x.G else "M"}</b> '
            f'{x.sayi:.0f}-{x.yenen:.0f}</div></div>' for x in r_m.itertuples())
        oyuncu_ort = log_tum[stat_k].tail(10).mean()
        rakip_yedirdigi = sv.loc[rakip_id, rol] if rakip_id in sv.index else np.nan
        cubuklar = ""
        for k_ in ["sayi", "ast", "uc_i", "rib"]:
            s_ = savunma(damga, k_)
            sira = s_.loc[rakip_id, f"{rol}_sira"] if rakip_id in s_.index else np.nan
            dol = 0 if pd.isna(sira) else (toplam_t - sira + 1) / toplam_t * 100
            cubuklar += (f'<div class="fav"><div class="fb"><span>{e(nba.STATLAR[k_][0].upper())}</span>'
                         f'<span>SIRA #{"–" if pd.isna(sira) else f"{sira:.0f}"}</span></div>'
                         f'<div class="iz"><div style="width:{dol:.0f}%"></div></div></div>')
        tablo = ""
        for k_ in ["sayi", "ast", "rib", "uc_i"]:
            s_ = savunma(damga, k_)
            def h(c):
                v = s_.loc[rakip_id, c] if rakip_id in s_.index else np.nan
                if pd.isna(v):
                    return "<td>–</td>"
                iyi = v <= toplam_t / 3
                kotu = v > 2 * toplam_t / 3
                isaret = '<span style="color:#2bd98a">▲</span>' if iyi else \
                    '<span style="color:#ff5a5f">▼</span>' if kotu else '<span style="color:#888">●</span>'
                return f"<td>{isaret} {v:.0f}.</td>"
            tablo += f"<tr><td>{e(nba.STATLAR[k_][1])}</td>{h('Toplam_sira')}{h('Ev_sira')}{h('Dep_sira')}</tr>"
        sag_html = f"""
        <style>
        .ok-sag{{background:#121214;color:#eee;border-radius:10px;overflow:hidden;font-family:system-ui,'Segoe UI',sans-serif}}
        .ok-sag .bas{{background:#127f93;padding:16px 18px}}
        .ok-sag .bas h3{{margin:0;font-size:24px;color:#fff}}
        .ok-sag .bas div{{font-size:15px;font-weight:700}}
        .ok-sag .bas small{{display:block;margin-top:14px;font-size:13px;opacity:.9}}
        .ok-sag .bl{{padding:12px 18px;border-top:1px solid #222}}
        .ok-sag .bl .t{{font-size:12px;color:#9a9a9a;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}}
        .ok-sag .sk{{display:flex;justify-content:space-between;font-size:13px;padding:3px 0}}
        .ok-sag .vs{{display:grid;grid-template-columns:1fr 30px 1fr;text-align:center;align-items:center}}
        .ok-sag .vs b{{display:block;font-size:15px}}
        .ok-sag .vs small{{color:#aaa}}
        .ok-sag .fav{{margin:8px 0 12px}}
        .ok-sag .fb{{display:flex;justify-content:space-between;font-size:12px;color:#ccc;margin-bottom:4px}}
        .ok-sag .iz{{background:#26262b;height:16px}}
        .ok-sag .iz div{{background:#127f93;height:100%;margin-left:auto}}
        .ok-sag table{{width:100%;font-size:13px;border-collapse:collapse}}
        .ok-sag td,.ok-sag th{{padding:5px 2px;text-align:left;color:#eee}}
        .ok-sag th{{color:#9a9a9a;font-weight:600;font-size:11px}}
        </style>
        <div class="ok-sag">
          <div class="bas"><h3>Eşleşme</h3><div>{e(rakip_ad)}</div>
            <small>Kayıt {nba.kayit(kutu, rakip_id)} · evinde {nba.kayit(kutu, rakip_id, 'Ev')} · deplasmanda
            {nba.kayit(kutu, rakip_id, 'Dep')}</small></div>
          <div class="bl"><div class="t">Rakibin son maçları</div>{gecmis or '<small>Maç yok</small>'}</div>
          <div class="bl"><div class="vs"><div><b>{e(oy['oyuncu'])}</b><small>{oyuncu_ort:.1f}
            {e(nba.STATLAR[stat_k][1])}/maç (son 10)</small></div><div>VS</div>
            <div><b>{e(rakip_ad)}</b><small>{'–' if pd.isna(rakip_yedirdigi) else f'{rakip_yedirdigi:.1f}'}
            {e(nba.STATLAR[stat_k][1])} yediriyor ({e(rol)})</small></div></div></div>
          <div class="bl"><div class="t">{e(rol)} pozisyonuna karşı elverişlilik</div>{cubuklar}
            <small style="color:#888">Dolu çubuk = rakip bu pozisyona çok yediriyor (Üst için iyi).</small></div>
          <div class="bl"><div class="t">{e(rakip_ad)} savunması (yedirdiği, sıra)</div>
            <table><tr><th></th><th>Genel</th><th>Evinde</th><th>Deplasmanda</th></tr>{tablo}</table>
            <small style="color:#888">#1 = en çok yediren. ▲ ilk üçte bir, ▼ son üçte bir.</small></div>
        </div>"""
        st.html(sag_html)


# ================================================================ Barem tarayıcı
@st.cache_data(show_spinner="Oyuncu baremleri son maçlarla karşılaştırılıyor…")
def tarama(damga, n):
    _, kutu_, _, _ = veri(damga)
    return nba.barem_tarayici(nba.props_yukle(), kutu_, n)


TARAMA_STAT = ["sayi", "rib", "ast", "uc_i", "pra", "pr", "pa", "ra", "tc", "blok", "tk"]


def yuzde_n(v, k):
    return "–" if pd.isna(v) else f"{100 * v:3.0f}% ({int(k)})"


with sekme_tarama:
    if nba.props_yukle().empty:
        st.info("Henüz oyuncu baremi yok. **NBA verisini güncelle** penceresinde 'Oyuncu baremlerini global piyasadan "
                "çek' seçiliyken güncellemeyi başlatın.")
    else:
        a1, a2, a3, a4 = st.columns([1, 1.3, 1.5, 1.2])
        n_t = a1.selectbox("Kaç maça bakılsın", [5, 10, 15, 20], index=1, key="nba_t_n")
        yon_t = a2.segmented_control("Taraf", ["Üst", "Alt"], selection_mode="multi", default=["Üst", "Alt"],
                                     key="nba_t_yon") or ["Üst", "Alt"]
        oran_t = a3.slider("Oran", 1.0, 6.0, (1.3, 6.0), 0.05, key="nba_t_oran")
        form_t = a4.slider("En az form", 0, 100, 60, 5, key="nba_t_form", format="%d%%")
        t = tarama(damga, n_t)
        if t.empty:
            st.warning("Baremlerdeki oyuncular veritabanındaki oyuncularla eşleşmedi.")
        else:
            mevcut = [k for k in TARAMA_STAT if k in set(t["stat"])]
            stat_t = st.pills("İstatistik", mevcut, selection_mode="multi", default=mevcut, key="nba_t_stat",
                              format_func=lambda k: nba.STATLAR[k][0]) or mevcut
            b1, b2, b3 = st.columns([2, 2, 1])
            mac_t = b1.multiselect("Maç", sorted(t["Maç"].unique()), key="nba_t_mac", placeholder="Tüm maçlar")
            tk_t = b2.multiselect("Takım", sorted(t["Takım"].dropna().unique()), key="nba_t_takim",
                                  placeholder="Tüm takımlar")
            en_az_t = b3.number_input("En az maç", 1, 20, 2, key="nba_t_enaz")
            g = t[t["stat"].isin(stat_t) & t["Yön"].isin(yon_t) & t["Oran"].between(*oran_t)
                  & (t["Form"].fillna(0) >= form_t / 100) & (t["Maç sayısı"] >= en_az_t)]
            if mac_t:
                g = g[g["Maç"].isin(mac_t)]
            if tk_t:
                g = g[g["Takım"].isin(tk_t)]
            g = g.sort_values(["Form", "Rakip izin", "Değer"], ascending=False).reset_index(drop=True)
            st.caption(f"{len(g)} barem · {g['Oyuncu'].nunique()} oyuncu · Form: oyuncunun son {n_t} maçında bu "
                       "tarafı tutturma oranı (parantez: maç sayısı). Rakip izin: rakibin aynı roldeki ilk 5 "
                       f"oyuncularına son {n_t} maçta bu tarafı yaşattığı maç oranı.")
            saat = pd.to_datetime(g["baslangic"], utc=True, errors="coerce").dt.tz_convert("Europe/Istanbul")
            gor = pd.DataFrame({
                "Oyuncu": g["Oyuncu"], "Takım": g["Takım"], "Rakip": g["Rakip"],
                "Saat": saat.dt.strftime("%d.%m %H:%M").fillna("–"),
                "İstatistik": g["İstatistik"], "Barem": g["Yön"] + " " + g["Çizgi"].map("{:g}".format),
                "Oran": g["Oran"].map("{:.2f}".format),
                f"Form L{n_t}": [yuzde_n(v, k) for v, k in zip(g["Form"], g["Maç sayısı"])],
                "Sezon": [yuzde_n(v, k) for v, k in zip(g["Sezon"], g["Sezon maç"])],
                "Rakip izin": [yuzde_n(v, k) for v, k in zip(g["Rakip izin"], g["Rakip maç"])],
                "Ort.": g["Ort."], "Rakip izin ort.": g["Rakip izin ort."],
                "Model": g["Model"].map(lambda v: "–" if pd.isna(v) else f"{100 * v:3.0f}%"),
                "Değer": g["Değer"].map(lambda v: "–" if pd.isna(v) else f"{100 * v:+.0f}%"),
                "Rol": g["Rol"],
            })

            def renk_y(v):
                if pd.isna(v):
                    return ""
                if v >= 0.7:
                    return "color:#0a7a3f;font-weight:600"
                if v <= 0.5:
                    return "background-color:#fdecc8;color:#8a5a00"
                return ""

            def renkler_t(_):
                css = pd.DataFrame("", index=gor.index, columns=gor.columns)
                css[f"Form L{n_t}"] = g["Form"].map(renk_y)
                css["Sezon"] = g["Sezon"].map(renk_y)
                css["Rakip izin"] = g["Rakip izin"].map(renk_y)
                css["Model"] = g["Model"].map(renk_y)
                css["Değer"] = g["Değer"].map(lambda v: "" if pd.isna(v) else
                                              "color:#0a7a3f;font-weight:600" if v > 0.05 else
                                              "color:#9a9890" if v < -0.05 else "")
                css["Barem"] = g["Yön"].map({"Üst": "color:#0a7a3f", "Alt": "color:#b4532a"})
                return css

            secim_t = st.dataframe(
                gor.style.format({"Ort.": "{:.1f}", "Rakip izin ort.": "{:.1f}"}, na_rep="–").apply(renkler_t, axis=None),
                hide_index=True, width="stretch", height=min(36 * (len(gor) + 1) + 4, 640),
                on_select="rerun", selection_mode="single-row", key="nba_t_tablo",
                column_config={
                    "Oran": st.column_config.TextColumn(help="Kambi (Unibet) oranı"),
                    "Sezon": st.column_config.TextColumn(help="Bu sezonun tüm maçlarında bu tarafı tutturma oranı"),
                    "Rakip izin": st.column_config.TextColumn(
                        help="Rakibin, bu oyuncunun rolündeki (PG/SG/SF/PF/C) ilk 5 oyuncularına son maçlarda bu "
                             "tarafı yaşatma oranı"),
                    "Rakip izin ort.": st.column_config.Column(
                        help="Rakibe karşı bu roldeki ilk 5 oyuncularının ortalaması"),
                    "Model": st.column_config.TextColumn(help="Ortalama, dağılım ve rakip savunmasıyla bu tarafın "
                                                              "olasılığı (az maçta piyasa çizgisine çekilir)"),
                    "Değer": st.column_config.TextColumn(help="Model olasılığı × oran − 1"),
                })
            sec_t = secim_t.selection.rows if secim_t and secim_t.selection else []
            if sec_t and sec_t[0] < len(g):
                r = g.iloc[sec_t[0]]
                log = kutu[kutu["oyuncu_id"] == r["oyuncu_id"]].sort_values("ts").tail(n_t)
                log = log.assign(deger=log[r["stat"]],
                                 Sonuç=np.where((log[r["stat"]] > r["Çizgi"]) if r["Yön"] == "Üst"
                                                else (log[r["stat"]] < r["Çizgi"]), "Tuttu", "Tutmadı"),
                                 Etiket=log["tarih"].dt.strftime("%d.%m") + " " +
                                 np.where(log["saha"] == "Ev", "", "@") + log["rakip"].fillna(""))
                st.markdown(f"**{r['Oyuncu']}** · {r['İstatistik']} {r['Yön']} {r['Çizgi']:g} · rakip {r['Rakip']}")
                bar = alt.Chart(log).mark_bar().encode(
                    x=alt.X("Etiket:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("deger:Q", title=r["İstatistik"]),
                    color=alt.Color("Sonuç:N", scale=alt.Scale(domain=["Tuttu", "Tutmadı"], range=[ISABET, ISKA]),
                                    legend=None),
                    tooltip=["Etiket", "deger", alt.Tooltip("dk:Q", format=".0f", title="Dakika")])
                kural = alt.Chart(pd.DataFrame({"c": [r["Çizgi"]]})).mark_rule(
                    color=CIZGI, strokeDash=[6, 4], size=2).encode(y="c:Q")
                st.altair_chart((bar + kural).properties(height=240), width="stretch")
