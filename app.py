"""
Futbol ve basketbol istatistik / barem arayüzü.
Çalıştırma:  streamlit run app.py
Her sayfanın üstünde spor menüsü: ⚽ Futbol (Barem Tarayıcı, Maç İstatistikleri, Maç Analizi) · 🏀 Basketbol · ⛹️ NBA · 🏐 Voleybol
"""
import streamlit as st

st.set_page_config(page_title="Maç İstatistik Tahmin", page_icon="⚽", layout="wide")


@st.cache_resource
def _modul_zamanlari():
    return {}


def _degisen_modulleri_yenile():
    """Sunucu açıkken dosyalar güncellenirse (ör. Streamlit Cloud'a yeni sürüm gelince) projedeki yardımcı modüller
    bellekteki eski haliyle kalmasın: değişen dosyalar yeniden yüklenir."""
    import importlib
    import sys
    from pathlib import Path
    klasor = Path(__file__).resolve().parent
    zamanlar = _modul_zamanlari()
    for ad, modul in list(sys.modules.items()):
        dosya = getattr(modul, "__file__", None)
        if not dosya or ad == "__main__" or Path(dosya).resolve().parent != klasor:
            continue
        try:
            z = Path(dosya).stat().st_mtime
        except OSError:
            continue
        if ad in zamanlar and zamanlar[ad] != z:
            try:
                importlib.reload(modul)
                st.cache_data.clear()
            except Exception:
                pass
        zamanlar[ad] = z


_degisen_modulleri_yenile()

FUTBOL = [
    st.Page("barem_tarayici.py", title="Barem Tarayıcı", icon="🎯", default=True),
    st.Page("mac_listesi.py", title="Maç İstatistikleri", icon="📋"),
    st.Page("mac_analizi.py", title="Maç Analizi", icon="📊"),
]
BASKETBOL = [
    st.Page("basketbol_sayfa.py", title="Basketbol", icon="🏀"),
]
NBA = [
    st.Page("nba_sayfa.py", title="NBA", icon="⛹️"),
]
VOLEYBOL = [
    st.Page("voleybol_sayfa.py", title="Voleybol", icon="🏐"),
]
SPORLAR = {"⚽ Futbol": FUTBOL, "🏀 Basketbol": BASKETBOL, "⛹️ NBA": NBA, "🏐 Voleybol": VOLEYBOL}

# Streamlit'in kendi menüsü gizli; menüyü aşağıda biz çiziyoruz (her sürümde aynı görünsün)
sayfa = st.navigation(FUTBOL + BASKETBOL + NBA + VOLEYBOL, position="hidden")
aktif_spor = next(ad for ad, liste in SPORLAR.items() if sayfa.url_path in {p.url_path for p in liste})

menu = st.container()
with menu:
    sol, sag = st.columns([2.5, 4], vertical_alignment="center")
    secilen = sol.segmented_control("Spor", list(SPORLAR), default=aktif_spor, key=f"spor_menu_{aktif_spor}",
                                    label_visibility="collapsed")
    if secilen and secilen != aktif_spor:
        st.switch_page(SPORLAR[secilen][0])
    with sag:
        sekmeler = SPORLAR[aktif_spor]
        for kol, p in zip(st.columns(len(sekmeler) + (4 - len(sekmeler) if len(sekmeler) < 4 else 0)), sekmeler):
            kol.page_link(p, label=p.title, icon=p.icon, disabled=p.url_path == sayfa.url_path)
    st.divider()

sayfa.run()
