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
