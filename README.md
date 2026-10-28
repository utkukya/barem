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
- **NBA / EuroLeague:** *NBA verisini güncelle* / *EuroLeague verisini güncelle* (sezonun kutu skorları, takım kadroları, iddaa bülteni ve oyuncu baremleri). İki lig aynı kodu kullanır (`nba.py`, `ligler.py`).

## Veri kaynakları

- iddaa (bülten, oranlar, baremler)
- Maçkolik (futbol ve basketbol sonuç arşivi, NBA kutu skorları ve kadroları)
- Ofsayt.com (futbol maç istatistikleri: korner, kart, şut...)
- Flashscore (voleybol)
- Kambi / Unibet (NBA oyuncu baremleri)

## Streamlit Cloud (yalnızca görüntüleme)

Bulutta çalışan sürüm veri çekmez; güncelleme düğmeleri kapalıdır. Veriler yerel bilgisayarda güncellenir:

1. Yerelde `streamlit run app.py` ile uygulamayı açıp sayfalardaki güncellemeleri çalıştırın.
2. `yayinla.bat` dosyasına çift tıklayın: önce ağır tabloları hesaplayıp veritabanına yazar (`python hazir.py`), sonra veritabanlarını GitHub'a gönderir. Yayındaki sürüm bu hazır tabloları hesaplama yapmadan okur.
3. Streamlit Cloud birkaç dakika içinde yeni veriyle açılır.

Yerelde güncelleme her zaman açıktır; `BAREM_GUNCELLEME=0` / `1` ortam değişkeniyle zorlanabilir.

## Buluttan güncelleme (kalıcı)

Streamlit Cloud'daki güncelleme düğmeleri de çalışır. Buluttan çekilen verinin kalıcı olması için güncellenen
veritabanı GitHub'a otomatik commit edilir (`bulut_kayit.py`). Streamlit → Manage app → Settings → Secrets:

```
github_token = "github_pat_..."      # yalnızca bu depoya Contents: Read and write izni
guncelleme_sifresi = "..."           # isteğe bağlı: güncelleme düğmesi şifre ister
```

Yerelde çalışmaya başlamadan önce `guncel_al.bat` ile buluttaki güncellemeleri indirin; yayınlarken
`yayinla.bat` önce buluttakileri alır, aynı dosya iki tarafta da değiştiyse yereldeki kalır.

## Giriş

Uygulama giriş ister; giriş yapılmadan hiçbir sayfa (adresle açılsa da) çalışmaz. Kullanıcı adı ve şifre depoda
yoktur, yalnızca şifrenin özeti Secrets'ta saklanır:

1. `giris_ayarla.bat` (ya da `python giris_ayarla.py`) çalıştırın, kullanıcı adı ve şifreyi girin.
2. Çıkan iki satırı Streamlit Cloud → Manage app → Settings → Secrets'a yapıştırın.
3. İsterseniz aynı satırlar yereldeki `.streamlit/secrets.toml` dosyasına da yazılır (depoya gitmez).

Bulutta bu satırlar yoksa uygulama tamamen kilitli kalır. Yerelde ayarlanmamışsa giriş sorulmaz.
