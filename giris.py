"""
Giriş (login). app.py, hiçbir sayfayı çalıştırmadan önce giris_kontrol() çağırır: giriş yapılmamışsa yalnızca
giriş formu görünür ve uygulama durur — doğrudan sayfa adresine (/nba_sayfa gibi) gidilse de.

Kullanıcı adı ve şifre kodda ya da depoda yoktur. Şifrenin kendisi değil, tuzlanmış PBKDF2 özeti saklanır:
  - Streamlit Cloud: Manage app → Settings → Secrets
  - Yerel bilgisayar: .streamlit/secrets.toml (depoya gönderilmez)
Her ikisi için de satırları üretmek:  python giris_ayarla.py
"""
import hashlib
import hmac
import secrets as _secrets
import time

import streamlit as st

TUR = 300_000
EN_FAZLA_DENEME = 5
BEKLEME_SN = 60


def sifre_ozeti(sifre, tuz=None, tur=TUR):
    tuz = tuz or _secrets.token_hex(16)
    ozet = hashlib.pbkdf2_hmac("sha256", sifre.encode("utf-8"), bytes.fromhex(tuz), tur).hex()
    return f"pbkdf2_sha256${tur}${tuz}${ozet}"


def _dogrula(sifre, kayitli):
    try:
        _, tur, tuz, ozet = kayitli.split("$")
        yeni = hashlib.pbkdf2_hmac("sha256", sifre.encode("utf-8"), bytes.fromhex(tuz), int(tur)).hex()
        return hmac.compare_digest(yeni, ozet)
    except Exception:
        return False


def _ayarlar():
    try:
        k = st.secrets.get("giris_kullanici")
        s = st.secrets.get("giris_sifre_ozeti")
    except Exception:
        return None, None
    return (str(k), str(s)) if k and s else (None, None)


@st.cache_resource
def _genel_hatalar():
    """Tüm oturumlardaki hatalı denemelerin zamanları (yeni sekme açarak sınırı aşmayı engeller)."""
    return []


def giris_kontrol():
    """Giriş yapılmışsa döner; yapılmamışsa giriş formunu gösterip sayfayı durdurur."""
    if st.session_state.get("giris_tamam"):
        return
    kullanici, ozet = _ayarlar()
    if not kullanici:
        from ortam import bulutta_mi
        if not bulutta_mi():
            return  # yerelde giriş ayarlanmamışsa açık (kendi bilgisayarın); ayarlanırsa yerelde de sorulur
        st.title("Giriş ayarlanmamış")
        st.error("Uygulama kilitli: Streamlit Secrets'ta giris_kullanici ve giris_sifre_ozeti tanımlı değil.")
        st.stop()

    _, orta, _ = st.columns([1, 1.2, 1])
    with orta:
        st.title("Giriş")
        hatalar = _genel_hatalar()
        hatalar[:] = [t for t in hatalar if time.time() - t < 300]
        kilit = max(st.session_state.get("giris_kilit", 0), (hatalar[-1] + BEKLEME_SN) if len(hatalar) >= 15 else 0)
        if time.time() < kilit:
            st.error(f"Çok fazla hatalı deneme. {int(kilit - time.time())} saniye sonra tekrar deneyin.")
            st.stop()
        with st.form("giris_formu"):
            ad = st.text_input("Kullanıcı adı", autocomplete="username")
            sifre = st.text_input("Şifre", type="password", autocomplete="current-password")
            gonder = st.form_submit_button("Giriş yap", type="primary", width="stretch")
        if gonder:
            ad_dogru = hmac.compare_digest(ad.strip().encode("utf-8"), kullanici.encode("utf-8"))
            if _dogrula(sifre, ozet) and ad_dogru:
                st.session_state["giris_tamam"] = True
                st.session_state.pop("giris_hata", None)
                st.rerun()
            hatalar.append(time.time())
            hata = st.session_state.get("giris_hata", 0) + 1
            st.session_state["giris_hata"] = hata
            time.sleep(1)  # deneme hızını düşür
            if hata >= EN_FAZLA_DENEME:
                st.session_state["giris_kilit"] = time.time() + BEKLEME_SN
                st.session_state["giris_hata"] = 0
            st.error("Kullanıcı adı ya da şifre hatalı.")
    st.stop()


def cikis_dugmesi(yer):
    """Giriş açıksa 'Çıkış' düğmesi."""
    if st.session_state.get("giris_tamam") and yer.button("Çıkış", icon="🔒", key="giris_cikis"):
        st.session_state.pop("giris_tamam", None)
        st.rerun()
