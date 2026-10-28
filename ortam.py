"""Çalışma ortamı: yerel bilgisayar ya da Streamlit Cloud; güncelleme izni ve tablo sayfalama."""
import os
from pathlib import Path


def bulutta_mi():
    """Streamlit Community Cloud: kod /mount/src altında, kullanıcı appuser."""
    return Path("/mount/src").exists() or os.environ.get("HOME", "") == "/home/appuser"


def guncelleme_acik():
    """Veri çekme hem yerelde hem Streamlit Cloud'da açık. BAREM_GUNCELLEME=0 ortam değişkeniyle kapatılabilir."""
    v = os.environ.get("BAREM_GUNCELLEME")
    if v is not None:
        return v.strip() == "1"
    return True


KAPALI_YARDIM = "Güncelleme bu ortamda kapalı (BAREM_GUNCELLEME=0)."


def _sifre():
    """Streamlit Cloud'da Settings → Secrets bölümüne guncelleme_sifresi = "..." yazılırsa güncelleme şifre ister."""
    try:
        import streamlit as st
        return st.secrets.get("guncelleme_sifresi")
    except Exception:
        return None


def guncelleme_izni(anahtar):
    """Güncelleme penceresinde, düğmeden önce çağrılır. Şifre tanımlıysa (yalnızca bulutta) şifre alanı gösterir;
    güncellemeye izin varsa True döner."""
    import streamlit as st
    sifre = _sifre() if bulutta_mi() else None
    if not sifre or st.session_state.get("guncelleme_izni"):
        return True
    girilen = st.text_input("Güncelleme şifresi", type="password", key=f"{anahtar}_sifre")
    if girilen and girilen == str(sifre):
        st.session_state["guncelleme_izni"] = True
        return True
    if girilen:
        st.error("Şifre yanlış.")
    return False


SAYFA_BOYUTU = 100


def sayfala(df, anahtar, boyut=SAYFA_BOYUTU):
    """Uzun tabloyu sayfalara böler: yalnızca seçili sayfanın satırları tarayıcıya gönderilir.
    Seçili sayfanın satırlarını döner (index korunur); tek sayfaysa tabloyu olduğu gibi döner."""
    import math
    import streamlit as st
    toplam = len(df)
    if toplam <= boyut:
        return df
    sayfa_sayisi = math.ceil(toplam / boyut)
    k = f"{anahtar}_sayfa"
    if st.session_state.get(k, 1) > sayfa_sayisi:  # süzgeç değişince sayfa sayısı azalmış olabilir
        st.session_state[k] = 1
    sol, sag = st.columns([1, 5], vertical_alignment="center")
    sayfa = sol.number_input("Sayfa", 1, sayfa_sayisi, 1, key=k, label_visibility="collapsed")
    bas = (sayfa - 1) * boyut
    sag.caption(f"Sayfa {sayfa}/{sayfa_sayisi} · {bas + 1}–{min(bas + boyut, toplam)}. satırlar · toplam {toplam}")
    return df.iloc[bas:bas + boyut]
