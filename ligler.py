"""
Oyuncu ve pozisyon analizi yapılan basketbol ligleri. Hepsi aynı kodu (nba.py) kullanır; her lig kendi
veritabanına yazar. Yeni lig eklemek için LIGLER'e bir satır yeterli.
"""
import importlib.util
import sys
from pathlib import Path

KLASOR = Path(__file__).resolve().parent

LIGLER = {
    "nba": None,  # nba.py'nin kendi sabitleri
    "euroleague": {
        "DB": KLASOR / "euroleague.db",
        "NBA_LIG": "EuroLeague", "NBA_ULKE": "Avrupa",
        "BASLIK": "EuroLeague", "SEZON_ADI": "2026-27",
        "SEZON_BASLANGIC": "2026-09-01",   # EuroLeague'de hazırlık maçları ayrı yarışma; sezonun tamamı alınır
        "IDDAA_CI": {251000},              # iddaa: "Euroleague"
        "KAMBI_LISTE": "listView/basketball/euroleague.json",
    },
}
_yuklu = {}


def modul(ad="nba"):
    """Ligin analiz modülü. NBA için nba.py'nin kendisi; diğerleri için nba.py'nin sabitleri değiştirilmiş ayrı
    bir kopyası (fonksiyonlar kendi kopyasının sabitlerini kullanır)."""
    if ad == "nba":
        import nba
        return nba
    if ad in _yuklu and _yuklu[ad].__file__ == str(KLASOR / "nba.py") and \
            getattr(_yuklu[ad], "_kaynak_zamani", None) == (KLASOR / "nba.py").stat().st_mtime:
        return _yuklu[ad]
    ad_ = f"lig_{ad}"
    spec = importlib.util.spec_from_file_location(ad_, KLASOR / "nba.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[ad_] = m
    spec.loader.exec_module(m)
    for k, v in LIGLER[ad].items():
        setattr(m, k, v)
    m._kaynak_zamani = (KLASOR / "nba.py").stat().st_mtime
    _yuklu[ad] = m
    return m
