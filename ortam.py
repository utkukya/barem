"""Çalışma ortamı: uygulama Streamlit Cloud'da mı (yalnızca okuma) yoksa yerel bilgisayarda mı (güncelleme açık)."""
import os
from pathlib import Path


def bulutta_mi():
    """Streamlit Community Cloud: kod /mount/src altında, kullanıcı appuser."""
    return Path("/mount/src").exists() or os.environ.get("HOME", "") == "/home/appuser"


def guncelleme_acik():
    """Veri çekme yalnızca yerelde. BAREM_GUNCELLEME=1 / 0 ortam değişkeniyle zorlanabilir."""
    v = os.environ.get("BAREM_GUNCELLEME")
    if v is not None:
        return v.strip() == "1"
    return not bulutta_mi()


KAPALI_YARDIM = "Bu sürüm yalnızca görüntüleme içindir; veriler yerel bilgisayardan güncellenip yayınlanır."


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
