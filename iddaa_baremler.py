"""
iddaa.com'daki açık maçların Alt/Üst baremlerini ve oranlarını çekip futbol.db'ye ('baremler' tablosu) yazar.

Kullanım:
    python iddaa_baremler.py                # bugünkü tüm iddaa maçları (tüm ligler)
    python iddaa_baremler.py --gun 3        # önümüzdeki 3 gün
    python iddaa_baremler.py --lig buyuk    # yalnızca 5 büyük lig + Avrupa kupaları
    python iddaa_baremler.py --ham ham.json # ham yanıtları da dosyaya kaydet (hata ayıklama)

Kaynak: iddaa.com'un web sitesinin kullandığı herkese açık API (sportsbookv2.iddaa.com).
Resmi bir API değildir; yapı değişirse ayrıştırma güncellenmelidir.

Takım adları: iddaa Türkçe adlar kullanır (ör. "Bayern Münih"). Adlar otomatik eşleştirilir;
eşleşmeyenler ekrana yazılır. Elle düzeltmek için takim_eslesme.csv dosyasına
    iddaa_adi;veritabani_adi
satırı ekleyin.
"""
import argparse
import difflib
import functools
import json
import re
import sqlite3
import time
import unicodedata
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

KLASOR = Path(__file__).resolve().parent
DB = KLASOR / "futbol.db"
ESLESME = KLASOR / "takim_eslesme.csv"
API = "https://sportsbookv2.iddaa.com/sportsbook"
TZ = ZoneInfo("Europe/Istanbul")

# iddaa yarışma kimliği (event.ci) -> veritabanındaki lig adı
LIGLER = {43: "Premier League", 129: "La Liga", 143: "Serie A", 45: "Bundesliga", 381: "Ligue 1"}
# Avrupa kupaları: takımlardan en az biri veritabanındaysa taranır (o takımın son lig maçlarına bakılır)
KUPALAR = {126: "Şampiyonlar Ligi", 46194: "Şampiyonlar Ligi", 46195: "Avrupa Ligi",
           46196: "Konferans Ligi", 23986: "Konferans Ligi"}

KOLONLAR = ["event_id", "lig", "tarih", "ev_iddaa", "dep_iddaa", "ev", "dep", "pazar_adi", "stat",
            "periyot", "kapsam", "cizgi", "alt_oran", "ust_oran", "cekilme"]

# Pazar adı yapılandırmada yoksa kullanılacak bilinen alt türler (st)
YEDEK_PAZAR = {
    101: "Alt/Üst {0}",
    603: "Ev Sahibi Alt/Üst {0}",
    604: "Deplasman Alt/Üst {0}",
    822: "Ev Sahibi Toplam Korner Altı/Üstü {0}",
    823: "Deplasman Toplam Korner Altı/Üstü {0}",
}

# Eşik pazarları ("Inter 20+", "Toplam 31+", "İY Toplam 14+"): pazar adı yapılandırmada bulunamazsa
# st numarasından istatistik. Seçeneklerin büyüklüklerinden çıkarıldı (Inter–Club Brugge, 14.10.2026):
# ör. st 913 "Toplam 10+ … 13+" isabetli şut, st 917 "Toplam 32+ … 38+" taç.
YEDEK_ESIK = {
    890: "goalkeeperSaves",   # seçenekler kaleci adı + Toplam 6+ … 8+
    912: "totalShotsOnGoal",
    913: "shotsOnGoal",
    914: "fouls",
    917: "throwIns",
    918: "goalKicks",
}

BASLIKLAR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.iddaa.com",
    "Referer": "https://www.iddaa.com/",
}


def get(yol, deneme=3):
    for i in range(deneme):
        try:
            req = urllib.request.Request(f"{API}/{yol}", headers=BASLIKLAR)
            with urllib.request.urlopen(req, timeout=30) as r:
                veri = json.loads(r.read().decode("utf-8"))
            if veri.get("isSuccess") is False:
                raise RuntimeError(veri.get("message"))
            return veri["data"]
        except Exception as e:
            if i == deneme - 1:
                raise
            print(f"  tekrar deneniyor ({e})")
            time.sleep(2 * (i + 1))


# ---------------------------------------------------------------- pazar sınıflandırma
def tr_kucuk(s):
    return s.replace("İ", "i").replace("I", "ı").lower()


STAT_KELIMELERI = [  # sıra önemli: özelden genele
    ("isabetli şut", "shotsOnGoal"),
    ("şut", "totalShotsOnGoal"),
    ("korner", "cornerKicks"),
    ("sarı kart", "yellowCards"),
    ("kart", "kart"),
    ("faul", "fouls"),
    ("ofsayt", "offsides"),
    ("taç", "throwIns"),
    ("kurtarış", "goalkeeperSaves"),
    ("kale vuruşu", "goalKicks"),
    (" aut ", "goalKicks"),
    ("gol", "gol"),
]
DISLA = ["oyuncu", "maç sonucu", " ve ", "periyot", "uzatma", "handikap", "hangi", "kırmızı",
         "penaltı", "aralık", "dakika", " tek ", " çift ", "kim "]
NOTR = ["alt/üst", "altı/üstü", "alt / üst", "alt", "üst", "altı", "üstü", "ev sahibi", "deplasman",
        "toplam", "1. yarı", "ilk yarı", "2. yarı", "ikinci yarı", "maç", "sayısı", "aralığı", "aralık",
        "kaç", "{0}", "/", "(", ")"]


def siniflandir(ad, aralik=False):
    """Pazar adından (istatistik, periyot, kapsam) çıkarır; tanınmazsa None.
    aralik=True: "0-2 / 3-4 / 5+" gibi aralık pazarı (adında "aralık" geçmesine izin verilir)."""
    a = f" {tr_kucuk(ad)} "
    disla = [x for x in DISLA if not (aralik and x == "aralık")]
    if any(x in a for x in disla):
        return None
    periyot = "IY_" if re.search(r"(1\.\s?yarı|ilk yarı)", a) else \
              "2Y_" if re.search(r"(2\.\s?yarı|ikinci yarı)", a) else ""
    kapsam = "ev" if "ev sahibi" in a else "dep" if "deplasman" in a else "toplam"
    for kelime, stat in STAT_KELIMELERI:
        if kelime in a:
            return stat, periyot, kapsam
    # İstatistik kelimesi yoksa ("Alt/Üst {0}", "Ev Sahibi Alt/Üst {0}") gol pazarıdır;
    # ama adda tanımadığımız başka bir kelime varsa riske girme.
    kalan = a
    for k in sorted(NOTR, key=len, reverse=True):
        kalan = kalan.replace(k, " ")
    return ("gol", periyot, kapsam) if not kalan.strip() else None


# ---------------------------------------------------------------- takım eşleştirme
EXONIM = {  # iddaa'daki Türkçe / kısaltılmış adlar -> veritabanı adına yakın biçim
    "munih": "munchen", "marsilya": "marseille", "psg": "paris saint germain",
    "paris sg": "paris saint germain", "m gladbach": "mgladbach", "monchengladbach": "mgladbach",
    "gladbach": "mgladbach", "athletic bilbao": "athletic club", "bilbao": "athletic club",
    "lyon": "lyonnais", "brest": "brestois", "rennes": "rennais", "racing santander": "racing",
    "la coruna": "coruna", "inter milan": "inter", "wolves": "wolverhampton",
    "nottingham": "nottingham forest", "man city": "manchester city", "man utd": "manchester united",
    "man united": "manchester united", "utd": "united", "spurs": "tottenham", "tottenham hotspur": "tottenham",
    "brighton": "brighton hove albion", "newcastle": "newcastle united", "leeds": "leeds united",
    "kizil yildiz": "crvena zvezda", "kopenhag": "kobenhavn",
}
DOLGU = {"fc", "cf", "ac", "as", "ssc", "sc", "rc", "ud", "cd", "sv", "tsg", "fsv", "vfb", "vfl",
         "afc", "club", "calcio", "olympique", "stade", "de", "hotspur", "1", "04", "05", "07",
         "real", "deportivo", "bk", "ogc", "sd", "rcd", "us", "hsc", "the"}


# NFKD'nin ayıramadığı harfler (Łódź, Đorđe, Ørsted, Straße...)
_HARF = str.maketrans({"ł": "l", "Ł": "l", "đ": "d", "Đ": "d", "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae",
                       "œ": "oe", "ß": "ss", "þ": "th", "ð": "d", "ħ": "h"})


@functools.lru_cache(maxsize=None)
def norm(ad):
    s = tr_kucuk(ad).replace("ı", "i").translate(_HARF)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]", " ", s.replace("'", "").replace("&", " "))
    s = re.sub(r"\s+", " ", s).strip()
    for k, v in sorted(EXONIM.items(), key=lambda x: -len(x[0])):
        s = re.sub(rf"\b{k}\b", v, s)
    return s


@functools.lru_cache(maxsize=None)
def anahtar(ad):
    return " ".join(t for t in norm(ad).split() if t not in DOLGU) or norm(ad)


def _kelime_uyar(q, c):
    """iddaa kelimesi q, aday kelimesi c ile aynı mı ya da onun kısaltması mı (B. -> Bayer, Man -> Manchester)."""
    if q == c:
        return True
    if len(q) == 1:
        return c.startswith(q)
    return len(q) >= 3 and c.startswith(q)


def _katı_puan(a, b):
    """iddaa adının her kelimesi adayın bir kelimesine uymalı. Uyuyorsa adayın kapsanma oranı, yoksa 0."""
    qa, cb = a.split(), b.split()
    if not qa or not cb:
        return 0.0
    kullanilan = set()
    for q in qa:
        bulundu = next((k for k, c in enumerate(cb) if k not in kullanilan and _kelime_uyar(q, c)), None)
        if bulundu is None:
            return 0.0
        kullanilan.add(bulundu)
    if all(len(q) == 1 for q in qa):  # yalnızca baş harflerle eşleşme yetmez
        return 0.0
    return len(kullanilan) / len(cb)


def esles(iddaa_adi, adaylar, elle, gevsek=True):
    """iddaa'daki takım adını veritabanındaki adla eşleştirir.
    Önce katı kelime eşleşmesi; gevsek=True ise (aynı lig içinde, küçük aday listesi) bulanık yedek."""
    if iddaa_adi in elle:
        return elle[iddaa_adi]
    a = anahtar(iddaa_adi)
    puanlar = sorted(((_katı_puan(a, anahtar(c)), c) for c in adaylar), reverse=True)
    if puanlar and puanlar[0][0] > 0:
        # Eşit puanlı iki aday varsa (ör. "Paris" -> PSG / Paris FC) belirsiz: eşleştirme
        if len(puanlar) > 1 and puanlar[1][0] == puanlar[0][0]:
            return None
        return puanlar[0][1]
    if not gevsek:
        return None
    en_iyi, puan = None, 0.0
    for aday in adaylar:
        oran = difflib.SequenceMatcher(None, a, anahtar(aday)).ratio()
        if oran > puan:
            en_iyi, puan = aday, oran
    return en_iyi if puan >= 0.75 else None


def elle_eslesmeler():
    if not ESLESME.exists():
        return {}
    d = pd.read_csv(ESLESME, sep=";", header=None, names=["iddaa", "db"], comment="#", dtype=str)
    return dict(zip(d["iddaa"].str.strip(), d["db"].str.strip()))


def guncel_takimlar(con):
    df = pd.read_sql("SELECT lig, takim, MAX(tarih) AS son FROM maclar GROUP BY lig, takim", con)
    df["son"] = pd.to_datetime(df["son"])
    df["lig_son"] = df.groupby("lig")["son"].transform("max")
    df = df[(df["lig_son"] - df["son"]).dt.days <= 60]
    return {lig: list(g["takim"]) for lig, g in df.groupby("lig")}


# ---------------------------------------------------------------- ana akış
ARALIK = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)|\+)\s*([a-zçğıöşü]*)\s*$", re.I)


def aralik_cozumle(adlar):
    """{"0-2": 2.1, "3-4": 2.6, "5+": 3.4} -> [(alt_sinir, ust_sinir veya None, oran), ...] (sıralı).
    Aralıklar 0'dan başlayıp boşluksuz ilerlemiyorsa None."""
    parcalar = []
    for ad, oran in adlar.items():
        m = ARALIK.match(str(ad))
        if not m or not oran:
            return None
        a, b = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
        parcalar.append((a, b, float(oran)))
    parcalar.sort()
    if len(parcalar) < 2 or parcalar[0][0] != 0 or parcalar[-1][1] is not None:
        return None
    for (a1, b1, _), (a2, _, _) in zip(parcalar, parcalar[1:]):
        if b1 is None or a2 != b1 + 1:
            return None
    return parcalar


def aralik_baremleri(parcalar):
    """Aralık oranlarından her sınırda Alt/Üst baremi: Alt k.5 = ilk aralıkların birleşimi.
    Birleşik seçenek oranı = 1 / Σ(1/oran) (bahis sitesinin kendi fiyatlamasıyla tutarlı)."""
    ters = [1 / o for _, _, o in parcalar]
    sonuc = []
    for i in range(len(parcalar) - 1):
        cizgi = parcalar[i][1] + 0.5
        alt_t, ust_t = sum(ters[: i + 1]), sum(ters[i + 1:])
        alt_o, ust_o = 1 / alt_t, 1 / ust_t
        # Marj birleşik seçeneklerde birikir; 1.05'in altındaki oran anlamsız (neredeyse kesin taraf) -> atla
        if alt_o < 1.05 or ust_o < 1.05:
            continue
        sonuc.append((cizgi, round(alt_o, 2), round(ust_o, 2)))
    return sonuc


ESIK = re.compile(r"^\s*(?:(İY|1Y|2Y)\s+)?(.+?)\s+(\d+)\+\s*$", re.I)


def esik_stat(ad, st):
    """Eşik pazarının istatistiği: önce pazar adındaki kelime, yoksa st tablosu. Oyuncu pazarları None."""
    if ad:
        a = f" {tr_kucuk(ad)} "
        if "oyuncu" in a or "özel" in a:
            return None
        for kelime, stat in STAT_KELIMELERI:
            if kelime in a and stat != "gol":
                return stat
    return YEDEK_ESIK.get(st)


def esik_baremleri(m, ad, ev_adi, dep_adi):
    """{"Inter 20+": 1.53, "Toplam 31+": 1.56, "İY Toplam 14+": 1.42, ...} -> Üst baremleri.
    "N+" = Üst N-0.5. iddaa bu pazarlarda yalnızca Üst tarafını sunar (Alt oranı yok)."""
    ev_k, dep_k = tr_kucuk(ev_adi or "").strip(), tr_kucuk(dep_adi or "").strip()
    sonuc = []
    for o in m.get("o", []):
        e = ESIK.match(str(o.get("n", "")))
        if not e or not o.get("odd"):
            continue
        on, kim, sayi = (e.group(1) or "").upper(), tr_kucuk(e.group(2)).strip(), int(e.group(3))
        periyot = {"İY": "IY_", "1Y": "IY_", "2Y": "2Y_"}.get(on.replace("I", "İ") if on else "", "")
        if kim in ("toplam", "maç toplamı", "toplamı"):
            kapsam = "toplam"
        elif kim == ev_k:
            kapsam = "ev"
        elif kim == dep_k:
            kapsam = "dep"
        else:
            continue  # oyuncu adı (ör. kaleci) -> takımı bilinmiyor, atla
        sonuc.append({"pazar_adi": f"{ad or 'iddaa eşik pazarı'}: {o.get('n')}", "periyot": periyot,
                      "kapsam": kapsam, "cizgi": sayi - 0.5, "alt_oran": None, "ust_oran": float(o["odd"])})
    return sonuc


def baremleri_cikar(olay, pazar_adlari, ev_adi=None, dep_adi=None):
    """Maç detayındaki pazarlardan barem satırları çıkarır:
    Alt/Üst pazarları (iki taraflı), aralık pazarları (0-2 / 3-4 / 5+) ve eşik pazarları (Inter 20+ / Toplam 31+)."""
    ev_adi, dep_adi = ev_adi or olay.get("hn"), dep_adi or olay.get("an")
    satirlar, taninmayan = [], set()
    for m in olay.get("m", []):
        if m.get("sov") is None:
            adlar_ = {o.get("n"): o.get("odd") for o in m.get("o", [])}
            # Eşik pazarı: seçeneklerin çoğu "<ad> N+" biçiminde
            esikler = [n for n in adlar_ if ESIK.match(str(n))]
            if adlar_ and len(esikler) >= max(2, len(adlar_) // 2):
                ad = pazar_adlari.get(f"{m.get('t')}_{m.get('st')}")
                stat = esik_stat(ad, m.get("st"))
                if not stat:
                    if ad and "oyuncu" not in tr_kucuk(ad):
                        taninmayan.add(f"{ad} (eşik, st={m.get('st')})")
                    elif not ad:
                        taninmayan.add(f"st={m.get('st')} eşik pazarı (adı yok): {', '.join(list(adlar_)[:4])}…")
                    continue
                for b in esik_baremleri(m, ad, ev_adi, dep_adi):
                    satirlar.append({**b, "stat": stat, "oncelik": 2})
                continue
            parcalar = aralik_cozumle(adlar_) if len(adlar_) >= 2 else None
            if parcalar:
                ad = pazar_adlari.get(f"{m.get('t')}_{m.get('st')}")
                sinif = siniflandir(ad, aralik=True) if ad else None
                if not sinif:
                    if ad:
                        taninmayan.add(f"{ad} (aralık)")
                    continue
                stat, periyot, kapsam = sinif
                for cizgi, alt_o, ust_o in aralik_baremleri(parcalar):
                    satirlar.append({"pazar_adi": f"{ad} → Alt/Üst {cizgi:g}", "stat": stat,
                                     "periyot": periyot, "kapsam": kapsam, "cizgi": cizgi,
                                     "alt_oran": alt_o, "ust_oran": ust_o, "oncelik": 1})
                continue
        sov = m.get("sov")
        adlar = {o.get("n"): o.get("odd") for o in m.get("o", [])}
        if sov is None or set(adlar) != {"Alt", "Üst"}:
            continue
        ad = pazar_adlari.get(f"{m.get('t')}_{m.get('st')}") or YEDEK_PAZAR.get(m.get("st"))
        if not ad:
            taninmayan.add(f"t={m.get('t')} st={m.get('st')} (adı yok)")
            continue
        sinif = siniflandir(ad)
        if not sinif:
            taninmayan.add(ad)
            continue
        stat, periyot, kapsam = sinif
        try:
            cizgi = float(str(sov).replace(",", "."))
        except ValueError:
            continue
        satirlar.append({"pazar_adi": ad.replace("{0}", str(sov)), "stat": stat, "periyot": periyot,
                         "kapsam": kapsam, "cizgi": cizgi,
                         "alt_oran": adlar["Alt"], "ust_oran": adlar["Üst"], "oncelik": 0})
    # Aynı barem birden çok pazardan geldiyse öncelik: Alt/Üst (iki taraflı) > aralık > eşik (tek taraflı)
    gorulen, temiz = set(), []
    for r in sorted(satirlar, key=lambda r: r["oncelik"]):
        k = (r["stat"], r["periyot"], r["kapsam"], r["cizgi"])
        if k not in gorulen:
            gorulen.add(k)
            temiz.append({k_: v for k_, v in r.items() if k_ != "oncelik"})
    return temiz, taninmayan


def son_guncelleme():
    """Son barem güncellemesinin özeti (hiç güncellenmediyse None)."""
    try:
        with sqlite3.connect(DB) as con:
            r = con.execute("SELECT cekilme, gun, lig_mac, kupa_mac FROM barem_meta").fetchone()
    except sqlite3.Error:
        return None
    return dict(zip(["cekilme", "gun", "lig_mac", "kupa_mac"], r)) if r else None


# Kupa maçları hiç çekilmez (ör. Ziraat Türkiye Kupası, Slovenya Kupası, FA Cup, Copa ..., Süper Kupa).
# Lig biçimindeki Avrupa yarışmaları (Şampiyonlar Ligi, Avrupa Ligi, Konferans Ligi, Eurocup) kupa sayılmaz.
KUPA_DESENI = re.compile(r"\b(kupa\w*|cup|copa|coppa|coupe|pokal|ta[cç]a|trophy|beker)\b", re.I)


def kupa_mi(yarisma_adi):
    return bool(yarisma_adi) and bool(KUPA_DESENI.search(tr_kucuk(str(yarisma_adi))))


def kupalari_cikar(olaylar, adlar, yazdir=None):
    """Kupa yarışmalarının maçlarını listeden çıkarır."""
    kalan, cikan = [], {}
    for e in olaylar:
        ad = adlar.get(e.get("ci"), ("",))[0]
        if kupa_mi(ad):
            cikan[ad] = cikan.get(ad, 0) + 1
        else:
            kalan.append(e)
    if cikan and yazdir:
        yazdir(f"Kupa maçları çekilmedi: {sum(cikan.values())} maç ("
               + ", ".join(f"{k} {v}" for k, v in sorted(cikan.items(), key=lambda x: -x[1])[:8])
               + (" …" if len(cikan) > 8 else "") + ").")
    return kalan


def yarismalar():
    """iddaa futbol yarışmaları: {ci: (ad, ülke kodu, ülke adı)}."""
    try:
        veri = get("competitions")
    except Exception:
        return {}
    liste = veri if isinstance(veri, list) else veri.get("competitions", veri.get("c", []))
    return {c["i"]: (c.get("n", str(c["i"])), c.get("cid"), _ulke_adi(c)) for c in liste
            if isinstance(c, dict) and str(c.get("si")) == "1"}


def _ulke_adi(c):
    """"İngiltere Premier Lig" (kısa ad "Premier Lig") -> "İngiltere"."""
    ad, kisa = (c.get("n") or "").strip(), (c.get("sn") or "").strip()
    if kisa and ad.endswith(kisa) and len(ad) > len(kisa):
        return ad[: -len(kisa)].strip(" ,-")
    if str(c.get("cid") or "").upper() in ("INT", "WW", "EU", ""):
        return "Uluslararası"
    return ad.split(" ")[0] if ad else "Diğer"


def lig_ozeti(gun=7):
    """Önümüzdeki günlerde iddaa'da maçı olan futbol yarışmaları ve maç sayıları (arayüzdeki lig seçimi için)."""
    olaylar = get("events?st=1&type=0&version=0").get("events", [])
    adlar = yarismalar()
    simdi, sinir = (t.timestamp() for t in zaman_araligi(gun))
    sayac = {}
    for e in olaylar:
        if simdi < e.get("d", 0) <= sinir and not kupa_mi(adlar.get(e.get("ci"), ("",))[0]):
            sayac[e.get("ci")] = sayac.get(e.get("ci"), 0) + 1
    d = pd.DataFrame([{"ci": ci, "ad": adlar.get(ci, (f"Yarışma {ci}",))[0],
                       "ulke": adlar.get(ci, (None, None))[1],
                       "ulke_adi": adlar.get(ci, (None, None, "Diğer"))[2] or "Diğer", "mac": n}
                      for ci, n in sayac.items()])
    if not len(d):
        return d
    d["ulke_mac"] = d.groupby("ulke_adi")["mac"].transform("sum")
    return d.sort_values(["ulke_mac", "ulke_adi", "mac"], ascending=[False, True, False])


VARSAYILAN_LIGLER = list(LIGLER) + list(KUPALAR)

BULTEN_KOLONLAR = ["event_id", "lig", "ulke", "tarih", "ev_iddaa", "dep_iddaa", "ev", "dep", "ev_sofa", "dep_sofa",
                   "sofa_id", "custom_id", "mk_id", "kaynak", "ms1", "ms0", "ms2", "iy1", "iy0", "iy2", "kg_var", "kg_yok",
                   "au15_alt", "au15_ust", "au25_alt", "au25_ust", "au35_alt", "au35_ust",
                   "iy_au05_alt", "iy_au05_ust", "iy_au15_alt", "iy_au15_ust",
                   "barem_sayisi", "istatistik_pazari", "cekilme"]


def _oran(v):
    """iddaa'da 1.00 oran = kapalı seçenek."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 1.0 else None


def bulten_oranlari(pazarlar):
    """Maç sonucu, İY sonucu, karşılıklı gol ve gol Alt/Üst oranları (bülten tablosu için)."""
    o = {}
    for m in pazarlar or []:
        t, st, sov = m.get("t"), m.get("st"), str(m.get("sov") or "")
        ad = {x.get("n"): _oran(x.get("odd")) for x in m.get("o", [])}
        if t == 1 and st == 1:
            o.update(ms1=ad.get("1"), ms0=ad.get("0"), ms2=ad.get("2"))
        elif st == 88:
            o.update(iy1=ad.get("1"), iy0=ad.get("0"), iy2=ad.get("2"))
        elif st == 89:
            o.update(kg_var=ad.get("Var"), kg_yok=ad.get("Yok"))
        elif st == 101 and sov in ("1.5", "2.5", "3.5"):
            k = sov.replace(".", "")
            o.update({f"au{k}_alt": ad.get("Alt"), f"au{k}_ust": ad.get("Üst")})
        elif st == 60 and sov in ("0.5", "1.5"):
            k = sov.replace(".", "")
            o.update({f"iy_au{k}_alt": ad.get("Alt"), f"iy_au{k}_ust": ad.get("Üst")})
    return o


def zaman_araligi(gun):
    """gun=0: bugün gece yarısına kadar; gun=N: önümüzdeki N×24 saat."""
    simdi = datetime.now(TZ)
    if gun <= 0:
        sinir = simdi.replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        sinir = simdi + timedelta(days=gun)
    return simdi, sinir


def _mod(takim_verisi):
    if takim_verisi is True:
        return "akilli"
    if not takim_verisi:
        return "yok"
    return takim_verisi


def _detaylari_cek(secili, yazdir, isci=6):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    detaylar = {}
    with ThreadPoolExecutor(isci) as havuz:
        isler = {havuz.submit(get, f"event/{e['i']}"): e for e in secili}
        for k, f in enumerate(as_completed(isler), 1):
            e = isler[f]
            try:
                detaylar[e["i"]] = f.result()
            except Exception as hata:
                yazdir(f"  ayrıntı alınamadı {e.get('hn')} - {e.get('an')}: {hata} (bülten oranları kullanılacak)")
            if k % 50 == 0 or k == len(isler):
                yazdir(f"  iddaa maç ayrıntıları: {k}/{len(isler)}")
    return detaylar


def _tablolari_yaz(bulten, satirlar, cekilme, gun, buyuk5, toplam):
    df = pd.DataFrame(satirlar, columns=KOLONLAR)
    bt = pd.DataFrame(bulten, columns=BULTEN_KOLONLAR)
    with sqlite3.connect(DB) as con:
        con.execute("DROP TABLE IF EXISTS baremler")
        con.execute("CREATE TABLE baremler (" + ", ".join(f'"{k}"' for k in KOLONLAR) + ")")
        if len(df):
            df.to_sql("baremler", con, if_exists="append", index=False)
        bt.to_sql("bulten", con, if_exists="replace", index=False)
        con.execute("DROP TABLE IF EXISTS barem_meta")
        con.execute("CREATE TABLE barem_meta (cekilme, gun, lig_mac, kupa_mac)")
        con.execute("INSERT INTO barem_meta VALUES (?, ?, ?, ?)", (cekilme, gun, buyuk5, toplam - buyuk5))
    return df


def guncelle(gun=0, ham_dosya=None, yazdir=print, ligler="hepsi", takim_verisi="akilli", ilerleme=None):
    """iddaa'da açık maçları, oranlarını ve baremlerini çeker. Takımların son maçları (skor, ilk yarı) Maçkolik'in
    günlük sonuç arşivinden; korner/kart/şut istatistikleri yalnızca gereken maçlar için Ofsayt.com'dan gelir.
    (barem satırı sayısı, verisi bulunamayan takımlar, tanınmayan pazarlar) döner.

    gun: 0 -> bugün (gece yarısına kadar), N -> önümüzdeki N gün.
    ligler: "hepsi" -> iddaa'daki tüm futbol maçları; liste -> yalnızca bu yarışma kimlikleri (ci);
            None -> 5 büyük lig + Avrupa kupaları.
    takim_verisi: "yok" | "skor" (yalnızca Maçkolik; Ofsayt'a hiç istek gitmez) |
                  "akilli" (istatistik pazarı açılan maçlarda + büyük liglerde Ofsayt istatistikleri) |
                  "tam" (tüm maçlarda Ofsayt istatistikleri; uzun sürer).
    ilerleme: isteğe bağlı fonksiyon (aşama, tamamlanan, toplam)."""
    mod = _mod(takim_verisi)
    pazar_cfg = get("get_market_config").get("m", {})
    pazar_adlari = {k: v.get("n") for k, v in pazar_cfg.items() if isinstance(v, dict)}
    olaylar = get("events?st=1&type=0&version=0").get("events", [])
    adlar = yarismalar()
    simdi, sinir = zaman_araligi(gun)
    zamanda = [e for e in olaylar if simdi.timestamp() < e.get("d", 0) <= sinir.timestamp()]
    if ligler == "hepsi":
        secili = zamanda
    else:
        kume = set(ligler or VARSAYILAN_LIGLER)
        secili = [e for e in zamanda if e.get("ci") in kume]
    secili = kupalari_cikar(secili, adlar, yazdir)
    secili.sort(key=lambda e: e.get("d", 0))
    buyuk5 = sum(1 for e in secili if e.get("ci") in LIGLER)
    donem = "bugün" if gun <= 0 else f"önümüzdeki {gun} günde"
    yazdir(f"iddaa'da {len(olaylar)} futbol maçı açık; {donem} seçili liglerden {len(secili)} maç "
           f"({buyuk5} tanesi 5 büyük ligden).")
    cekilme = simdi.strftime("%Y-%m-%d %H:%M")
    if not secili:
        _tablolari_yaz([], [], cekilme, gun, 0, 0)
        yazdir("Taranacak maç bulunamadı.")
        return 0, [], []

    # 1) Maç ayrıntıları (tüm pazarlar) — paralel
    detaylar = _detaylari_cek(secili, yazdir)
    if ham_dosya:
        Path(ham_dosya).write_text(json.dumps({"config": pazar_adlari, "olaylar": list(detaylar.values())},
                                              ensure_ascii=False), encoding="utf-8")

    def lig_adi(ci):
        return LIGLER.get(ci) or KUPALAR.get(ci) or adlar.get(ci, (f"Yarışma {ci}", None))[0]

    bilgiler, taninmayan = {}, set()
    for e in secili:
        detay = detaylar.get(e["i"]) or e  # ayrıntı alınamadıysa bültendeki pazarlar
        bs, tn = baremleri_cikar(detay, pazar_adlari, e.get("hn"), e.get("an"))
        taninmayan |= tn
        bilgiler[e["i"]] = {"baremler": bs, "oranlar": bulten_oranlari(detay.get("m") or e.get("m")),
                            "stat_pazari": any(b["stat"] != "gol" for b in bs)}

    # 2) Maçkolik: günlük sonuç arşivi + iddaa koduyla kesin eşleşme. Goller ve ilk yarı skorları tüm takımlar için
    #    buradan gelir (gün başına 1 istek; geçmiş günler bir kez çekilir).
    import mackolik as mk
    mk_eslesme = {}
    if mod != "yok":
        try:
            mk.arsiv_guncelle(DB, "futbol", yazdir=yazdir)
            mk_eslesme = mk.iddaa_eslesme("futbol", secili, yazdir)
            yazdir(f"{len(mk_eslesme)}/{len(secili)} maç Maçkolik'te iddaa koduyla eşleşti "
                   f"(Maçkolik'e {mk.istek_sayisi()} istek).")
        except mk.MackolikEngel as hata:
            yazdir(f"⚠ {hata}")
        except Exception as hata:
            yazdir(f"Maçkolik'e bağlanılamadı: {hata}")
    mk_adlar = mk.takim_adlari(DB, "futbol")

    # 3) Ofsayt: yalnızca istatistik (korner, kart, şut...) gereken maçlar — iddaa'nın istatistik pazarı açtığı
    #    maçlar ve büyük ligler ("tam" modda tüm maçlar). Diğer maçlar Ofsayt'a hiç istek göndermez.
    def istatistik_gerekli(e):
        buyuk = e.get("ci") in LIGLER or e.get("ci") in KUPALAR
        return mod == "tam" or (mod == "akilli" and (bilgiler[e["i"]]["stat_pazari"] or buyuk))

    ist_maclar = [e for e in secili if istatistik_gerekli(e)]
    ofs, eslesme = None, {}
    if ist_maclar:
        import ofsayt as _o
        from sofascore import eslestir  # ad + saat eşleştirici (ağa çıkmaz)
        try:
            ofs = _o
            # Önce daha önce eşleşen takımlar (istek yok); günlük liste (gün başına ~7 MB) yalnızca eşleşmeyen
            # maçların günleri için çekilir
            _o.hafizadan_esle(ist_maclar, eslesme)
            eksik = [e for e in ist_maclar if e["i"] not in eslesme]
            if eksik:
                tk = ofs.gunler(sorted({datetime.fromtimestamp(e["d"], TZ).date() for e in eksik}), yazdir)
                yeni = eslestir(eksik, tk)
                eslesme.update(yeni)
                ofs.hafiza_yaz(yeni, eksik)
            yazdir(f"İstatistik gereken {len(ist_maclar)} maçın {len(eslesme)} tanesi Ofsayt'ta eşleşti "
                   f"({len(ist_maclar) - len(eksik)} tanesi önceki eşleşmelerden).")
        except _o.OfsaytEngel as hata:
            yazdir(f"⚠ {hata} Bu maçlarda gol baremleri Maçkolik verisiyle çalışır; korner/kart/şut "
                   "baremleri için önceki istatistikler kullanılır.")
            ofs = None
        except Exception as hata:
            yazdir(f"Ofsayt'a bağlanılamadı, istatistikler güncellenmeyecek: {hata}")
            ofs = None
        _o.hafizadan_esle(ist_maclar, eslesme)  # daha önce eşleşen takımlar (liste alınamadıysa da)

    with sqlite3.connect(DB) as con:
        takimlar = guncel_takimlar(con)
    elle = elle_eslesmeler()
    tum_takimlar = [t for liste in takimlar.values() for t in liste]

    def db_esles(iddaa_adi, ci):
        if ci in LIGLER:
            return esles(iddaa_adi, takimlar.get(LIGLER[ci], []), elle)
        return esles(iddaa_adi, tum_takimlar, elle, gevsek=False)

    def satirlari_kur(ad_haritasi):
        """ad_haritasi: {ofsayt takım id: veritabanı adı}"""
        bulten, satirlar = [], []
        for e in secili:
            b, s, k = bilgiler[e["i"]], eslesme.get(e["i"]), mk_eslesme.get(e["i"])
            ci = e.get("ci")
            if s is not None:  # istatistikli: Ofsayt adları (maçlar tablosundaki istatistikli satırlar)
                ev = ad_haritasi.get(s["ev_id"], s["ev"])
                dep = ad_haritasi.get(s["dep_id"], s["dep"])
                kaynak = "Ofsayt + Maçkolik"
            elif k is not None:  # Maçkolik arşivi
                ev, dep = mk_adlar.get(k["ev_id"], k["ev"]), mk_adlar.get(k["dep_id"], k["dep"])
                kaynak = "Maçkolik"
            else:
                ev, dep = db_esles(e["hn"], ci), db_esles(e["an"], ci)
                kaynak = None
            tarih = datetime.fromtimestamp(e["d"], TZ).strftime("%Y-%m-%d %H:%M")
            lig = lig_adi(ci)
            bulten.append({"event_id": e["i"], "lig": lig, "ulke": adlar.get(ci, (None, None))[1], "tarih": tarih,
                           "ev_iddaa": e["hn"], "dep_iddaa": e["an"], "ev": ev, "dep": dep,
                           "ev_sofa": s["ev_id"] if s is not None else None,
                           "dep_sofa": s["dep_id"] if s is not None else None,
                           "sofa_id": s["sofa_id"] if s is not None else None,
                           "custom_id": s.get("custom_id") if s is not None else None,
                           "mk_id": k["mk_id"] if k is not None else None, "kaynak": kaynak,
                           **b["oranlar"], "barem_sayisi": len(b["baremler"]),
                           "istatistik_pazari": int(b["stat_pazari"]), "cekilme": cekilme})
            for r in b["baremler"]:
                satirlar.append({"event_id": e["i"], "lig": lig, "tarih": tarih, "ev_iddaa": e["hn"],
                                 "dep_iddaa": e["an"], "ev": ev, "dep": dep, **r, "cekilme": cekilme})
        return bulten, satirlar

    # 4) İlk yazım: maçlar ve oranlar hemen görünsün (istatistikler sonra tamamlanır)
    bulten, satirlar = satirlari_kur({})
    _tablolari_yaz(bulten, satirlar, cekilme, gun, buyuk5, len(secili))
    yazdir(f"{len(bulten)} maç ve {len(satirlar)} barem kaydedildi.")

    # 5) Ofsayt istatistikleri (yalnızca istatistik gereken maçların takımları)
    ad_haritasi = {}
    if eslesme:
        import ofsayt as _o
        istekler = {s_[k + "_id"]: s_[k] for s_ in eslesme.values() for k in ("ev", "dep")}
        if ofs is None:  # liste alınamadı / engel: yalnızca veritabanındaki adlar
            with sqlite3.connect(DB) as con:
                _o._tablolar(con)
                ad_haritasi = {r[0]: r[1] for r in con.execute("SELECT takim_id, takim FROM ofsayt_takim")}
        else:
            yazdir(f"Ofsayt: {len(istekler)} takımın istatistikleri güncellenecek "
                   f"(diğer {max(0, len(secili) - len(eslesme)) * 2} takım yalnızca Maçkolik'ten).")
            try:
                ad_haritasi = ofs.takimlari_guncelle(
                    istekler, yazdir=yazdir, ilerleme=(lambda i, n: ilerleme("takım", i, n)) if ilerleme else None)
            except _o.OfsaytEngel as hata:
                yazdir(f"⚠ {hata}")
            except Exception as hata:
                yazdir(f"Takım istatistikleri tamamlanamadı: {hata}")
            with sqlite3.connect(DB) as con:
                ad_haritasi = {**{r[0]: r[1] for r in con.execute("SELECT takim_id, takim FROM ofsayt_takim")},
                               **ad_haritasi}

    bulten, satirlar = satirlari_kur(ad_haritasi)
    df = _tablolari_yaz(bulten, satirlar, cekilme, gun, buyuk5, len(secili))

    with sqlite3.connect(DB) as con:
        bilinen = {r[0] for r in con.execute("SELECT DISTINCT takim FROM maclar")} | set(mk_adlar.values())
    eslesmeyen = sorted({f"{b['lig']}: {b[k + '_iddaa']}" for b in bulten for k in ("ev", "dep")
                         if not (isinstance(b[k], str) and b[k] in bilinen)})
    yazdir(f"Bitti: {len(bulten)} maç, {len(df)} barem. "
           f"{len(bulten) * 2 - len(eslesmeyen)}/{len(bulten) * 2} takımın geçmiş maç verisi var.")
    if eslesmeyen and len(eslesmeyen) <= 40:
        yazdir("Verisi bulunamayan takımlar:\n  " + "\n  ".join(eslesmeyen))
    elif eslesmeyen:
        yazdir(f"{len(eslesmeyen)} takımın verisi bulunamadı (çoğu alt lig / amatör takım ya da arşivde henüz maçı yok).")
    return len(df), eslesmeyen, sorted(taninmayan)


ULUSLARARASI_KOD = {None, "", "INT", "WW", "EU"}

def sofa_kaynak_oku():
    import sofascore
    return sofascore.kaynak_oku()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--gun", type=int, default=0, help="0 = bugün (varsayılan), N = önümüzdeki N gün")
    p.add_argument("--lig", default="hepsi",
                   help='"hepsi" (varsayılan) = iddaa\'daki tüm futbol maçları; "buyuk" = 5 büyük lig + Avrupa '
                        "kupaları; ya da virgülle yarışma kimlikleri (ör. 43,45).")
    p.add_argument("--takim-verisi", default="akilli", choices=["yok", "skor", "akilli", "tam"],
                   help="Takım verisi: yok | skor (Maçkolik) | akilli (varsayılan; + Ofsayt istatistikleri) | tam")
    p.add_argument("--takim-verisi-yok", action="store_true", help="--takim-verisi yok ile aynı")
    p.add_argument("--ligleri-listele", action="store_true", help="Maçı olan yarışmaları ve kimliklerini yazdır")
    p.add_argument("--ham", default=None, help="Ham API yanıtlarını bu JSON dosyasına kaydet")
    a = p.parse_args()
    if a.ligleri_listele:
        print(lig_ozeti(a.gun).to_string(index=False))
        raise SystemExit
    ligler = ("hepsi" if a.lig == "hepsi" else None if a.lig == "buyuk"
              else [int(x) for x in a.lig.split(",")])
    _, _, tn = guncelle(a.gun, a.ham, ligler=ligler,
                        takim_verisi="yok" if a.takim_verisi_yok else a.takim_verisi)
    if tn:
        print("Bilgi: kullanılmayan pazarlar:\n  " + "\n  ".join(sorted(tn)))
