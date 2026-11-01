"""
Flashscore: günlük maç listeleri, takımların son maçları, futbol maç istatistikleri ve basketbol kutu skorları.
Futbolda Ofsayt'la, basketbolda (NBA/EuroLeague kutu skor) Maçkolik'le iş bölümü yapar: her site daha az istek alır.

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
SPOR_KODU = {"futbol": 1, "basketbol": 3, "voleybol": 12}
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


def engelli_mi():
    return _engelde()


def feed(ad):
    """Maç ayrıntı verisi (df_st_1_..., df_li_1_... gibi). Yoksa None."""
    return _get(f"{FEED}/{ad}", {"x-fsign": imza()})


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


def gunler(spor, tarihler, yazdir=print):
    """Verilen günlerin (Türkiye saati) maç listeleri; gün başına 1 istek. Flashscore yalnızca ±7 günü verir."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    bugun = datetime.now(ZoneInfo("Europe/Istanbul")).date()
    parcalar = []
    for g in sorted(set(tarihler)):
        fark = (g - bugun).days
        if not -7 <= fark <= 7:
            continue
        try:
            parcalar.append(gunluk(spor, fark))
        except FlashscoreEngel:
            raise
        except Exception as hata:
            yazdir(f"  Flashscore {g:%d.%m} listesi alınamadı: {hata}")
    parcalar = [p for p in parcalar if len(p)]
    return pd.concat(parcalar, ignore_index=True).drop_duplicates("sofa_id") if parcalar else pd.DataFrame()


# ---------------------------------------------------------------- futbol
def _gomulu(sayfa, ad):
    m = re.search(r'cjs\.initialFeeds\["' + re.escape(ad) + r'"\]\s*=\s*\{\s*data:\s*`([^`]*)`', sayfa or "")
    return m.group(1) if m else ""


HAZIRLIK = re.compile(r"friendl", re.I)


def futbol_sonuclari(slug, takim_id):
    """Takımın son biten maçları (eskiden yeniye) ve sıradaki maçının zamanı. Takım başına 1 istek.
    İlk yarı skoru bu listede yok (None); istatistik çekilirken Maçkolik arşivinden tamamlanır."""
    sayfa = _get(f"{ANA}/team/{slug}/{takim_id}/results/")
    if not sayfa:
        return [], None
    sonuc = []
    for k in ayristir(_gomulu(sayfa, "summary-results")):
        ev_g, dep_g = _int(k.get("AG")), _int(k.get("AH"))
        if k.get("AB") != "3" or ev_g is None or dep_g is None or HAZIRLIK.search(k.get("lig") or ""):
            continue
        sonuc.append({"mac_id": k["AA"], "ts": _int(k.get("AD")), "lig": _lig_adi(k.get("lig")),
                      "ev_id": k.get("PX"), "ev": k.get("AE") or k.get("CX"), "dep_id": k.get("PY"),
                      "dep": k.get("AF"), "ev_gol": ev_g, "dep_gol": dep_g, "ev_iy": None, "dep_iy": None})
    simdi = time.time()
    sonraki = [t for k in ayristir(_gomulu(sayfa, "summary-fixtures"))
               if (t := _int(k.get("AD"))) and t > simdi - 3 * 3600]
    return sorted(sonuc, key=lambda x: x["ts"] or 0), (min(sonraki) if sonraki else None)


BUYUK_LIG = {"ENGLAND: Premier League": "Premier League", "SPAIN: LaLiga": "La Liga", "SPAIN: La Liga": "La Liga", "ITALY: Serie A": "Serie A",
             "GERMANY: Bundesliga": "Bundesliga", "FRANCE: Ligue 1": "Ligue 1"}


def _lig_adi(lig):
    """Büyük 5 lig veritabanındaki (Ofsayt/SofaScore) adlarıyla; diğerleri ülkesiyle ('TURKEY: Super Lig') —
    farklı ülkelerin aynı adlı ligleri karışmasın."""
    lig = (lig or "").strip()
    return BUYUK_LIG.get(lig, lig) or None


# Flashscore etiketi -> SofaScore/Ofsayt ile aynı anahtar. "a% (x/y)" biçimindekilerden hangi sayının alınacağı:
# "isabetli" = x, "toplam" = y
FUTBOL_ETIKET = {
    "Expected goals (xG)": "expectedGoals", "Ball possession": "ballPossession", "Total shots": "totalShotsOnGoal",
    "Shots on target": "shotsOnGoal", "Shots off target": "shotsOffGoal", "Blocked shots": "blockedScoringAttempt",
    "Shots inside the box": "totalShotsInsideBox", "Shots outside the box": "totalShotsOutsideBox",
    "Hit the woodwork": "hitWoodwork", "Big chances": "bigChanceCreated",
    "Touches in opposition box": "touchesInOppBox", "Fouls": "fouls", "Offsides": "offsides",
    "Corner kicks": "cornerKicks", "Yellow cards": "yellowCards", "Red cards": "redCards", "Throw ins": "throwIns",
    "Goal kicks": "goalKicks", "Interceptions": "interceptionWon", "Clearances": "totalClearance",
    "Goalkeeper saves": "goalkeeperSaves", "Duels won": "duelWon",
}
FUTBOL_ORANLI = {  # etiket -> [(anahtar, "isabetli" | "toplam")]
    "Passes": [("accuratePasses", "isabetli"), ("passes", "toplam")],
    "Long passes": [("accurateLongBalls", "isabetli")],
    "Crosses": [("accurateCross", "isabetli")],
    "Tackles": [("totalTackle", "toplam")],
}
FUTBOL_BOLUM = {"Match": "", "1st Half": "IY_", "2nd Half": "2Y_"}


def _fs_sayi(v):
    v = (v or "").strip().replace("%", "")
    m = re.match(r"-?\d+(?:\.\d+)?", v)
    if not m:
        return None
    return float(m.group(0)) if "." in m.group(0) else int(m.group(0))


def _oran(v):
    m = re.search(r"\((\d+)/(\d+)\)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def futbol_istatistik_ayristir(metin):
    """df_st_1_ verisi -> {"ev_cornerKicks": 8, "IY_dep_yellowCards": 1, ...}. İstatistik yoksa boş sözlük."""
    satir, on, gorulen = {}, None, set()
    for parca in (metin or "").split("~"):
        a = dict(x.split("÷", 1) for x in parca.split("¬") if "÷" in x)
        if "SE" in a:
            on = FUTBOL_BOLUM.get(a["SE"])
            gorulen = set()
            continue
        if on is None or "SG" not in a:
            continue
        etiket = a["SG"].strip()
        if etiket in gorulen:  # aynı istatistik birden çok başlık altında tekrar edilir
            continue
        gorulen.add(etiket)
        if etiket in FUTBOL_ETIKET:
            satir[f"{on}ev_{FUTBOL_ETIKET[etiket]}"] = _fs_sayi(a.get("SH"))
            satir[f"{on}dep_{FUTBOL_ETIKET[etiket]}"] = _fs_sayi(a.get("SI"))
        for anahtar, hangi in FUTBOL_ORANLI.get(etiket, []):
            i = 0 if hangi == "isabetli" else 1
            satir[f"{on}ev_{anahtar}"] = _oran(a.get("SH"))[i]
            satir[f"{on}dep_{anahtar}"] = _oran(a.get("SI"))[i]
    return satir


def futbol_istatistikleri(mac_id):
    return futbol_istatistik_ayristir(feed(f"df_st_1_{mac_id}"))


def ilk_yari_skoru(mac_id):
    """(ev, dep) ilk yarı golleri — maç özeti verisinden (yalnızca Maçkolik arşivinde bulunamazsa kullanılır)."""
    m = re.search(r"AC÷1st Half¬IG÷(\d+)¬IH÷(\d+)", feed(f"df_sui_1_{mac_id}") or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


# ---------------------------------------------------------------- basketbol kutu skor
FS_KUTU = {"PTS": "sayi", "REB": "rib", "AST": "ast", "FGM": "_fg_i", "FGA": "_fg_a", "2PM": "iki_i",
           "2PA": "iki_a", "3PM": "uc_i", "3PA": "uc_a", "FTM": "sa_i", "FTA": "sa_a", "PF": "faul", "ST": "tc",
           "TO": "tk", "BS": "blok"}


def kadro_ayristir(metin):
    """df_li_1_ -> {oyuncu kimliği: (taraf 1=ev/2=dep, ilk5 mi)}."""
    # Bölümler: ilk 5 (ev, dep), yedekler (ev, dep), antrenörler... Her tarafın ilk grubu ilk 5'tir.
    sonuc, taraf, grup, kac = {}, None, None, {1: 0, 2: 0}
    for parca in (metin or "").split("~"):
        a = dict(x.split("÷", 1) for x in parca.split("¬") if "÷" in x)
        if "LC" in a:
            taraf = _int(a["LC"])
            if taraf in kac:
                kac[taraf] += 1
                grup = kac[taraf]
        if "LP" in a and taraf in (1, 2) and a["LP"] not in sonuc:
            sonuc[a["LP"]] = (taraf, grup == 1)
    return sonuc


def oyuncu_ayristir(metin):
    """df_psn_1_ (genel sekme) -> [{fs_id, ad, slug, kisa_takim, sn, sayi, rib, ...}] — oynayanlar."""
    basliklar, satirlar, sekme = [], [], None
    for parca in (metin or "").split("~"):
        a = dict(x.split("÷", 1) for x in parca.split("¬") if "÷" in x)
        if "PA" in a:
            sekme = a["PA"]
            if satirlar:
                break  # yalnızca ilk (genel) sekme
            continue
        if "PF" in a:
            basliklar.append(a["PF"])
            continue
        if "PJ" in a and "PC" in a:
            degerler = a["PC"].split("|")
            k = dict(zip(basliklar[1:], degerler))  # ilk başlık "Player"
            m = re.search(r"/player/([^/]+)/([^/]+)/", a.get("PK", ""))
            dk = re.match(r"(\d+):(\d+)", k.get("MIN", ""))
            sn = int(dk.group(1)) * 60 + int(dk.group(2)) if dk else 0
            if not m or sn == 0:
                continue
            s = {"fs_id": m.group(2), "slug": m.group(1), "ad": a["PJ"], "kisa_takim": a.get("PN"), "sn": sn}
            for b, anahtar in FS_KUTU.items():
                v = k.get(b, "-")
                s[anahtar] = int(v) if re.fullmatch(r"-?\d+", v or "") else 0
            if not s["iki_a"] and s["_fg_a"]:  # NBA'de 2'lik sütunları boş: alan golü - 3'lük
                s["iki_i"], s["iki_a"] = s["_fg_i"] - s["uc_i"], s["_fg_a"] - s["uc_a"]
            del s["_fg_i"], s["_fg_a"]
            satirlar.append(s)
    return satirlar


def kutu_skor(mac_id):
    """Maçın oyuncu istatistikleri: [{..., taraf (1/2), ilk5, sira}]. Veri yoksa boş liste. 2 istek."""
    oyuncular = oyuncu_ayristir(feed(f"df_psn_1_{mac_id}"))
    if not oyuncular:
        return []
    kadro = kadro_ayristir(feed(f"df_li_1_{mac_id}"))
    kisa = {}  # kadroda olmayan oyuncunun tarafı: aynı kısaltmalı takım arkadaşından
    for o in oyuncular:
        if o["fs_id"] in kadro:
            kisa.setdefault(o["kisa_takim"], kadro[o["fs_id"]][0])
    sira = {1: 0, 2: 0}
    for o in oyuncular:
        taraf, ilk5 = kadro.get(o["fs_id"], (kisa.get(o["kisa_takim"]), False))
        o["taraf"], o["ilk5"] = taraf, int(ilk5)
        if taraf in sira:
            o["sira"] = sira[taraf]
            sira[taraf] += 1
    return [o for o in oyuncular if o["taraf"] in (1, 2)]
