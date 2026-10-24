"""
Maçkolik: günlük sonuç arşivi (futbol, basketbol).

Maçkolik'in günlük canlı sonuç verisi o günün tüm maçlarını iddaa kodu, maç sonu ve ilk yarı skoruyla birlikte
tek istekte verir. Bu yüzden takım takım istek atmak yerine her gün bir kez çekilip arşivlenir:
  - ilk kullanımda son ARSIV_GUN gün (gün başına 1 istek),
  - sonraki güncellemelerde yalnızca bugün ve dün (geçmiş günler değişmez).
Takımların son maçları bu arşivden çıkarılır. iddaa maçları, Maçkolik'teki iddaa koduyla birebir eşleşir.
"""
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

URL = "https://www.mackolik.com/perform/p0/ajax/components/competition/livescores/json"
SPOR = {"futbol": "Soccer", "basketbol": "Basketball"}
TZ = ZoneInfo("Europe/Istanbul")
ARSIV_GUN = 45          # ilk kullanımda geriye dönük kaç gün arşivlensin
ARALIK = 1.2            # iki istek arası en az saniye
TAM_SAYILAN_GUN = 2     # bugünden bu kadar gün önceki ve daha eski günler "tamamlandı" sayılır, tekrar çekilmez

_kilit = threading.Lock()
_durum = {"son": 0.0, "engel": False, "istek": 0}
_gun_onbellek = {}      # (spor, tarih) -> veri; tek güncelleme içinde aynı gün iki kez çekilmesin


class MackolikEngel(RuntimeError):
    """Maçkolik istekleri reddediyor (çok fazla istek / bot koruması)."""


ENGEL_MESAJI = ("Maçkolik istekleri geçici olarak reddediyor. Bu güncellemede Maçkolik verisi alınamadı; "
                "arşivdeki maçlar kullanılıyor. Bir süre sonra tekrar deneyin.")


def _oturum():
    if "oturum" not in _durum:
        try:
            from curl_cffi import requests as creq
            s = creq.Session(impersonate="chrome")
        except ImportError:
            import requests as creq  # yedek
            s = creq.Session()
        s.headers.update({"Accept": "application/json, text/plain, */*",
                          "Accept-Language": "tr-TR,tr;q=0.9",
                          "Referer": "https://www.mackolik.com/futbol/canli-sonuclar"})
        _durum["oturum"] = s
    return _durum["oturum"]


def istek_sayisi():
    return _durum["istek"]


def engelli_mi():
    return _durum["engel"]


def gun_al(spor, tarih):
    """Bir günün tüm maçları: {"matches": {...}, "competitions": {...}}. tarih: 'YYYY-AA-GG' (Türkiye saati)."""
    k = (spor, tarih)
    if k in _gun_onbellek:
        return _gun_onbellek[k]
    if _durum["engel"]:
        raise MackolikEngel(ENGEL_MESAJI)
    durum = None
    for deneme in range(3):
        with _kilit:
            bekle = _durum["son"] + ARALIK - time.monotonic()
            if bekle > 0:
                time.sleep(bekle)
            _durum["son"] = time.monotonic()
            _durum["istek"] += 1
        try:
            r = _oturum().get(URL, params={"sports[]": SPOR[spor], "matchDate": tarih}, timeout=40)
            durum = r.status_code
        except Exception as e:
            durum = str(e)
            time.sleep(3 * (deneme + 1))
            continue
        if durum == 200:
            try:
                veri = (r.json() or {}).get("data") or {}
            except ValueError:  # JSON yerine HTML (doğrulama sayfası)
                _durum["engel"] = True
                raise MackolikEngel(ENGEL_MESAJI)
            veri = {"matches": veri.get("matches") or {}, "competitions": veri.get("competitions") or {}}
            _gun_onbellek[k] = veri
            return veri
        if durum in (403, 429):
            _durum["engel"] = True
            raise MackolikEngel(ENGEL_MESAJI)
        time.sleep(3 * (deneme + 1))
    raise RuntimeError(f"Maçkolik {durum}: {spor} {tarih}")


def sayfa_al(url, params=None, json_mi=False):
    """Maçkolik'ten herhangi bir sayfa/uç nokta (aynı hız sınırı ve engel denetimiyle).
    Bulunamazsa ya da sunucu hatası sürerse None döner."""
    if _durum["engel"]:
        raise MackolikEngel(ENGEL_MESAJI)
    durum = None
    for deneme in range(2):
        with _kilit:
            bekle = _durum["son"] + ARALIK - time.monotonic()
            if bekle > 0:
                time.sleep(bekle)
            _durum["son"] = time.monotonic()
            _durum["istek"] += 1
        try:
            r = _oturum().get(url, params=params, timeout=40)
            durum = r.status_code
        except Exception as e:
            durum = str(e)
            time.sleep(3 * (deneme + 1))
            continue
        if durum == 200:
            if not json_mi:
                return r.text
            try:
                return r.json()
            except ValueError:
                _durum["engel"] = True
                raise MackolikEngel(ENGEL_MESAJI)
        if durum in (403, 429):
            _durum["engel"] = True
            raise MackolikEngel(ENGEL_MESAJI)
        if durum == 404:
            return None
        time.sleep(2 * (deneme + 1))
    return None  # 5xx: bu sayfa için veri yok (ör. hazırlık maçlarında kutu skor)


def gun_onbellegi():
    """Bu oturumda çekilmiş günlük listeler (ek istek göndermeden takım bilgisi okumak için)."""
    return dict(_gun_onbellek)


# ---------------------------------------------------------------- arşiv
def _tablolar(con):
    con.execute("CREATE TABLE IF NOT EXISTS mk_gun (spor TEXT, tarih TEXT, PRIMARY KEY (spor, tarih))")
    con.execute("""CREATE TABLE IF NOT EXISTS mk_mac (
        spor TEXT, mac_id TEXT, ts INTEGER, lig TEXT, ulke TEXT, ev_id TEXT, ev TEXT, dep_id TEXT, dep TEXT,
        ev_skor REAL, dep_skor REAL, ev_iy REAL, dep_iy REAL, iddaa_kodu INTEGER, PRIMARY KEY (spor, mac_id))""")


def _sayi(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _biten_maclar(spor, veri):
    lig = {k: (c.get("name"), (c.get("country") or {}).get("name")) for k, c in veri["competitions"].items()}
    satirlar = []
    for m in veri["matches"].values():
        sk = m.get("score") or {}
        ev_s, dep_s = _sayi(sk.get("home")), _sayi(sk.get("away"))
        if m.get("state") != "post" or ev_s is None or dep_s is None:
            continue
        iy = sk.get("ht") or {}
        ad, ulke = lig.get(m.get("competitionId"), (None, None))
        satirlar.append((spor, m["id"], int((m.get("mstUtc") or 0) / 1000), ad, ulke,
                         m["homeTeam"]["id"], m["homeTeam"]["name"], m["awayTeam"]["id"], m["awayTeam"]["name"],
                         ev_s, dep_s, _sayi(iy.get("home")), _sayi(iy.get("away")), m.get("iddaaCode")))
    return satirlar


def arsiv_guncelle(db, spor, gun_sayisi=ARSIV_GUN, yazdir=print):
    """Son gun_sayisi günün biten maçlarını arşive ekler. Daha önce tamamlanmış günler tekrar çekilmez.
    Çekilen gün sayısını döner."""
    bugun = datetime.now(TZ).date()
    with sqlite3.connect(db) as con:
        _tablolar(con)
        tamam = {r[0] for r in con.execute("SELECT tarih FROM mk_gun WHERE spor = ?", (spor,))}
    gunler = [(bugun - timedelta(days=i)).isoformat() for i in range(gun_sayisi, -1, -1)]
    cekilecek = [g for g in gunler if g not in tamam]
    if len(cekilecek) > 3:
        yazdir(f"Maçkolik arşivi ({spor}): {len(cekilecek)} gün çekilecek (ilk seferde uzun sürer, "
               f"gün başına 1 istek; sonra yalnızca bugün ve dün).")
    eklenen = 0
    for i, g in enumerate(cekilecek, 1):
        try:
            veri = gun_al(spor, g)
        except MackolikEngel:
            raise
        except Exception as hata:
            yazdir(f"  Maçkolik {g} alınamadı: {hata}")
            continue
        satirlar = _biten_maclar(spor, veri)
        with sqlite3.connect(db) as con:
            _tablolar(con)
            con.executemany("INSERT OR REPLACE INTO mk_mac VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", satirlar)
            if (bugun - datetime.fromisoformat(g).date()).days >= TAM_SAYILAN_GUN:
                con.execute("INSERT OR IGNORE INTO mk_gun VALUES (?, ?)", (spor, g))
        eklenen += len(satirlar)
        if len(cekilecek) > 3 and (i % 10 == 0 or i == len(cekilecek)):
            yazdir(f"  [{i}/{len(cekilecek)}] gün · {eklenen} maç arşivlendi")
    return len(cekilecek)


def iddaa_eslesme(spor, olaylar, yazdir=print):
    """iddaa maçlarını Maçkolik maçlarıyla iddaa kodu üzerinden eşleştirir.
    {iddaa event id: {"ev", "dep", "ev_id", "dep_id", "mk_id"}} döner."""
    tarihler = sorted({datetime.fromtimestamp(e["d"], TZ).date() for e in olaylar})
    kodlar = {}
    for t in tarihler:
        for g in (t, t - timedelta(days=1)):  # gece yarısına yakın maçlar bir önceki günün listesinde olabilir
            try:
                veri = gun_al(spor, g.isoformat())
            except MackolikEngel:
                raise
            except Exception as hata:
                yazdir(f"  Maçkolik {g} alınamadı: {hata}")
                continue
            for m in veri["matches"].values():
                if m.get("iddaaCode"):
                    kodlar[int(m["iddaaCode"])] = m
        eksik = [e for e in olaylar if e["i"] not in kodlar and datetime.fromtimestamp(e["d"], TZ).date() == t]
        if eksik:  # ertesi gün listesine bak
            try:
                for m in gun_al(spor, (t + timedelta(days=1)).isoformat())["matches"].values():
                    if m.get("iddaaCode"):
                        kodlar[int(m["iddaaCode"])] = m
            except MackolikEngel:
                raise
            except Exception:
                pass
    sonuc = {}
    for e in olaylar:
        m = kodlar.get(int(e["i"]))
        if m:
            sonuc[e["i"]] = {"ev": m["homeTeam"]["name"], "dep": m["awayTeam"]["name"],
                             "ev_id": m["homeTeam"]["id"], "dep_id": m["awayTeam"]["id"], "mk_id": m["id"]}
    return sonuc


def takim_adlari(db, spor):
    """Maçkolik takım id'si -> tekil görünen ad. Aynı adlı farklı takımlar (Rangers, Al Nasr...) ülkeyle ayrılır."""
    with sqlite3.connect(db) as con:
        _tablolar(con)
        d = pd.read_sql("SELECT ev_id AS id, ev AS ad, ulke, ts FROM mk_mac WHERE spor = ? "
                        "UNION ALL SELECT dep_id, dep, ulke, ts FROM mk_mac WHERE spor = ?", con, params=(spor, spor))
    if d.empty:
        return {}
    d = d.sort_values("ts").drop_duplicates("id", keep="last")
    sayi = d.groupby("ad")["id"].transform("nunique")
    ad = d["ad"].where(sayi == 1, d["ad"] + " (" + d["ulke"].fillna("?") + ")")
    # ülke de aynıysa kısa id ekle
    sayi2 = ad.groupby(ad).transform("size")
    ad = ad.where(sayi2 == 1, ad + " #" + d["id"].str[:4])
    return dict(zip(d["id"], ad))


def takim_maclari(db, spor):
    """Arşivdeki biten maçlar, takım açısından (her maç iki satır). tarih UTC."""
    with sqlite3.connect(db) as con:
        _tablolar(con)
        m = pd.read_sql("SELECT * FROM mk_mac WHERE spor = ?", con, params=(spor,))
    kolonlar = ["mac_id", "takim_id", "tarih", "lig", "saha", "takim", "rakip", "rakip_id",
                "takim_skor", "rakip_skor", "takim_iy", "rakip_iy"]
    if m.empty:
        return pd.DataFrame(columns=kolonlar)
    adlar = takim_adlari(db, spor)
    tarih = pd.to_datetime(m["ts"], unit="s")
    ev = pd.DataFrame({"mac_id": m["mac_id"], "takim_id": m["ev_id"], "tarih": tarih, "lig": m["lig"], "saha": "Ev",
                       "takim": m["ev_id"].map(adlar), "rakip": m["dep_id"].map(adlar), "rakip_id": m["dep_id"],
                       "takim_skor": m["ev_skor"], "rakip_skor": m["dep_skor"],
                       "takim_iy": m["ev_iy"], "rakip_iy": m["dep_iy"]})
    dep = pd.DataFrame({"mac_id": m["mac_id"], "takim_id": m["dep_id"], "tarih": tarih, "lig": m["lig"],
                        "saha": "Deplasman", "takim": m["dep_id"].map(adlar), "rakip": m["ev_id"].map(adlar),
                        "rakip_id": m["ev_id"], "takim_skor": m["dep_skor"], "rakip_skor": m["ev_skor"],
                        "takim_iy": m["dep_iy"], "rakip_iy": m["ev_iy"]})
    return pd.concat([ev, dep], ignore_index=True).sort_values("tarih")
