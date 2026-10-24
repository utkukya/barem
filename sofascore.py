"""
SofaScore'dan herhangi bir takımın son maçlarını tüm istatistikleriyle çeker ve futbol.db'ye ekler.

iddaa'da barem açılan her maçın iki takımı için kullanılır: takım SofaScore'da aranır (ülke, cinsiyet,
altyapı/rezerv kontrolüyle), son 10 maçı ve her maçın istatistikleri çekilir. Daha önce çekilmiş maçlar
tekrar indirilmez; takımın son çekim zamanı tutulur, kısa süre içinde tekrar sorulursa ağa çıkılmaz.

Elle düzeltme: yanlış ya da bulunamayan takımlar için sofascore_eslesme.csv dosyasına
    iddaa_adi;sofascore_takim_id
satırı ekleyin (takım id'si SofaScore'daki takım sayfasının adresinin sonundaki sayıdır,
ör. sofascore.com/team/football/club-brugge/2888 -> 2888).
"""
import difflib
import functools
import math
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from db_olustur import DB, KLASOR, ekle, hazirla_df

API = "https://www.sofascore.com/api/v1"
ESLESME = KLASOR / "sofascore_eslesme.csv"
SON_N = 10
# İstekler tek bir hız sınırlayıcıdan geçer (iş parçacıkları da dahil): iki istek arasında en az bu kadar saniye.
# SofaScore 403/429 dönerse aralık kendiliğinden iki katına çıkar.
ARALIK = 0.5
ARALIK_UST = 4.0
ISCI = 2               # takım verisi çekerken eşzamanlı iş parçacığı
TAZE_SAAT = 12         # takım bu kadar saat içinde çekildiyse tekrar ağa çıkma

# Büyük 5 lig: ilk veriyle aynı lig adlarını kullan (SofaScore "LaLiga" yazıyor, veritabanında "La Liga")
LIG_ADI = {17: "Premier League", 8: "La Liga", 23: "Serie A", 35: "Bundesliga", 34: "Ligue 1"}
HAZIRLIK = re.compile(r"friendl|hazırlık|club friendly", re.I)
ALTYAPI = re.compile(r"\b(u-?1[5-9]|u-?2[0-3]|ii|b|reserves?|youth|academy|jong|castilla|primavera|"
                     r"women|kadın|ladies|fem(enino|inino|minile)?)\b", re.I)

# iddaa'nın Türkçe yazdığı şehir/kulüp adları -> SofaScore aramasında bulunan biçim
ARAMA_EXONIM = {
    "kopenhag": "copenhagen", "prag": "praha", "atina": "athens", "varşova": "warszawa",
    "kızılyıldız": "crvena zvezda", "belgrad": "beograd", "lizbon": "lisbon", "münih": "münchen",
    "bükreş": "bucuresti", "viyana": "wien", "moskova": "moscow", "kiev": "kyiv", "brüksel": "brussels",
    "marsilya": "marseille", "porto": "porto", "zagrep": "zagreb", "sofya": "sofia",
    "kızıl yıldız": "crvena zvezda",
}
# iddaa yarışma ülke kodu -> SofaScore'da karşılık gelebilecek ülke kodları
ULKE_ESDEGER = {"GB": {"GB", "EN", "SX", "WA", "WL", "NI"}, "EN": {"GB", "EN"}, "ENG": {"EN", "GB"},
                "SCO": {"SX", "GB"}, "WAL": {"WA", "GB"}, "KSV": {"XK"}, "XK": {"XK"}}
ULUSLARARASI = {None, "", "INT", "WW", "EU"}

_yerel = threading.local()       # her iş parçacığının kendi oturumu (curl_cffi oturumu paylaşılmaz)
_kilit = threading.Lock()
_durum = {"son": 0.0, "aralik": ARALIK, "istek": 0}


# ---------------------------------------------------------------- ağ
def _oturum_al():
    oturum = getattr(_yerel, "oturum", None)
    if oturum is None:
        try:
            from curl_cffi import requests as creq
        except ImportError as e:
            raise RuntimeError("curl_cffi yüklü değil. Komut satırında: "
                               ".venv\\Scripts\\python -m pip install curl_cffi") from e
        oturum = creq.Session(impersonate="chrome")
        oturum.headers.update({
            "Referer": "https://www.sofascore.com/",
            "Origin": "https://www.sofascore.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        })
        try:
            oturum.get("https://www.sofascore.com/", timeout=30)  # çerezleri al
        except Exception:
            pass
        _yerel.oturum = oturum
    return oturum


def _sira_bekle():
    """Tüm iş parçacıkları için ortak hız sınırı: iki istek arası en az _durum['aralik'] saniye."""
    with _kilit:
        bekle = _durum["son"] + _durum["aralik"] - time.monotonic()
        if bekle > 0:
            time.sleep(bekle)
        _durum["son"] = time.monotonic()
        _durum["istek"] += 1


def istek_sayisi():
    return _durum["istek"]


class SofaEngel(RuntimeError):
    """SofaScore bot doğrulaması (403 challenge / captcha): IP geçici olarak engelli."""


ENGEL_MESAJI = ("SofaScore bu bilgisayarın internet bağlantısına geçici bir bot doğrulaması koydu (403 challenge). "
                "Çok sayıda istekten sonra olur ve genellikle birkaç saat içinde kalkar. Bu güncellemede takım verisi "
                "çekilmedi; daha önce eşleşen takımların veritabanındaki maçları kullanılıyor.")


def engelli_mi():
    return _durum.get("engel", False)


_engel_yazildi = set()


def hata_yaz(yazdir, mesaj, hata):
    """Hata mesajı; SofaScore engeli ise yalnızca bir kez yazılır."""
    if isinstance(hata, SofaEngel):
        if id(yazdir) not in _engel_yazildi:
            _engel_yazildi.add(id(yazdir))
            yazdir(f"⚠ {hata}")
        return
    yazdir(f"{mesaj}: {hata}")


def get(yol):
    if _durum.get("engel"):
        raise SofaEngel(ENGEL_MESAJI)  # engelliyken istek göndermeye devam etmek engeli uzatır
    oturum = _oturum_al()
    durum = None
    for deneme in range(4):
        _sira_bekle()
        try:
            r = oturum.get(f"{API}{yol}", timeout=30)
            durum = r.status_code
        except Exception as e:  # bağlantı hatası: kısa bekleyip tekrar dene
            durum = str(e)
            time.sleep(2 * (deneme + 1))
            continue
        if durum == 200:
            return r.json()
        if durum == 404:
            return None
        if durum == 403 and "challenge" in (r.text or "")[:300].lower():
            _durum["engel"] = True
            raise SofaEngel(ENGEL_MESAJI)
        if durum in (403, 429):  # yavaşla
            with _kilit:
                _durum["aralik"] = min(_durum["aralik"] * 2, ARALIK_UST)
        time.sleep(5 * (deneme + 1))
    raise RuntimeError(f"SofaScore {durum}: {yol}")


# ---------------------------------------------------------------- takım eşleştirme
def _norm(s):
    from iddaa_baremler import norm
    return norm(s)


def _ad_puani(sorgu, aday):
    """İsim benzerliği 0-1: kelime kapsama + karakter benzerliği."""
    import difflib
    from iddaa_baremler import anahtar
    a, b = anahtar(sorgu), anahtar(aday)
    ta, tb = set(a.split()), set(b.split())
    kapsama = len(ta & tb) / max(len(ta), 1)
    onek = sum(1 for q in ta if any(c.startswith(q) or q.startswith(c) for c in tb if min(len(q), len(c)) >= 3))
    onek = onek / max(len(ta), 1)
    return max(kapsama, 0.9 * onek, difflib.SequenceMatcher(None, a, b).ratio())


def takim_ara(iddaa_adi, ulke=None, kadin=False, yazdir=print):
    """iddaa'daki takım adını SofaScore'da arar. (takim_id, sofascore_adi, ulke) ya da None döner.
    ulke: iddaa yarışmasının ülke kodu (ör. "BE"); uluslararası kupalarda None."""
    turkce = iddaa_adi
    for k, v in ARAMA_EXONIM.items():
        turkce = re.sub(rf"(?i)\b{k}\b", v, turkce)
    sorgular = [iddaa_adi, turkce, _norm(iddaa_adi)]
    gorulen, adaylar = set(), []
    for q in dict.fromkeys(s for s in sorgular if s):
        veri = get(f"/search/all?q={_url(q)}&page=0") or {}
        for r in veri.get("results", []):
            e = r.get("entity") or {}
            if r.get("type") != "team" or e.get("id") in gorulen:
                continue
            if (e.get("sport") or {}).get("slug") != "football":
                continue
            gorulen.add(e.get("id"))
            adaylar.append(e)
        if adaylar:
            break
    en_iyi, en_puan = None, 0.0
    for e in adaylar:
        ad = e.get("name", "")
        cinsiyet = e.get("gender") or "M"
        if (cinsiyet == "F") != kadin:
            continue
        if not kadin and ALTYAPI.search(ad) and not ALTYAPI.search(iddaa_adi):
            continue  # "Club Brugge" ararken "Club Brugge II" / "U19" gelmesin
        if e.get("national"):
            continue
        p = _ad_puani(iddaa_adi, ad)
        e_ulke = (e.get("country") or {}).get("alpha2")
        if ulke and e_ulke:
            uygun = ULKE_ESDEGER.get(ulke.upper(), {ulke.upper()})
            p += 0.25 if e_ulke.upper() in uygun else -0.2
        p += 0.03 * math.log10(1 + (e.get("userCount") or 0))  # popüler olan, aynı adlılar arasında
        if p > en_puan:
            en_iyi, en_puan = e, p
    if en_iyi is None or en_puan < 0.7:
        yazdir(f"    SofaScore'da bulunamadı: {iddaa_adi}" + (f" ({ulke})" if ulke else ""))
        return None
    return en_iyi["id"], en_iyi["name"], (en_iyi.get("country") or {}).get("alpha2")


def _url(s):
    import urllib.parse
    return urllib.parse.quote(s)


def elle_eslesmeler():
    if not ESLESME.exists():
        return {}
    d = pd.read_csv(ESLESME, sep=";", header=None, names=["iddaa", "id"], comment="#", dtype=str)
    d = d.dropna()
    return {a.strip(): int(b) for a, b in zip(d["iddaa"], d["id"]) if b.strip().isdigit()}


# ---------------------------------------------------------------- kaynak tablosu (önbellek)
def _kaynak_tablosu(con):
    con.execute("""CREATE TABLE IF NOT EXISTS takim_kaynak (
        iddaa_adi TEXT PRIMARY KEY, sofascore_id INTEGER, takim TEXT, ulke TEXT, son_cekim TEXT)""")


def kaynak_oku():
    with sqlite3.connect(DB) as con:
        _kaynak_tablosu(con)
        return pd.read_sql("SELECT * FROM takim_kaynak", con).set_index("iddaa_adi")


def kaynak_yaz(iddaa_adi, takim_id, takim, ulke, son_cekim):
    with sqlite3.connect(DB) as con:
        _kaynak_tablosu(con)
        con.execute("INSERT OR REPLACE INTO takim_kaynak VALUES (?, ?, ?, ?, ?)",
                    (iddaa_adi, takim_id, takim, ulke, son_cekim))


# ---------------------------------------------------------------- maç ve istatistikler
def mac_istatistikleri(event_id):
    """Maçın tüm istatistikleri (tüm maç + 1. ve 2. yarı) düz sözlük olarak; scraper ile aynı biçim."""
    veri = get(f"/event/{event_id}/statistics")
    satir = {}
    if not veri:
        return satir
    for periyot in veri.get("statistics", []):
        on_ek = {"ALL": "", "1ST": "IY_", "2ND": "2Y_"}.get(periyot["period"], periyot["period"] + "_")
        for grup in periyot.get("groups", []):
            for item in grup.get("statisticsItems", []):
                k = item.get("key") or item.get("name")
                satir[f"{on_ek}ev_{k}"] = item.get("homeValue", item.get("home"))
                satir[f"{on_ek}dep_{k}"] = item.get("awayValue", item.get("away"))
    return satir


def son_maclar(takim_id, n=SON_N):
    """Takımın oynanmış son n resmi maçı (hazırlık maçları hariç), eskiden yeniye."""
    maclar, sayfa = [], 0
    while len(maclar) < n and sayfa < 3:
        veri = get(f"/team/{takim_id}/events/last/{sayfa}")
        if not veri:
            break
        for e in veri.get("events", []):
            ut = (e.get("tournament") or {}).get("uniqueTournament") or {}
            if (e.get("status") or {}).get("type") != "finished":
                continue
            if HAZIRLIK.search(ut.get("name", "") + " " + (e.get("tournament") or {}).get("name", "")):
                continue
            maclar.append(e)
        if not veri.get("hasNextPage"):
            break
        sayfa += 1
    maclar.sort(key=lambda e: e["startTimestamp"])
    return maclar[-n:]


def _genis_satir(e):
    ut = (e.get("tournament") or {}).get("uniqueTournament") or {}
    lig = LIG_ADI.get(ut.get("id")) or ut.get("name") or (e.get("tournament") or {}).get("name")
    hs, as_ = e.get("homeScore") or {}, e.get("awayScore") or {}
    return {
        "lig": lig, "mac_id": e["id"],
        "tarih": pd.to_datetime(e["startTimestamp"], unit="s"),
        "hafta": (e.get("roundInfo") or {}).get("round"),
        "ev": e["homeTeam"]["name"], "dep": e["awayTeam"]["name"],
        "ev_gol": hs.get("current"), "dep_gol": as_.get("current"),
        "ev_iy_gol": hs.get("period1"), "dep_iy_gol": as_.get("period1"),
    }


def _uzun(genis, takim_adi):
    """Geniş maç satırlarını takım açısından satırlara çevirir (scraper'daki ile aynı mantık)."""
    df = pd.DataFrame(genis)
    sabit = ["lig", "mac_id", "tarih", "hafta"]
    stat = [c for c in df.columns if c not in sabit]

    def cevir(taraf, karsi, saha):
        d = {c: df[c] for c in sabit}
        d["saha"] = saha
        for c in stat:
            if c in ("ev", "dep"):
                yeni = "takim" if c == taraf else "rakip"
            else:
                yeni = c.replace(f"{taraf}_", "takim_", 1) if f"{taraf}_" in c else c.replace(f"{karsi}_", "rakip_", 1)
            d[yeni] = df[c]
        return pd.DataFrame(d)

    uzun = pd.concat([cevir("ev", "dep", "Ev"), cevir("dep", "ev", "Deplasman")], ignore_index=True)
    return uzun[uzun["takim"] == takim_adi]  # yalnızca istenen takımın satırları


def taze_mi(iddaa_adi):
    """Takım yakın zamanda çekildiyse True (ağa çıkmaya gerek yok)."""
    kaynak = kaynak_oku()
    if iddaa_adi not in kaynak.index:
        return False
    son = pd.to_datetime(kaynak.loc[iddaa_adi, "son_cekim"], errors="coerce")
    return pd.notna(son) and datetime.now() - son < timedelta(hours=TAZE_SAAT)


def takim_verisi_tamamla(iddaa_adi, ulke=None, kadin=False, yazdir=print, zorla=False, arama_adi=None):
    """iddaa'daki takımın son 10 maçının veritabanında olmasını sağlar. Veritabanındaki takım adını döner.
    arama_adi: SofaScore'da aranacak ad (ör. büyük 5 lig takımının veritabanındaki adı); yoksa iddaa adı."""
    kaynak = kaynak_oku()
    simdi = datetime.now()
    elle = elle_eslesmeler()
    kayit = kaynak.loc[iddaa_adi] if iddaa_adi in kaynak.index else None

    if kayit is not None and not zorla and iddaa_adi not in elle:
        son = pd.to_datetime(kayit["son_cekim"], errors="coerce")
        if pd.notna(son) and simdi - son < timedelta(hours=TAZE_SAAT):
            return kayit["takim"] if isinstance(kayit["takim"], str) else None
    if iddaa_adi in elle:
        takim_id = elle[iddaa_adi]
        bilgi = (get(f"/team/{takim_id}") or {}).get("team") or {}
        bulunan = (takim_id, bilgi.get("name", iddaa_adi), (bilgi.get("country") or {}).get("alpha2"))
    elif kayit is not None and pd.notna(kayit["sofascore_id"]):
        bulunan = (int(kayit["sofascore_id"]), kayit["takim"], kayit["ulke"])
    else:
        bulunan = takim_ara(arama_adi or iddaa_adi, ulke, kadin, yazdir)
    if bulunan is None:
        # Bulunamadı bilgisini de sakla: 36 saat boyunca her güncellemede tekrar aranmasın
        kaynak_yaz(iddaa_adi, None, None, None, simdi.strftime("%Y-%m-%d %H:%M"))
        return None
    takim_id, takim_adi, t_ulke = bulunan

    maclar = son_maclar(takim_id)
    # Takım adını maç kaydından al (büyük 5 lig verisindeki adlarla birebir aynı olsun)
    for e in reversed(maclar):
        for taraf in ("homeTeam", "awayTeam"):
            if e[taraf].get("id") == takim_id:
                takim_adi = e[taraf]["name"]
                break
        else:
            continue
        break
    with sqlite3.connect(DB) as con:
        try:
            mevcut = set(pd.read_sql("SELECT mac_id FROM maclar WHERE takim = ?", con, params=(takim_adi,))["mac_id"])
        except Exception:
            mevcut = set()
    yeni = [e for e in maclar if e["id"] not in mevcut]
    yazdir(f"    {iddaa_adi} → {takim_adi}: {len(maclar)} maç, {len(yeni)} yeni maçın istatistiği çekiliyor")
    if yeni:
        genis = []
        for e in yeni:
            satir = _genis_satir(e)
            satir.update(mac_istatistikleri(e["id"]))
            genis.append(satir)
        ekle(hazirla_df(_uzun(genis, takim_adi)))
    kaynak_yaz(iddaa_adi, takim_id, takim_adi, t_ulke, simdi.strftime("%Y-%m-%d %H:%M"))
    return takim_adi


# ================================================================ toplu akış (iddaa bülteninin tamamı için)
# Takım takım arama yapmak yerine:
#   1) iddaa'daki maçların günleri için SofaScore'un o günkü tüm maç takvimi çekilir (ülke kategorisi başına 1 istek),
#   2) her iddaa maçı SofaScore'daki maçla başlama saati + iki takım adıyla eşleştirilir (takım id'leri buradan gelir),
#   3) her takımın son maçları tek istekle alınır (skorlar, ilk yarı skorları);
#      istatistikler (şut, korner, kart...) yalnızca gerektiğinde maç başına bir istekle eklenir.

def _ulke_kodlari(iddaa_ulkeleri):
    """iddaa ülke kodları -> SofaScore kategori alpha2 kodları kümesi."""
    kume = set()
    for u in iddaa_ulkeleri:
        if u in ULUSLARARASI:
            continue
        u = str(u).upper()
        kume |= ULKE_ESDEGER.get(u, {u})
    return kume


def takvim(baslangic, bitis, iddaa_ulkeleri=None, yazdir=print, spor="football"):
    """[baslangic, bitis] (unix zaman) arasındaki SofaScore futbol maçları (DataFrame).
    iddaa_ulkeleri verilirse yalnızca o ülkelerin kategorileri + uluslararası kategoriler çekilir."""
    gunler = []
    g = datetime.fromtimestamp(baslangic, timezone.utc).date()
    while g <= datetime.fromtimestamp(bitis, timezone.utc).date():
        gunler.append(g.isoformat())
        g += timedelta(days=1)
    ulkeler = _ulke_kodlari(iddaa_ulkeleri) if iddaa_ulkeleri is not None else None

    isler = []
    for gun in gunler:
        kat = (get(f"/sport/{spor}/{gun}/10800/categories") or {}).get("categories", [])
        for c in kat:
            k = c.get("category") or {}
            a2 = (k.get("alpha2") or "").upper()
            if ulkeler is not None and a2 and a2 not in ulkeler:
                continue
            isler.append((gun, k.get("id"), a2))
    yazdir(f"SofaScore maç takvimi alınıyor ({len(gunler)} gün, {len(isler)} ülke/kategori)…")

    def cek(is_):
        gun, kid, a2 = is_
        veri = get(f"/category/{kid}/scheduled-events/{gun}") or {}
        satirlar = []
        for e in veri.get("events", []):
            ts = e.get("startTimestamp") or 0
            if not (baslangic - 3600 <= ts <= bitis + 3600):
                continue
            ev, dep = e.get("homeTeam") or {}, e.get("awayTeam") or {}
            ut = (e.get("tournament") or {}).get("uniqueTournament") or {}
            satirlar.append({"sofa_id": e["id"], "ts": ts, "ev": ev.get("name"), "dep": dep.get("name"),
                             "ev_kisa": ev.get("shortName") or "", "dep_kisa": dep.get("shortName") or "",
                             "ev_id": ev.get("id"), "dep_id": dep.get("id"),
                             "turnuva": ut.get("name") or (e.get("tournament") or {}).get("name"),
                             "turnuva_id": ut.get("id"), "ulke": a2, "cinsiyet": ev.get("gender") or "M",
                             "custom_id": e.get("customId")})
        return satirlar

    tum = []
    with ThreadPoolExecutor(ISCI) as havuz:
        for f in as_completed([havuz.submit(cek, i) for i in isler]):
            try:
                tum.extend(f.result())
            except SofaEngel:
                raise
            except Exception as hata:
                yazdir(f"  takvim kategorisi alınamadı: {hata}")
    df = pd.DataFrame(tum)
    return df.drop_duplicates("sofa_id") if len(df) else df


@functools.lru_cache(maxsize=None)
def _isim_puani_tek(iddaa_adi, aday, alt_esik=0.4):
    from iddaa_baremler import anahtar
    a, b = anahtar(iddaa_adi), anahtar(aday)
    ta, tb = set(a.split()), set(b.split())
    kapsama = len(ta & tb) / max(len(ta), 1)
    onek = sum(1 for q in ta if any((c.startswith(q) or q.startswith(c)) and min(len(q), len(c)) >= 3
                                    for c in tb)) / max(len(ta), 1)
    en = max(kapsama, 0.9 * onek)
    if en < 1.0:
        sm = difflib.SequenceMatcher(None, a, b)
        esik = max(en, alt_esik)  # bunun altındaki benzerlik kararı değiştirmez
        if sm.real_quick_ratio() > esik and sm.quick_ratio() > esik:
            en = max(en, sm.ratio())
    return en


def _isim_puani(iddaa_adi, adlar, alt_esik=0.4):
    return max((_isim_puani_tek(iddaa_adi, b, alt_esik) for b in adlar if b), default=0.0)


def _eski_isim_puani(iddaa_adi, adlar):
    from iddaa_baremler import anahtar
    a = anahtar(iddaa_adi)
    ta = set(a.split())
    en = 0.0
    for b in adlar:
        if not b:
            continue
        b = anahtar(b)
        tb = set(b.split())
        kapsama = len(ta & tb) / max(len(ta), 1)
        onek = sum(1 for q in ta if any((c.startswith(q) or q.startswith(c)) and min(len(q), len(c)) >= 3
                                        for c in tb)) / max(len(ta), 1)
        en = max(en, kapsama, 0.9 * onek, difflib.SequenceMatcher(None, a, b).ratio())
    return en


def eslestir(olaylar, tk):
    """iddaa maçlarını SofaScore takvimindeki maçlarla eşleştirir.
    olaylar: [{"i", "d", "hn", "an"}...]  ->  {iddaa event id: takvim satırı (dict)}.
    Kural: başlama saati en fazla 30 dk farklı; iki takım adı da benziyor ya da biri birebir aynı ve saat tutuyor
    (aynı saatte aynı takımın iki maçı olamaz)."""
    if tk is None or tk.empty:
        return {}
    kayit = tk.to_dict("records")
    kova = {}
    for s in kayit:
        kova.setdefault(s["ts"] // 900, []).append(s)
    def puanla(e, s):
        p1 = _isim_puani(e["hn"], [s["ev"], s["ev_kisa"]])
        p2 = _isim_puani(e["an"], [s["dep"], s["dep_kisa"]])
        ayni_saat = s["ts"] == e["d"]
        if ayni_saat and max(p1, p2) >= 0.9 and min(p1, p2) < 0.4:  # zayıf tarafı tam hesapla
            if p1 < p2:
                p1 = _isim_puani(e["hn"], [s["ev"], s["ev_kisa"]], 0.0)
            else:
                p2 = _isim_puani(e["an"], [s["dep"], s["dep_kisa"]], 0.0)
        kabul = (min(p1, p2) >= 0.5
                 or (p1 + p2 >= 1.2 and min(p1, p2) >= 0.4)
                 or (ayni_saat and max(p1, p2) >= 0.9 and min(p1, p2) >= 0.15))
        return (min(p1, p2) + (p1 + p2) / 4 + (0.1 if ayni_saat else 0)) if kabul else None

    saatte = {}
    for s in kayit:
        saatte.setdefault(s["ts"], []).append(s)
    adaylar = []
    for e in olaylar:
        # Önce aynı başlama saatindeki maçlar (çoğu böyle); bulunamazsa ±30 dk
        bulunan = [(p, e["i"], s["sofa_id"], s) for s in saatte.get(e["d"], [])
                   if (p := puanla(e, s)) is not None]
        if not bulunan:
            d = e["d"]
            for k in range(d // 900 - 2, d // 900 + 3):
                for s in kova.get(k, []):
                    if s["ts"] != d and abs(s["ts"] - d) <= 1800 and (p := puanla(e, s)) is not None:
                        bulunan.append((p, e["i"], s["sofa_id"], s))
        adaylar.extend(bulunan)
    # En iyi eşleşmeden başlayarak; her iki taraf da bir kez kullanılır
    sonuc, kullanilan = {}, set()
    for puan, iid, sid, s in sorted(adaylar, key=lambda x: -x[0]):
        if iid in sonuc or sid in kullanilan:
            continue
        sonuc[iid] = s
        kullanilan.add(sid)
    return sonuc


# ---------------------------------------------------------------- takım verisi (id ile)
def _takim_tablosu(con):
    con.execute("""CREATE TABLE IF NOT EXISTS sofa_takim (
        sofascore_id INTEGER PRIMARY KEY, takim TEXT, son_cekim TEXT, seviye TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS sofa_istatistik_yok (mac_id INTEGER PRIMARY KEY)")


SEVIYE = {"skor": 1, "tam": 2}
ISTATISTIK_ISARETI = ["takim_totalShotsOnGoal", "takim_ballPossession", "takim_cornerKicks", "takim_passes"]


def _mevcut_durum():
    """Veritabanındaki (takim, mac_id) -> istatistiği var mı; ve istatistiği olmadığı bilinen maçlar."""
    with sqlite3.connect(DB) as con:
        _takim_tablosu(con)
        yok = {r[0] for r in con.execute("SELECT mac_id FROM sofa_istatistik_yok")}
        sofa = pd.read_sql("SELECT * FROM sofa_takim", con).set_index("sofascore_id")
        try:
            kol = [r[1] for r in con.execute("PRAGMA table_info(maclar)")]
            isaret = [c for c in ISTATISTIK_ISARETI if c in kol]
            secim = ", ".join(["takim", "mac_id"] + [f'"{c}"' for c in isaret])
            d = pd.read_sql(f"SELECT {secim} FROM maclar", con)
        except Exception:
            d = pd.DataFrame(columns=["takim", "mac_id"])
            isaret = []
    var = d[isaret].notna().any(axis=1) if isaret else pd.Series(False, index=d.index)
    mevcut = {}
    for t, m, v in zip(d["takim"], d["mac_id"], var):
        mevcut.setdefault(t, {})[int(m)] = bool(v)
    return mevcut, yok, sofa


def takimlari_guncelle(istekler, yazdir=print, ilerleme=None, zorla=False):
    """istekler: {sofascore takım id: "skor" | "tam"}.
    Her takımın son 10 resmi maçını veritabanına yazar ("tam": maçların istatistikleriyle).
    {takım id: veritabanındaki takım adı} döner."""
    mevcut, yok, sofa = _mevcut_durum()
    simdi = datetime.now()
    adlar, yapilacak = {}, []
    for tid, seviye in istekler.items():
        if tid is None:
            continue
        tid = int(tid)
        if not zorla and tid in sofa.index:
            r = sofa.loc[tid]
            son = pd.to_datetime(r["son_cekim"], errors="coerce")
            if (pd.notna(son) and simdi - son < timedelta(hours=TAZE_SAAT)
                    and SEVIYE.get(r["seviye"], 0) >= SEVIYE[seviye] and isinstance(r["takim"], str)):
                adlar[tid] = r["takim"]
                continue
        yapilacak.append((tid, seviye))
    yazdir(f"Takım verisi: {len(istekler)} takımdan {len(yapilacak)} tanesi SofaScore'dan güncellenecek "
           f"({len(istekler) - len(yapilacak)} takım zaten güncel).")
    if not yapilacak:
        return adlar

    ist_onbellek, ist_kilit = {}, threading.Lock()

    def istatistik(mac_id):
        with ist_kilit:
            if mac_id in ist_onbellek:
                return ist_onbellek[mac_id]
        s = mac_istatistikleri(mac_id)
        with ist_kilit:
            ist_onbellek[mac_id] = s
        return s

    def is_(tid, seviye):
        maclar = son_maclar(tid)
        ad = None
        for e in reversed(maclar):
            for taraf in ("homeTeam", "awayTeam"):
                if e[taraf].get("id") == tid:
                    ad = e[taraf]["name"]
                    break
            if ad:
                break
        if ad is None:
            return tid, None, [], [], 0
        onceki = mevcut.get(ad, {})
        genis, bos_ist, yeni_ist = [], [], 0
        for e in maclar:
            var_mi = e["id"] in onceki
            ist_var = onceki.get(e["id"], False)
            ist_gerek = seviye == "tam" and not ist_var and e["id"] not in yok
            if var_mi and not ist_gerek:
                continue
            satir = _genis_satir(e)
            if ist_gerek:
                s = istatistik(e["id"])
                if s:
                    satir.update(s)
                    yeni_ist += 1
                else:
                    bos_ist.append(e["id"])
                    if var_mi:
                        continue  # zaten skoru var, istatistiği de yok
            genis.append(satir)
        satirlar = _uzun(genis, ad) if genis else pd.DataFrame()
        return tid, ad, [satirlar] if len(satirlar) else [], bos_ist, yeni_ist

    biriken, bos_biriken, bitti = [], [], 0
    baslangic_istek, t0 = istek_sayisi(), time.time()

    def yaz_db():
        nonlocal biriken, bos_biriken
        if biriken:
            ekle(hazirla_df(pd.concat(biriken, ignore_index=True)))
        with sqlite3.connect(DB) as con:
            _takim_tablosu(con)
            con.executemany("INSERT OR IGNORE INTO sofa_istatistik_yok VALUES (?)", [(m,) for m in bos_biriken])
        biriken, bos_biriken = [], []

    with ThreadPoolExecutor(ISCI) as havuz:
        isler = {havuz.submit(is_, tid, sev): (tid, sev) for tid, sev in yapilacak}
        for f in as_completed(isler):
            tid, sev = isler[f]
            bitti += 1
            try:
                tid, ad, satirlar, bos_ist, yeni_ist = f.result()
            except Exception as hata:
                hata_yaz(yazdir, f"    SofaScore hatası (takım {tid})", hata)
                continue
            if ad:
                adlar[tid] = ad
                biriken.extend(satirlar)
                bos_biriken.extend(bos_ist)
                with sqlite3.connect(DB) as con:
                    _takim_tablosu(con)
                    con.execute("INSERT OR REPLACE INTO sofa_takim VALUES (?, ?, ?, ?)",
                                (tid, ad, simdi.strftime("%Y-%m-%d %H:%M"), sev))
            if ilerleme:
                ilerleme(bitti, len(yapilacak))
            if bitti % 10 == 0 or bitti == len(yapilacak):
                gecen = time.time() - t0
                kalan = gecen / bitti * (len(yapilacak) - bitti)
                yazdir(f"  [{bitti}/{len(yapilacak)}] takım güncellendi · {istek_sayisi() - baslangic_istek} istek · "
                       f"kalan ≈ {kalan / 60:.0f} dk")
            if len(biriken) >= 40:
                yaz_db()
    yaz_db()
    return adlar


def h2h_maclari(custom_id, once_ts=None):
    """İki takımın kendi aralarındaki oynanmış maçları (SofaScore maçın customId'si ile), eskiden yeniye.
    once_ts verilirse yalnızca bu zamandan önce başlamış maçlar."""
    if not custom_id:
        return []
    veri = get(f"/event/{custom_id}/h2h/events") or {}
    maclar = [e for e in veri.get("events", [])
              if (e.get("status") or {}).get("type") == "finished"
              and (once_ts is None or e.get("startTimestamp", 0) < once_ts)]
    return sorted(maclar, key=lambda e: e["startTimestamp"])


# ---------------------------------------------------------------- eşleşme hafızası
def hafiza_yaz(db, eslesme, olaylar):
    """Başarılı eşleşmelerde iddaa takım adı -> SofaScore takım id'si / adı kaydı (SofaScore'a ulaşılamayınca kullanılır)."""
    satirlar = []
    for e in olaylar:
        s = eslesme.get(e["i"])
        if s is None:
            continue
        satirlar += [(e["hn"], int(s["ev_id"]), s["ev"]), (e["an"], int(s["dep_id"]), s["dep"])]
    if not satirlar:
        return
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE IF NOT EXISTS iddaa_takim (iddaa_adi TEXT PRIMARY KEY, sofa_id INTEGER, ad TEXT)")
        con.executemany("INSERT OR REPLACE INTO iddaa_takim VALUES (?, ?, ?)", satirlar)


def hafizadan_esle(db, olaylar, eslesme):
    """SofaScore'da eşleşmeyen maçlara, iki takım da daha önce eşleşmişse hafızadaki id'leri verir."""
    try:
        with sqlite3.connect(db) as con:
            h = dict((a, (i, n)) for a, i, n in con.execute("SELECT iddaa_adi, sofa_id, ad FROM iddaa_takim"))
    except sqlite3.Error:
        return 0
    eklenen = 0
    for e in olaylar:
        if e["i"] in eslesme or e["hn"] not in h or e["an"] not in h:
            continue
        (ei, en), (di, dn) = h[e["hn"]], h[e["an"]]
        eslesme[e["i"]] = {"sofa_id": None, "ts": e["d"], "ev": en, "dep": dn, "ev_id": ei, "dep_id": di,
                           "custom_id": None, "hafiza": True}
        eklenen += 1
    return eklenen


def adla_esle(db, olaylar, eslesme):
    """Hâlâ eşleşmeyen maçlar için iddaa takım adını veritabanındaki (takimlar tablosu) takım adlarıyla katı kelime
    eşleşmesiyle bağlar (ör. "Washington" -> "Washington Wizards"). Kadın takımları "(K)" bu yolla eşlenmez."""
    from iddaa_baremler import esles
    try:
        with sqlite3.connect(db) as con:
            t = pd.read_sql("SELECT sofascore_id, takim FROM takimlar WHERE takim IS NOT NULL", con)
    except Exception:
        return 0
    if t.empty:
        return 0
    adlar = list(dict.fromkeys(t["takim"]))
    idler = dict(zip(t["takim"], t["sofascore_id"]))
    eklenen = 0
    for e in olaylar:
        if e["i"] in eslesme or "(K)" in e["hn"] or "(K)" in e["an"]:
            continue
        ev, dep = esles(e["hn"], adlar, {}, gevsek=False), esles(e["an"], adlar, {}, gevsek=False)
        if ev and dep and ev != dep:
            eslesme[e["i"]] = {"sofa_id": None, "ts": e["d"], "ev": ev, "dep": dep, "ev_id": int(idler[ev]),
                               "dep_id": int(idler[dep]), "custom_id": None, "hafiza": True}
            eklenen += 1
    return eklenen


def eslesmeyi_tamamla(db, olaylar, eslesme, yazdir=print, ad_ile=True):
    """SofaScore takvimiyle eşleşmeyen maçları önce eşleşme hafızasından, sonra veritabanındaki takım adlarından
    tamamlar (SofaScore engelliyken de son maç verileri görünsün)."""
    a = hafizadan_esle(db, olaylar, eslesme)
    b = adla_esle(db, olaylar, eslesme) if ad_ile else 0
    if a or b:
        yazdir(f"{a + b} maç daha önceki eşleşmelerden / veritabanındaki takım adlarından tamamlandı.")


# ---------------------------------------------------------------- H2H: her maç için bir kez
def h2h_alinanlar(db):
    """H2H'si daha önce çekilmiş iddaa maçları (bir maçın H2H'si o maç oynanana kadar değişmez)."""
    try:
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE IF NOT EXISTS h2h_alinan (event_id INTEGER PRIMARY KEY)")
            return {r[0] for r in con.execute("SELECT event_id FROM h2h_alinan")}
    except sqlite3.Error:
        return set()


def h2h_alindi(db, event_idler):
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE IF NOT EXISTS h2h_alinan (event_id INTEGER PRIMARY KEY)")
        con.executemany("INSERT OR IGNORE INTO h2h_alinan VALUES (?)", [(int(i),) for i in event_idler])
