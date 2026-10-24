"""Sayfaların ortak kullandığı yardımcılar."""
import streamlit as st

from db_olustur import DB, KLASOR, csv_bul, db_hazir, olustur


def veritabani_kontrol():
    """futbol.db yoksa klasördeki CSV'den otomatik oluşturur; CSV de yoksa CSV yükleme alanı gösterir."""
    if db_hazir():
        return
    csv = csv_bul()
    if csv is not None:
        with st.spinner(f"Veritabanı ilk kez oluşturuluyor ({csv.name})…"):
            olustur(csv)
        st.cache_data.clear()
        st.toast(f"futbol.db oluşturuldu: {csv.name}", icon="✅")
        return

    st.error("Maç verisi bulunamadı.")
    st.markdown(
        f"Uygulama veritabanını şu klasörde arıyor:\n\n`{KLASOR}`\n\n"
        "Bu klasörde ne **futbol.db** ne de **son10_takim_bazli.csv** var. Ya scraper'ın ürettiği "
        "`son10_takim_bazli.csv` dosyasını bu klasöre kopyalayıp sayfayı yenileyin, ya da aşağıdan yükleyin."
    )
    dosya = st.file_uploader("son10_takim_bazli.csv", type="csv")
    if dosya is not None:
        hedef = KLASOR / "son10_takim_bazli.csv"
        hedef.write_bytes(dosya.getvalue())
        with st.spinner("Veritabanı oluşturuluyor…"):
            olustur(hedef)
        st.cache_data.clear()
        st.rerun()
    st.stop()
