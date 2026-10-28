"""
Ofsayt.com (futbol): maç istatistikleri (korner, kart, şut, faul, taç... — tüm maç, 1. ve 2. yarı).

  - Günlük maç listesi: bir günün tüm maçları tek istekte (takım adları ve kimlikleri) -> iddaa maçlarıyla
    ad + başlama saati üzerinden eşleşir.
  - Takımın maçları: takım kartı (/takim/mini/...) bu sezonun tüm maçlarını skor ve ilk yarı skoruyla verir.
  - Maç istatistikleri: maç ayrıntısı; maç başına 1 istek. Bir kez çekilen maç veritabanına yazılır, bir daha
    istenmez (iki takımın ortak maçı da tek kez çekilir).
Satırlar futbol.db'deki maclar tablosuna SofaScore ile aynı adlandırmayla yazılır (takim_cornerKicks,
IY_rakip_yellowCards...), böylece barem hesapları kaynaktan bağımsız çalışır.
"""
import html
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from db_olustur import DB, ekle, hazirla_df

ANA = "https://ofsayt.com"
TZ = ZoneInfo("Europe/Istanbul")
ARALIK = 1.0            # iki istek arası en az saniye
SON_N = 10
TAZE_SAAT = 12          # takım bu kadar saat içinde çekildiyse tekrar ağa çıkma
HAZIRLIK = re.compile(r"haz[ıi]rl[ıi]k|friendl", re.I)

_kilit = threading.Lock()
_durum = {"son": 0.0, "engel": False, "istek": 0}
ENGEL_SURE = 20 * 60     # site istekleri reddettiyse bu kadar saniye istek gönderilmez, sonra yeniden denenir


def _engelde():
    t = _durum["engel"]
    if t and time.monotonic() - t < ENGEL_SURE:
        return True
    _durum["engel"] = False
    return False


class OfsaytEngel(RuntimeError):
    """Ofsayt istekleri reddediyor."""


ENGEL_MESAJI = ("Ofsayt.com istekleri geçici olarak reddediyor. Bu güncellemede korner/kart/şut istatistikleri "
                "çekilmedi; veritabanındaki maçlar kullanılıyor. Bir süre sonra tekrar deneyin.")


def _oturum():
    if "oturum" not in _durum:
        try:
            from curl_cffi import requests as creq
            s = creq.Session(impersonate="chrome")
        except ImportError:
            import requests as creq
            s = creq.Session()
        s.headers.update({"Accept-Language": "tr-TR,tr;q=0.9", "Referer": ANA + "/"})
        _durum["oturum"] = s
    return _durum["oturum"]


def istek_sayisi():
    return _durum["istek"]


def engelli_mi():
    return _engelde()


def _get(yol, params=None):
    if _engelde():
        raise OfsaytEngel(ENGEL_MESAJI)
    durum = None
    for deneme in range(3):
        with _kilit:
            bekle = _durum["son"] + ARALIK - time.monotonic()
            if bekle > 0:
                time.sleep(bekle)
            _durum["son"] = time.monotonic()
            _durum["istek"] += 1
        try:
            r = _oturum().get(ANA + yol, params=params, timeout=60)
            durum = r.status_code
        except Exception as e:
            durum = str(e)
            time.sleep(3 * (deneme + 1))
            continue
        if durum == 200:
            metin = r.text
            if "cf-chl" in metin[:5000] or "Just a moment" in metin[:2000]:
                _durum["engel"] = time.monotonic()
                raise OfsaytEngel(ENGEL_MESAJI)
            return metin
        if durum == 404:
            return None
        if durum in (401, 403, 429, 503):
            _durum["engel"] = time.monotonic()
            raise OfsaytEngel(ENGEL_MESAJI)
        time.sleep(3 * (deneme + 1))
    raise RuntimeError(f"Ofsayt {durum}: {yol}")


def _ts(tarih, bicim):
    try:
        return int(datetime.strptime(tarih.strip(), bicim).replace(tzinfo=TZ).timestamp())
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------- günlük liste
_LI = re.compile(r'<li data-match-id="([0-9a-f-]{36})"[^>]*?data-sport-type="1"[^>]*?'
                 r'data-match-date="([^"]+)"')
_TAKIM_A = re.compile(r'<a href="/futbol/takim/([^/"]+)/([0-9a-f-]{36})/detay"[^>]*data-team-id="[^"]*"[^>]*>'
                      r'\s*<span[^>]*>([^<]*)</span>')


def gunluk_ayristir(metin):
    """datesorted HTML -> eslestir() için DataFrame (sofa_id = ofsayt maç kimliği)."""
    satirlar = []
    bas = [(m.start(), m) for m in _LI.finditer(metin or "")]
    for k, (i, m) in enumerate(bas):
        son = bas[k + 1][0] if k + 1 < len(bas) else len(metin)
        parca = metin[i:son]
        takimlar = _TAKIM_A.findall(parca)
        ts = _ts(m.group(2), "%d.%m.%Y %H:%M")
        if len(takimlar) < 2 or ts is None:
            continue
        (es, ei, ea), (ds, di, da) = takimlar[0], takimlar[1]
        satirlar.append({"sofa_id": m.group(1), "ts": ts, "ev": html.unescape(ea).strip(),
                         "dep": html.unescape(da).strip(), "ev_kisa": "", "dep_kisa": "",
                         "ev_id": ei, "dep_id": di, "ev_slug": es, "dep_slug": ds})
    df = pd.DataFrame(satirlar)
    return df.drop_duplicates("sofa_id") if len(df) else df


def gunluk(tarih):
    """tarih: date/datetime (Türkiye saati). O günün tüm futbol maçları."""
    metin = _get("/api/live/datesorted", {"type": 1, "date": tarih.strftime("%d.%m.%Y")})
    return gunluk_ayristir(metin)


# ---------------------------------------------------------------- takımın maçları
_TR = re.compile(r'<tr class="match-row[^"]*" data-match-id="([0-9a-f-]{36})"([^>]*)>(.*?)</tr>', re.S)
_ATTR = re.compile(r'data-([a-z-]+)="([^"]*)"')
_SKOR = re.compile(r'data-role="score">\s*(\d+)\s*-\s*(\d+)\s*<')
_IY = re.compile(r'match-switchable-td[^"]*">\s*<span[^>]*>\s*(\d+)\s*-\s*(\d+)\s*</span>')
_AD = re.compile(r'<a href="/futbol/takim/[^/"]+/([0-9a-f-]{36})/detay"[^>]*>.*?title="([^"]*)"', re.S)


def takim_ayristir(metin):
    """Takım kartı HTML -> biten maçlar (eskiden yeniye)."""
    sonuc = []
    for mid, attr, govde in _TR.findall(metin or ""):
        a = dict(_ATTR.findall(attr))
        skor = _SKOR.search(govde)
        if not skor:
            continue  # oynanmamış
        iy = _IY.search(govde)
        adlar = {i: html.unescape(t).strip() for i, t in _AD.findall(govde)}
        ev_id, dep_id = a.get("home-id"), a.get("away-id")
        sonuc.append({"mac_id": mid, "ts": _ts(a.get("date", ""), "%Y-%m-%dT%H:%M"),
                      "lig": html.unescape(a.get("comp-name", "")),
                      "ev_id": ev_id, "dep_id": dep_id, "ev": adlar.get(ev_id), "dep": adlar.get(dep_id),
                      "ev_gol": int(skor.group(1)), "dep_gol": int(skor.group(2)),
                      "ev_iy": int(iy.group(1)) if iy else None, "dep_iy": int(iy.group(2)) if iy else None})
    sonuc = [s for s in sonuc if s["ts"] and not HAZIRLIK.search(s["lig"])]
    return sorted({s["mac_id"]: s for s in sonuc}.values(), key=lambda s: s["ts"])


def sonraki_mac(metin):
    """Takım kartındaki oynanmamış ilk maçın başlama zamanı (ts) ya da None."""
    simdi = time.time()
    zamanlar = []
    for _, attr, govde in _TR.findall(metin or ""):
        if _SKOR.search(govde):
            continue
        ts = _ts(dict(_ATTR.findall(attr)).get("date", ""), "%Y-%m-%dT%H:%M")
        if ts and ts > simdi - 3 * 3600:
            zamanlar.append(ts)
    return min(zamanlar) if zamanlar else None


def takim_maclari(takim_id):
    """(biten maçlar, sıradaki maçın zamanı)."""
    metin = _get(f"/takim/mini/{takim_id}", {"sportType": 1})
    return takim_ayristir(metin), sonraki_mac(metin)


# ---------------------------------------------------------------- maç istatistikleri
ETIKET = {  # ofsayt etiketi -> SofaScore ile aynı anahtar (istatistikler.ISTATISTIKLER)
    "Topla Oynama": "ballPossession", "Gol Beklentisi (xG)": "expectedGoals",
    "Toplam Şut": "totalShotsOnGoal", "İsabetli Şut": "shotsOnGoal", "İsabetsiz Şut": "shotsOffGoal",
    "Engellenen Şut": "blockedScoringAttempt", "Ceza Sahası İçinden Şut": "totalShotsInsideBox",
    "Ceza Sahası Dışından Şut": "totalShotsOutsideBox", "Direkten Dönen Şut": "hitWoodwork",
    "Yaratılan Büyük Fırsat": "bigChanceCreated", "RCS Topla Buluşma": "touchesInOppBox",
    "Faul": "fouls", "Ofsayt": "offsides", "Korner": "cornerKicks", "Sarı Kart": "yellowCards",
    "Kırmızı Kart": "redCards", "Taç Atışı": "throwIns", "Kale Vuruşu": "goalKicks",
    "Toplam Pas": "passes", "İsabetli Pas": "accuratePasses", "İsabetli Uzun Pas": "accurateLongBalls",
    "İsabetli Orta": "accurateCross", "Kilit Pas": "keyPasses",
    "Top Çalma": "totalTackle", "Pas Arası": "interceptionWon", "Uzaklaştırma": "totalClearance",
    "Kurtarış": "goalkeeperSaves", "İkili Mücadele Kazanma": "duelWon", "Başarılı Top Sürme": "dribbles",
    "Top Kaybı": "possessionLost",
}
_BLOK = re.compile(r'<div class="team-(all|first-half|second-half)[^"]*">')
_SATIR = re.compile(r'ofs-ts-val-home">([^<]*)</td>.*?ofs-ts-label">([^<]*)</span>.*?'
                    r'ofs-ts-val-away">([^<]*)</td>', re.S)
ON_EK = {"all": "", "first-half": "IY_", "second-half": "2Y_"}


def _sayi(v):
    v = html.unescape(v).strip().replace("%", "")
    m = re.match(r"-?\d+(?:[.,]\d+)?", v)
    if not m:
        return None
    s = m.group(0).replace(",", ".")
    return float(s) if "." in s else int(s)


def istatistik_ayristir(metin):
    """Maç ayrıntısı HTML -> {"cornerKicks" vb. ön ekli: ev_/dep_ ...}. İstatistik yoksa boş sözlük."""
    satir = {}
    bloklar = [(m.start(), m.group(1)) for m in _BLOK.finditer(metin or "")]
    for k, (i, ad) in enumerate(bloklar):
        son = bloklar[k + 1][0] if k + 1 < len(bloklar) else min(len(metin), i + 60000)
        kapanis = re.search(r"</table>\s*</div>", metin[i:son])  # blok, ardışık tabloların bittiği yerde biter
        if kapanis:
            son = i + kapanis.end()
        on = ON_EK[ad]
        gorulen = set()
        for ev, etiket, dep in _SATIR.findall(metin[i:son]):
            anahtar = ETIKET.get(" ".join(html.unescape(etiket).split()))
            if not anahtar or anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            satir[f"{on}ev_{anahtar}"] = _sayi(ev)
            satir[f"{on}dep_{anahtar}"] = _sayi(dep)
    return satir


def mac_istatistikleri(mac_id):
    return istatistik_ayristir(_get(f"/api/match/getMatchDetail/{mac_id}/1"))


# ---------------------------------------------------------------- veritabanı
def sayisal_id(mac_id):
    """maclar tablosundaki mac_id tamsayı; ofsayt kimliğinden kararlı bir sayı (SofaScore kimlikleriyle çakışmaz)."""
    return int(mac_id.replace("-", "")[:15], 16)


def _tablolar(con):
    con.execute("CREATE TABLE IF NOT EXISTS ofsayt_takim (takim_id TEXT PRIMARY KEY, takim TEXT, son_cekim TEXT)")
    if "sonraki_mac" not in [c[1] for c in con.execute("PRAGMA table_info(ofsayt_takim)")]:
        con.execute("ALTER TABLE ofsayt_takim ADD COLUMN sonraki_mac INTEGER")
    con.execute("CREATE TABLE IF NOT EXISTS ofsayt_istatistik_yok (mac_id TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS ofsayt_eslesme (iddaa_adi TEXT PRIMARY KEY, takim_id TEXT, takim TEXT)")


ISARET = ["takim_cornerKicks", "takim_totalShotsOnGoal", "takim_passes", "takim_ballPossession"]


def _mevcut():
    """(veritabanında istatistiği olan mac_id'ler, istatistiği olmadığı bilinen maçlar, takım tablosu)."""
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        yok = {r[0] for r in con.execute("SELECT mac_id FROM ofsayt_istatistik_yok")}
        takim = pd.read_sql("SELECT * FROM ofsayt_takim", con).set_index("takim_id")
        try:
            kol = [r[1] for r in con.execute("PRAGMA table_info(maclar)")]
            isaret = [c for c in ISARET if c in kol]
            d = pd.read_sql("SELECT mac_id" + "".join(f', "{c}"' for c in isaret) + " FROM maclar", con)
            dolu = d[isaret].notna().any(axis=1) if isaret else pd.Series(False, index=d.index)
            var = set(pd.to_numeric(d.loc[dolu, "mac_id"], errors="coerce").dropna().astype("int64"))
        except Exception:
            var = set()
    return var, yok, takim


def hafiza_yaz(eslesme, olaylar):
    """iddaa adı -> ofsayt takımı (günlük liste alınamazsa sonraki güncellemelerde kullanılır)."""
    ad = {e["i"]: (e["hn"], e["an"]) for e in olaylar}
    kayit = []
    for iid, s in eslesme.items():
        if iid in ad:
            kayit += [(ad[iid][0], s["ev_id"], s["ev"]), (ad[iid][1], s["dep_id"], s["dep"])]
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        con.executemany("INSERT OR REPLACE INTO ofsayt_eslesme VALUES (?, ?, ?)", kayit)


def hafizadan_esle(olaylar, eslesme):
    """Eşleşmeyen iddaa maçlarını daha önce görülen takım eşleşmelerinden tamamlar."""
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        h = {r[0]: (r[1], r[2]) for r in con.execute("SELECT * FROM ofsayt_eslesme")}
    for e in olaylar:
        if e["i"] in eslesme:
            continue
        ev, dep = h.get(e["hn"]), h.get(e["an"])
        if ev and dep:
            eslesme[e["i"]] = {"sofa_id": None, "ts": e["d"], "ev": ev[1], "dep": dep[1],
                               "ev_id": ev[0], "dep_id": dep[0]}
    return eslesme


def gunler(tarihler, yazdir=print):
    """Verilen günlerin ofsayt maç listeleri (gün başına 1 istek)."""
    parcalar = []
    for gun in tarihler:
        try:
            parcalar.append(gunluk(gun))
        except OfsaytEngel:
            raise
        except Exception as hata:
            yazdir(f"  Ofsayt {gun:%d.%m} listesi alınamadı: {hata}")
    parcalar = [p for p in parcalar if len(p)]
    return pd.concat(parcalar, ignore_index=True) if parcalar else pd.DataFrame()


def takvim(baslangic_ts, bitis_ts, yazdir=print):
    """Bülten aralığındaki günlerin ofsayt maç listeleri (gün başına 1 istek)."""
    gun = datetime.fromtimestamp(baslangic_ts, TZ).date()
    son = datetime.fromtimestamp(bitis_ts, TZ).date()
    parcalar = []
    while gun <= son:
        try:
            parcalar.append(gunluk(gun))
        except OfsaytEngel:
            raise
        except Exception as hata:
            yazdir(f"  Ofsayt {gun:%d.%m} listesi alınamadı: {hata}")
        gun += timedelta(days=1)
    parcalar = [p for p in parcalar if len(p)]
    return pd.concat(parcalar, ignore_index=True) if parcalar else pd.DataFrame()


def _uzun(mac, ist, takim_id, ad):
    """Bir maçın satırı, takım açısından (maclar tablosu biçimi)."""
    ev_mi = mac["ev_id"] == takim_id
    t, r = ("ev", "dep") if ev_mi else ("dep", "ev")
    satir = {"lig": mac["lig"], "mac_id": sayisal_id(mac["mac_id"]),
             "tarih": datetime.fromtimestamp(mac["ts"], TZ).replace(tzinfo=None), "hafta": None,
             "saha": "Ev" if ev_mi else "Deplasman", "takim": ad,
             "rakip": mac["dep"] if ev_mi else mac["ev"],
             "takim_gol": mac[f"{t}_gol"], "rakip_gol": mac[f"{r}_gol"],
             "takim_iy_gol": mac[f"{t}_iy"], "rakip_iy_gol": mac[f"{r}_iy"]}
    for k, v in (ist or {}).items():
        on, taraf, anahtar = re.match(r"((?:IY_|2Y_)?)(ev|dep)_(.+)", k).groups()
        satir[f"{on}{'takim' if taraf == t else 'rakip'}_{anahtar}"] = v
    return satir


def takim_adi_belirle(takim_id, ad, takim_tablosu):
    """Aynı adı taşıyan başka bir ofsayt takımı varsa ada kısa kimlik eklenir (veritabanında takım adla tutulur)."""
    baskasi = takim_tablosu[(takim_tablosu["takim"] == ad) & (takim_tablosu.index != takim_id)]
    return f"{ad} #{takim_id[:4]}" if len(baskasi) else ad


def takimlari_guncelle(takimlar, yazdir=print, ilerleme=None, zorla=False):
    """takimlar: {ofsayt takım id: takım adı}. Her takımın son SON_N maçını istatistikleriyle maclar tablosuna
    yazar. {takım id: veritabanındaki ad} döner."""
    var, yok, tablo = _mevcut()
    simdi = datetime.now()
    adlar, yapilacak = {}, []
    for tid, ad in takimlar.items():
        if not tid:
            continue
        if not zorla and tid in tablo.index:
            son = pd.to_datetime(tablo.loc[tid, "son_cekim"], errors="coerce")
            sonraki = tablo.loc[tid, "sonraki_mac"] if "sonraki_mac" in tablo.columns else None
            # Son çekimden beri takımın yeni maçı olamaz: sıradaki maçı henüz oynanıp bitmedi (başlama + 3 saat)
            mac_yok = pd.notna(sonraki) and time.time() < float(sonraki) + 3 * 3600
            if (pd.notna(son) and simdi - son < timedelta(hours=TAZE_SAAT)) or (pd.notna(son) and mac_yok):
                adlar[tid] = tablo.loc[tid, "takim"]
                continue
        yapilacak.append((tid, ad))
    yazdir(f"Ofsayt: {len(takimlar)} takımdan {len(yapilacak)} tanesi güncellenecek "
           f"({len(takimlar) - len(yapilacak)} takım zaten güncel).")
    onbellek, biriken, bos = {}, [], []
    t0, istek0 = time.time(), istek_sayisi()

    def yaz():
        nonlocal biriken, bos
        if biriken:
            ekle(hazirla_df(pd.DataFrame(biriken)))
        with sqlite3.connect(DB) as con:
            _tablolar(con)
            con.executemany("INSERT OR IGNORE INTO ofsayt_istatistik_yok VALUES (?)", [(m,) for m in bos])
        biriken, bos = [], []

    for n, (tid, ad) in enumerate(yapilacak, 1):
        try:
            maclar, sonraki = takim_maclari(tid)
            maclar = maclar[-SON_N:]
        except OfsaytEngel:
            yaz()
            raise
        except Exception as hata:
            yazdir(f"  Ofsayt hatası ({ad}): {hata}")
            continue
        if not maclar:
            continue
        gercek_ad = next((m["ev"] if m["ev_id"] == tid else m["dep"] for m in reversed(maclar)
                          if (m["ev"] if m["ev_id"] == tid else m["dep"])), ad)
        db_adi = takim_adi_belirle(tid, gercek_ad, tablo)
        for m in maclar:
            sid = sayisal_id(m["mac_id"])
            if sid in var or m["mac_id"] in yok:
                continue
            if m["mac_id"] not in onbellek:
                try:
                    onbellek[m["mac_id"]] = mac_istatistikleri(m["mac_id"])
                except OfsaytEngel:
                    yaz()
                    raise
                except Exception as hata:
                    yazdir(f"  Ofsayt maç hatası: {hata}")
                    onbellek[m["mac_id"]] = None
            ist = onbellek[m["mac_id"]]
            if ist == {}:
                bos.append(m["mac_id"])
            biriken.append(_uzun(m, ist, tid, db_adi))
        adlar[tid] = db_adi
        tablo.loc[tid, "takim"] = db_adi
        with sqlite3.connect(DB) as con:
            _tablolar(con)
            con.execute("INSERT OR REPLACE INTO ofsayt_takim (takim_id, takim, son_cekim, sonraki_mac) "
                        "VALUES (?, ?, ?, ?)", (tid, db_adi, simdi.strftime("%Y-%m-%d %H:%M"), sonraki))
        if ilerleme:
            ilerleme(n, len(yapilacak))
        if n % 10 == 0 or n == len(yapilacak):
            gecen = time.time() - t0
            yazdir(f"  [{n}/{len(yapilacak)}] takım · {istek_sayisi() - istek0} istek · "
                   f"kalan ≈ {gecen / n * (len(yapilacak) - n) / 60:.0f} dk")
        if len(biriken) >= 60:
            yaz()
    yaz()
    return adlar
