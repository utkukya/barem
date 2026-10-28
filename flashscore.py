"""
Flashscore (voleybol): günlük maç listesi ve takımların son maçları (set set sayılarla).

  - Günlük liste: tek istekte o günün tüm voleybol maçları (takım adları ve kimlikleri).
  - Takımın son maçları: takımın "sonuçlar" sayfasına gömülü veri; takım başına 1 istek, son ~13 maç.
Veri biçimi: kayıtlar "~" ile, alanlar "¬" ile ayrılır; alan adı ve değer "÷" ile.
"""
import re
import threading
import time

import pandas as pd

ANA = "https://www.flashscore.com"
FEED = "https://2.flashscore.ninja/2/x/feed"
SPOR_KODU = {"voleybol": 12}
YEDEK_IMZA = "SW9D1eZo"
ARALIK = 1.5

_kilit = threading.Lock()
_durum = {"son": 0.0, "engel": False, "istek": 0, "imza": None}
ENGEL_SURE = 20 * 60     # site istekleri reddettiyse bu kadar saniye istek gönderilmez, sonra yeniden denenir


def _engelde():
    t = _durum["engel"]
    if t and time.monotonic() - t < ENGEL_SURE:
        return True
    _durum["engel"] = False
    return False


class FlashscoreEngel(RuntimeError):
    """Flashscore istekleri reddediyor."""


ENGEL_MESAJI = ("Flashscore istekleri geçici olarak reddediyor. Bu güncellemede takım verisi çekilmedi; "
                "veritabanındaki maçlar kullanılıyor. Bir süre sonra tekrar deneyin.")


def _oturum():
    if "oturum" not in _durum:
        from curl_cffi import requests as creq
        s = creq.Session(impersonate="chrome")
        s.headers.update({"Accept-Language": "en-US,en;q=0.9", "Referer": ANA + "/"})
        _durum["oturum"] = s
    return _durum["oturum"]


def istek_sayisi():
    return _durum["istek"]


def _get(url, basliklar=None):
    if _engelde():
        raise FlashscoreEngel(ENGEL_MESAJI)
    durum = None
    for deneme in range(3):
        with _kilit:
            bekle = _durum["son"] + ARALIK - time.monotonic()
            if bekle > 0:
                time.sleep(bekle)
            _durum["son"] = time.monotonic()
            _durum["istek"] += 1
        try:
            r = _oturum().get(url, headers=basliklar or {}, timeout=40)
            durum = r.status_code
        except Exception as e:
            durum = str(e)
            time.sleep(3 * (deneme + 1))
            continue
        if durum == 200:
            return r.text
        if durum == 404:
            return None
        if durum in (401, 403, 429):
            _durum["engel"] = time.monotonic()
            raise FlashscoreEngel(ENGEL_MESAJI)
        time.sleep(3 * (deneme + 1))
    raise RuntimeError(f"Flashscore {durum}: {url}")


def imza():
    """Günlük liste için gereken x-fsign değeri (sitenin ana JS dosyasından; okunamazsa bilinen değer)."""
    if _durum["imza"]:
        return _durum["imza"]
    deger = YEDEK_IMZA
    try:
        sayfa = _get(ANA + "/volleyball/") or ""
        js = re.search(r'https://static\.flashscore\.com/x/js/core_\d+_\d+\.js', sayfa)
        if js:
            m = re.search(r"feed_sign\s*=\s*'([^']+)'", _get(js.group(0)) or "")
            if m:
                deger = m.group(1)
    except FlashscoreEngel:
        raise
    except Exception:
        pass
    _durum["imza"] = deger
    return deger


def ayristir(veri):
    """Flashscore feed metni -> kayıt listesi. Lig satırları (ZA) sonraki maçlara 'lig' olarak eklenir."""
    kayitlar, lig = [], None
    for parca in (veri or "").split("~"):
        alanlar = {}
        for a in parca.split("¬"):
            if "÷" in a:
                k, v = a.split("÷", 1)
                alanlar[k] = v
        if "ZA" in alanlar:
            lig = alanlar["ZA"]
        elif "AA" in alanlar:
            alanlar["lig"] = lig
            kayitlar.append(alanlar)
    return kayitlar


def gunluk(spor, gun_farki=0):
    """Bugünden gun_farki gün sonraki maçlar (Türkiye saati). eslestir() için DataFrame."""
    metin = _get(f"{FEED}/f_{SPOR_KODU[spor]}_{gun_farki}_3_en_1", {"x-fsign": imza()})
    satirlar = []
    for k in ayristir(metin):
        try:
            ts = int(k.get("AD") or 0)
        except ValueError:
            continue
        satirlar.append({"sofa_id": k["AA"], "ts": ts, "ev": k.get("AE") or k.get("CX"), "dep": k.get("AF"),
                         "ev_kisa": k.get("FH") or "", "dep_kisa": k.get("FK") or "",
                         "ev_id": k.get("PX"), "dep_id": k.get("PY"), "ev_slug": k.get("WU"), "dep_slug": k.get("WV"),
                         "lig": k.get("lig")})
    return pd.DataFrame(satirlar)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


EV_SET = ["BA", "BC", "BE", "BG", "BI"]
DEP_SET = ["BB", "BD", "BF", "BH", "BJ"]


def takim_sonuclari(slug, takim_id):
    """Takımın son maçları (bitenler): [{mac_id, ts, lig, ev_id, ev, dep_id, dep, ev_set, dep_set, setler}]."""
    sayfa = _get(f"{ANA}/team/{slug}/{takim_id}/results/")
    if not sayfa:
        return []
    m = re.search(r'cjs\.initialFeeds\["summary-results"\]\s*=\s*\{\s*data:\s*`([^`]*)`', sayfa)
    if not m:
        return []
    sonuc = []
    for k in ayristir(m.group(1)):
        if k.get("AB") != "3":  # 3 = bitti
            continue
        ev_set, dep_set = _int(k.get("AG")), _int(k.get("AH"))
        if ev_set is None or dep_set is None:
            continue
        setler = [(_int(k.get(a)), _int(k.get(b))) for a, b in zip(EV_SET, DEP_SET)]
        sonuc.append({"mac_id": k["AA"], "ts": _int(k.get("AD")), "lig": k.get("lig"),
                      "ev_id": k.get("PX"), "ev": k.get("AE") or k.get("CX"), "dep_id": k.get("PY"),
                      "dep": k.get("AF"), "ev_set": ev_set, "dep_set": dep_set, "setler": setler})
    return sorted(sonuc, key=lambda x: x["ts"] or 0)
