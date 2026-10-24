# Barem

iddaa'daki futbol, basketbol, NBA ve voleybol maçlarının baremlerini takımların ve oyuncuların son maçlarıyla karşılaştıran Streamlit uygulaması.

## Kurulum (yeni bilgisayarda)

Python 3.11 veya üstü gerekir.

```
git clone https://github.com/utkukya/barem.git
cd barem
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Sonraki açılışlarda: `.venv\Scripts\activate` ardından `streamlit run app.py`.

## İlk çalıştırma

Veritabanları depoda yoktur; uygulama ilk açılışta boş başlar. Her sayfadaki güncelleme penceresinden veriyi çekin:

- **Futbol:** Barem Tarayıcı → *Baremleri güncelle*. İlk seferde Maçkolik'in son 45 günlük sonuç arşivi çekilir (gün başına 1 istek, birkaç dakika sürer).
- **Basketbol / Voleybol:** sayfadaki *Baremleri güncelle*.
- **NBA:** *NBA verisini güncelle* (sezonun kutu skorları, takım kadroları, iddaa bülteni ve oyuncu baremleri).

## Veri kaynakları

- iddaa (bülten, oranlar, baremler)
- Maçkolik (futbol ve basketbol sonuç arşivi, NBA kutu skorları ve kadroları)
- Ofsayt.com (futbol maç istatistikleri: korner, kart, şut...)
- Flashscore (voleybol)
- Kambi / Unibet (NBA oyuncu baremleri)

## Streamlit Cloud (yalnızca görüntüleme)

Bulutta çalışan sürüm veri çekmez; güncelleme düğmeleri kapalıdır. Veriler yerel bilgisayarda güncellenir:

1. Yerelde `streamlit run app.py` ile uygulamayı açıp sayfalardaki güncellemeleri çalıştırın.
2. `yayinla.bat` dosyasına çift tıklayın (veritabanlarını GitHub'a gönderir).
3. Streamlit Cloud birkaç dakika içinde yeni veriyle açılır.

Yerelde güncelleme her zaman açıktır; `BAREM_GUNCELLEME=0` / `1` ortam değişkeniyle zorlanabilir.
