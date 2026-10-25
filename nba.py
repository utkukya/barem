"""
NBA: oyuncu istatistikleri, pozisyonlar ve pozisyona göre takım hücum/savunma verisi.

Kaynak Maçkolik:
  - Maç listesi: basketbol sayfasının kullandığı günlük sonuç arşivi (basketbol.db, mk_mac). Ek istek yok.
  - Kutu skor: maç başına 1 istek (dakika, sayı, ribaund, asist, 2/3 sayılık ve serbest atış isabet/deneme,
    faul, blok, top çalma, top kaybı; ilk 5 işaretli). Bir kez çekilen maç tekrar istenmez.
  - Kadro: takım başına 1 istek (oyuncuların pozisyonu: Gard (1), Skorer Gard (2), Kısa forvet (3),
    Uzun Forvet (4), Pivot (5)). 3 günde bir yenilenir.

Pozisyon ataması (rol bazlı): her maçta ilk 5'teki oyunculara PG/SG/SF/PF/C yerleri, kadro pozisyonlarına en yakın
olacak şekilde birer kez dağıtılır (iki kısa forvet ilk 5'teyse biri SF, diğeri SG ya da PF olur). Yedekler kendi
kadro pozisyonlarında sayılır. Kadroda olmayan oyuncunun pozisyonu istatistiklerinden tahmin edilir.
"""
import html
import itertools
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import mackolik as mk

KLASOR = Path(__file__).resolve().parent
DB = KLASOR / "nba.db"
SEZON_BASLANGIC = "2026-10-20"      # normal sezonun ilk günü (bu tarihten önceki NBA maçları hazırlık maçı)
KADRO_GUN = 3
POZ = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C"}
POZLAR = list(POZ.values())
KUTU_URL = "https://www.mackolik.com/ajax/basketball/match/box-score"
KADRO_URL = "https://www.mackolik.com/basketbol/takim/{slug}/kadro/{tid}"
NBA_LIG = "NBA"
NBA_ULKE = "ABD"

# İstatistikler: anahtar -> (etiket, kısa)
STATLAR = {"sayi": ("Sayı", "SAY"), "rib": ("Ribaund", "RİB"), "ast": ("Asist", "AST"),
           "uc_i": ("3 sayılık isabet", "3S"), "tc": ("Top çalma", "TÇ"), "blok": ("Blok", "BLK"),
           "tk": ("Top kaybı", "TK"), "pra": ("Sayı + Rib + Ast", "SRA"), "pr": ("Sayı + Rib", "SR"),
           "pa": ("Sayı + Ast", "SA"), "ra": ("Rib + Ast", "RA"), "uc_a": ("3 sayılık deneme", "3SD")}


# ---------------------------------------------------------------- veritabanı
def _tablolar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS nba_mac (mac_id TEXT PRIMARY KEY, ts INTEGER, ev_id TEXT, ev TEXT,
        dep_id TEXT, dep TEXT, ev_skor REAL, dep_skor REAL, iddaa_kodu INTEGER)""")
    con.execute("""CREATE TABLE IF NOT EXISTS nba_kutu (mac_id TEXT, takim_id TEXT, oyuncu_id TEXT, oyuncu TEXT,
        ilk5 INTEGER, sira INTEGER, sn INTEGER, sayi INTEGER, rib INTEGER, ast INTEGER, iki_i INTEGER, iki_a INTEGER,
        uc_i INTEGER, uc_a INTEGER, sa_i INTEGER, sa_a INTEGER, faul INTEGER, blok INTEGER, tc INTEGER, tk INTEGER,
        PRIMARY KEY (mac_id, oyuncu_id))""")
    con.execute("CREATE TABLE IF NOT EXISTS nba_kutu_yok (mac_id TEXT PRIMARY KEY)")
    con.execute("""CREATE TABLE IF NOT EXISTS nba_takim (takim_id TEXT PRIMARY KEY, ad TEXT, slug TEXT,
        kadro_cekim TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS nba_kadro (oyuncu_id TEXT PRIMARY KEY, oyuncu TEXT, takim_id TEXT,
        forma TEXT, poz INTEGER, poz_metin TEXT)""")


# ---------------------------------------------------------------- ayrıştırma
_KUTU_SATIR = re.compile(
    r'<tr class="widget-basketball-match-box-score__row">\s*<td class="([^"]*)"[^>]*>\s*'
    r'<a href="[^"]*/basketbolcu/[^/"]*/([a-z0-9]+)"[^>]*>([^<]*)</a>\s*</td>(.*?)</tr>', re.S)
_HUCRE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def _tam(v):
    v = (v or "").strip()
    return int(v) if v.isdigit() else 0


def _iki(v):
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _saniye(v):
    m = re.match(r"\s*(\d+):(\d+)", v or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def kutu_ayristir(tablo_html):
    """Kutu skor tablosu -> oyuncu satırları. Hiç süre almayanlar atlanır."""
    satirlar = []
    for sira, (sinif, oid, ad, govde) in enumerate(_KUTU_SATIR.findall(tablo_html or "")):
        h = [re.sub(r"<[^>]+>", "", x).strip() for x in _HUCRE.findall(govde)]
        if len(h) < 11:
            continue
        sn = _saniye(h[0])
        if sn == 0:
            continue
        (iki_i, iki_a), (uc_i, uc_a), (sa_i, sa_a) = _iki(h[4]), _iki(h[5]), _iki(h[6])
        satirlar.append({"oyuncu_id": oid, "oyuncu": html.unescape(ad).strip(), "ilk5": int("--starting" in sinif),
                         "sira": sira, "sn": sn, "sayi": _tam(h[1]), "rib": _tam(h[2]), "ast": _tam(h[3]),
                         "iki_i": iki_i, "iki_a": iki_a, "uc_i": uc_i, "uc_a": uc_a, "sa_i": sa_i, "sa_a": sa_a,
                         "faul": _tam(h[7]), "blok": _tam(h[8]), "tc": _tam(h[9]), "tk": _tam(h[10])})
    return satirlar


_KADRO_SATIR = re.compile(r'<tr class="p0c-team-squad__row">(.*?)</tr>', re.S)


def kadro_ayristir(sayfa):
    """Kadro sayfası -> [{oyuncu_id, oyuncu, forma, poz (1-5), poz_metin}]."""
    sonuc = []
    for govde in _KADRO_SATIR.findall(sayfa or ""):
        link = re.search(r'href="[^"]*/basketbolcu/[^/"]*/([a-z0-9]+)"\s*>\s*([^<]*)<', govde)
        poz = re.search(r'cell-player--position"\s*>\s*([^<]*?)\s*<', govde)
        forma = re.search(r'cell-player--shirt-number"\s*>\s*([^<]*?)\s*<', govde)
        if not link:
            continue
        metin = html.unescape(poz.group(1)).strip() if poz else ""
        no = re.search(r"\((\d)\)", metin)
        sonuc.append({"oyuncu_id": link.group(1), "oyuncu": html.unescape(link.group(2)).strip(),
                      "forma": forma.group(1).strip() if forma else None,
                      "poz": int(no.group(1)) if no else None, "poz_metin": metin})
    return sonuc


# ---------------------------------------------------------------- güncelleme
def _nba_maclari_arsivden(hazirlik=False):
    import basketbol
    with sqlite3.connect(basketbol.DB) as con:
        mk._tablolar(con)
        d = pd.read_sql("SELECT mac_id, ts, ev_id, ev, dep_id, dep, ev_skor, dep_skor, iddaa_kodu FROM mk_mac "
                        "WHERE spor = 'basketbol' AND lig = ? AND ulke = ?", con, params=(NBA_LIG, NBA_ULKE))
    if not hazirlik:
        sinir = datetime.fromisoformat(SEZON_BASLANGIC).replace(tzinfo=timezone.utc).timestamp()
        d = d[d["ts"] >= sinir]
    return d


def _sluglari_bul(eksik, yazdir):
    """Kadro adresi için takımların Maçkolik kısa adları (günlük listelerden; önce bu oturumdaki önbellek)."""
    bulunan = {}

    def tara(veri):
        for m in veri.get("matches", {}).values():
            for t in ("homeTeam", "awayTeam"):
                if m[t]["id"] in eksik and m[t].get("slug"):
                    bulunan[m[t]["id"]] = m[t]["slug"]

    for veri in mk.gun_onbellegi().values():
        tara(veri)
    bugun = datetime.now(mk.TZ).date()
    for i in range(0, 8):
        if set(eksik) <= set(bulunan):
            break
        try:
            tara(mk.gun_al("basketbol", (bugun - timedelta(days=i)).isoformat()))
        except mk.MackolikEngel:
            raise
        except Exception as hata:
            yazdir(f"  Maçkolik günlük liste alınamadı: {hata}")
    return bulunan


def guncelle(yazdir=print, ilerleme=None, hazirlik=False, kadro_zorla=False):
    """Maçkolik arşivini günceller; yeni NBA maçlarının kutu skorlarını ve takım kadrolarını çeker."""
    import basketbol
    try:
        mk.arsiv_guncelle(basketbol.DB, "basketbol", yazdir=yazdir)
    except mk.MackolikEngel as hata:
        yazdir(f"⚠ {hata}")
        return
    maclar = _nba_maclari_arsivden(hazirlik)
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        maclar.to_sql("_yeni", con, if_exists="replace", index=False)
        con.execute("INSERT OR REPLACE INTO nba_mac SELECT * FROM _yeni")
        con.execute("DROP TABLE _yeni")
        alinan = {r[0] for r in con.execute("SELECT DISTINCT mac_id FROM nba_kutu")}
        yok = {r[0] for r in con.execute("SELECT mac_id FROM nba_kutu_yok")}
        takim = pd.read_sql("SELECT * FROM nba_takim", con).set_index("takim_id")
    yazdir(f"Arşivde {len(maclar)} biten NBA maçı var; "
           f"{len(alinan & set(maclar['mac_id']))} tanesinin kutu skoru veritabanında.")

    # Takımlar ve kadrolar
    adlar = {**dict(zip(maclar["ev_id"], maclar["ev"])), **dict(zip(maclar["dep_id"], maclar["dep"]))}
    simdi = datetime.now()
    eski = [t for t in adlar if kadro_zorla or t not in takim.index or pd.isna(takim.loc[t, "kadro_cekim"])
            or simdi - pd.to_datetime(takim.loc[t, "kadro_cekim"]) > timedelta(days=KADRO_GUN)]
    slug = {t: takim.loc[t, "slug"] for t in adlar if t in takim.index and isinstance(takim.loc[t, "slug"], str)}
    try:
        if eski:
            eksik_slug = [t for t in eski if t not in slug]
            if eksik_slug:
                slug.update(_sluglari_bul(eksik_slug, yazdir))
            yazdir(f"Kadrolar: {len(eski)} takımın kadrosu (pozisyonlar) güncellenecek.")
        for i, t in enumerate(eski, 1):
            if t not in slug:
                yazdir(f"  {adlar[t]}: Maçkolik adresi bulunamadı, kadro atlandı.")
                continue
            sayfa = mk.sayfa_al(KADRO_URL.format(slug=slug[t], tid=t))
            kadro = kadro_ayristir(sayfa)
            with sqlite3.connect(DB) as con:
                _tablolar(con)
                con.execute("INSERT OR REPLACE INTO nba_takim VALUES (?, ?, ?, ?)",
                            (t, adlar[t], slug[t], simdi.strftime("%Y-%m-%d %H:%M") if kadro else None))
                con.executemany("INSERT OR REPLACE INTO nba_kadro VALUES (?, ?, ?, ?, ?, ?)",
                                [(k["oyuncu_id"], k["oyuncu"], t, k["forma"], k["poz"], k["poz_metin"])
                                 for k in kadro])
            if ilerleme:
                ilerleme("kadro", i, len(eski))

        # Kutu skorlar
        yeni = maclar[~maclar["mac_id"].isin(alinan | yok)].sort_values("ts")
        if len(yeni):
            yazdir(f"Kutu skor: {len(yeni)} yeni maç çekilecek (maç başına 1 istek).")
        elif len(maclar):
            son = datetime.fromtimestamp(int(maclar["ts"].max()), mk.TZ).strftime("%d.%m %H:%M")
            yazdir(f"Kutu skorlar güncel: yeni biten NBA maçı yok (arşivdeki son maç {son}). Maçlar bitip "
                   "Maçkolik'e düştükten sonra yeniden güncelleyin.")
        for i, m in enumerate(yeni.itertuples(), 1):
            veri = mk.sayfa_al(KUTU_URL, {"matchId": m.mac_id}, json_mi=True)
            gorunum = ((veri or {}).get("data") or {}).get("views") or {}
            satirlar = []
            for taraf, tid in (("home", m.ev_id), ("away", m.dep_id)):
                for s in kutu_ayristir((gorunum.get(taraf) or {}).get("html")):
                    satirlar.append({"mac_id": m.mac_id, "takim_id": tid, **s})
            with sqlite3.connect(DB) as con:
                _tablolar(con)
                if satirlar:
                    pd.DataFrame(satirlar).to_sql("_yeni", con, if_exists="replace", index=False)
                    con.execute("INSERT OR REPLACE INTO nba_kutu SELECT mac_id, takim_id, oyuncu_id, oyuncu, ilk5, "
                                "sira, sn, sayi, rib, ast, iki_i, iki_a, uc_i, uc_a, sa_i, sa_a, faul, blok, tc, tk "
                                "FROM _yeni")
                    con.execute("DROP TABLE _yeni")
                elif (datetime.now(timezone.utc).timestamp() - m.ts) > 86400:
                    con.execute("INSERT OR IGNORE INTO nba_kutu_yok VALUES (?)", (m.mac_id,))
            if ilerleme:
                ilerleme("kutu", i, len(yeni))
            if i % 10 == 0 or i == len(yeni):
                yazdir(f"  [{i}/{len(yeni)}] maç")
    except mk.MackolikEngel as hata:
        yazdir(f"⚠ {hata}")
    yazdir(f"Bitti. Maçkolik'e toplam {mk.istek_sayisi()} istek gönderildi.")


def bulten_guncelle(gun=1, yazdir=print):
    """iddaa'daki NBA maçları (maç sonucu, toplam ve takım toplamı baremleri)."""
    import basketbol
    import iddaa_baremler as ib
    cfg = ib.get("get_market_config").get("m", {})
    pazar_adlari = {k: v.get("n") for k, v in cfg.items() if isinstance(v, dict)}
    olaylar = basketbol.olaylar_al()
    simdi, sinir = ib.zaman_araligi(gun)
    secili = [e for e in olaylar if simdi.timestamp() < e.get("d", 0) <= sinir.timestamp()]
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        nba_takimlari = {r[0] for r in con.execute("SELECT ev_id FROM nba_mac UNION SELECT dep_id FROM nba_mac")}
    if not nba_takimlari:
        yazdir("Önce NBA verisini güncelleyin (takımlar henüz bilinmiyor).")
        return 0
    eslesme = mk.iddaa_eslesme("basketbol", secili, yazdir)
    nba = [e for e in secili if e["i"] in eslesme and eslesme[e["i"]]["ev_id"] in nba_takimlari]
    detay = ib._detaylari_cek(nba, yazdir) if nba else {}
    satirlar = []
    for e in sorted(nba, key=lambda e: e["d"]):
        s = eslesme[e["i"]]
        bs, oran = basketbol.baremleri_cikar(detay.get(e["i"]) or e, pazar_adlari)
        cizgi = {}
        for k in ("toplam", "ev", "dep"):
            aday = [b for b in bs if b["kapsam"] == k and b["periyot"] == "" and b["alt_oran"] and b["ust_oran"]]
            if aday:  # ana çizgi: alt ve üst oranı birbirine en yakın olan
                b = min(aday, key=lambda b: abs(b["alt_oran"] - b["ust_oran"]))
                cizgi[k] = b["cizgi"]
        satirlar.append({"event_id": e["i"], "tarih": datetime.fromtimestamp(e["d"], ib.TZ).strftime("%Y-%m-%d %H:%M"),
                         "ev_id": s["ev_id"], "dep_id": s["dep_id"], "ev": s["ev"], "dep": s["dep"],
                         "ms1": oran.get("ms1"), "ms2": oran.get("ms2"), "toplam": cizgi.get("toplam"),
                         "ev_toplam": cizgi.get("ev"), "dep_toplam": cizgi.get("dep")})
    with sqlite3.connect(DB) as con:
        pd.DataFrame(satirlar, columns=["event_id", "tarih", "ev_id", "dep_id", "ev", "dep", "ms1", "ms2", "toplam",
                                        "ev_toplam", "dep_toplam"]).to_sql("nba_bulten", con, if_exists="replace",
                                                                           index=False)
        con.execute("CREATE TABLE IF NOT EXISTS nba_meta (anahtar TEXT PRIMARY KEY, deger TEXT)")
        con.execute("INSERT OR REPLACE INTO nba_meta VALUES ('bulten', ?)", (simdi.strftime("%Y-%m-%d %H:%M"),))
    yazdir(f"iddaa'da {len(satirlar)} NBA maçı bulundu.")
    return len(satirlar)


# ---------------------------------------------------------------- analiz
def verileri_yukle():
    """(maclar, kutu, kadro, bulten). kutu: oyuncu-maç satırları; 'rol' (PG..C), rakip ve tarih eklenmiş."""
    if not DB.exists():
        return None
    with sqlite3.connect(DB) as con:
        _tablolar(con)
        maclar = pd.read_sql("SELECT * FROM nba_mac", con)
        kutu = pd.read_sql("SELECT * FROM nba_kutu", con)
        kadro = pd.read_sql("SELECT * FROM nba_kadro", con)
        takim = pd.read_sql("SELECT * FROM nba_takim", con)
        try:
            bulten = pd.read_sql("SELECT * FROM nba_bulten", con)
        except Exception:
            bulten = pd.DataFrame()
    if kutu.empty:
        return maclar, kutu, kadro, bulten
    m = maclar.set_index("mac_id")
    kutu["ts"] = kutu["mac_id"].map(m["ts"])
    kutu["tarih"] = pd.to_datetime(kutu["ts"], unit="s", utc=True).dt.tz_convert(mk.TZ).dt.tz_localize(None)
    ev = kutu["takim_id"] == kutu["mac_id"].map(m["ev_id"])
    kutu["saha"] = np.where(ev, "Ev", "Dep")
    kutu["rakip_id"] = np.where(ev, kutu["mac_id"].map(m["dep_id"]), kutu["mac_id"].map(m["ev_id"]))
    ad = {**dict(zip(maclar["ev_id"], maclar["ev"])), **dict(zip(maclar["dep_id"], maclar["dep"]))}
    ad.update(dict(zip(takim["takim_id"], takim["ad"])))
    kutu["takim"] = kutu["takim_id"].map(ad)
    kutu["rakip"] = kutu["rakip_id"].map(ad)
    kutu["dk"] = kutu["sn"] / 60
    kutu["pra"] = kutu["sayi"] + kutu["rib"] + kutu["ast"]
    kutu["pr"] = kutu["sayi"] + kutu["rib"]
    kutu["pa"] = kutu["sayi"] + kutu["ast"]
    kutu["ra"] = kutu["rib"] + kutu["ast"]
    kutu["sg_a"] = kutu["iki_a"] + kutu["uc_a"]
    kutu["sg_i"] = kutu["iki_i"] + kutu["uc_i"]
    kutu = pozisyon_ata(kutu, kadro)
    return maclar, kutu, kadro, bulten


def _tahmini_poz(g):
    """Kadroda olmayan oyuncu: maç başına 36 dakikadaki asist/ribaunda göre kaba pozisyon."""
    dk = max(g["sn"].sum() / 60, 1)
    a, r = g["ast"].sum() * 36 / dk, g["rib"].sum() * 36 / dk
    if a >= 6:
        return 1
    if r >= 10:
        return 5
    if r >= 7.5:
        return 4
    if a >= 3.5:
        return 2
    return 3


def pozisyon_ata(kutu, kadro):
    """Her satıra kadro pozisyonu ('poz', 1-5) ve maç içi rolü ('rol', PG..C) ekler."""
    pz = dict(zip(kadro["oyuncu_id"], kadro["poz"]))
    kutu = kutu.copy()
    kutu["poz"] = kutu["oyuncu_id"].map(pz)
    eksik = kutu["poz"].isna()
    if eksik.any():
        tahmin = kutu[eksik].groupby("oyuncu_id").apply(_tahmini_poz, include_groups=False)
        kutu.loc[eksik, "poz"] = kutu.loc[eksik, "oyuncu_id"].map(tahmin)
        kutu["poz_tahmini"] = eksik
    else:
        kutu["poz_tahmini"] = False
    kutu["poz"] = kutu["poz"].astype(int)
    rol = kutu["poz"].copy()
    yerler = list(range(1, 6))
    for (_, _), g in kutu[kutu["ilk5"] == 1].groupby(["mac_id", "takim_id"]):
        if len(g) != 5:
            continue
        p = g["poz"].tolist()
        en_iyi = min(itertools.permutations(yerler),
                     key=lambda yer: (sum(abs(a - b) for a, b in zip(p, yer)),
                                      sum(abs(a - b) ** 2 for a, b in zip(p, yer))))
        rol.loc[g.index] = list(en_iyi)
    kutu["rol"] = rol.map(POZ)
    kutu["poz_ad"] = kutu["poz"].map(POZ)
    return kutu


def takim_maclari(kutu):
    """Takım-maç satırları: takımın oynadığı her maç, tarih ve sonuç."""
    t = kutu.groupby(["mac_id", "takim_id"]).agg(ts=("ts", "first"), tarih=("tarih", "first"),
                                                 saha=("saha", "first"), rakip_id=("rakip_id", "first"),
                                                 takim=("takim", "first"), rakip=("rakip", "first"),
                                                 sayi=("sayi", "sum")).reset_index()
    karsi = t.set_index(["mac_id", "takim_id"])["sayi"]
    t["yenen"] = [karsi.get((m, r), np.nan) for m, r in zip(t["mac_id"], t["rakip_id"])]
    t["G"] = t["sayi"] > t["yenen"]
    return t.sort_values("ts")


def son_maclar(kutu, n=None):
    """Her takımın son n maçının mac_id kümesi (None: tüm sezon)."""
    t = takim_maclari(kutu)
    if n:
        t = t.groupby("takim_id").tail(n)
    return set(zip(t["mac_id"], t["takim_id"]))


def pozisyon_tablosu(kutu, stat="sayi", n=None):
    """Takım başına maç başı: hücum (o pozisyondaki oyuncularının ürettiği) ve savunma (rakibin o pozisyondan
    ürettiği). Sütunlar: ('hucum'|'savunma', PG..C), toplamlar, sıralar ve maç sayısı."""
    k = kutu.copy()
    secim = son_maclar(k, n)
    uretim = k.groupby(["mac_id", "takim_id", "rol"])[stat].sum().unstack("rol").reindex(columns=POZLAR).fillna(0)
    uretim = uretim.reset_index()
    rakip = dict(zip(zip(k["mac_id"], k["takim_id"]), k["rakip_id"]))
    uretim["rakip_id"] = [rakip.get((m, t)) for m, t in zip(uretim["mac_id"], uretim["takim_id"])]
    h = uretim[[(m, t) in secim for m, t in zip(uretim["mac_id"], uretim["takim_id"])]]
    hucum = h.groupby("takim_id")[POZLAR].mean()
    # savunma: rakibin bu takıma karşı ürettiği (takımın son n maçı)
    s = uretim.rename(columns={"takim_id": "uretici", "rakip_id": "takim_id"})
    s = s[[(m, t) in secim for m, t in zip(s["mac_id"], s["takim_id"])]]
    savunma = s.groupby("takim_id")[POZLAR].mean()
    mac_sayisi = h.groupby("takim_id").size()
    tablo = pd.concat({"hucum": hucum, "savunma": savunma}, axis=1)
    tablo[("hucum", "Toplam")] = hucum.sum(axis=1)
    tablo[("savunma", "Toplam")] = savunma.sum(axis=1)
    for p in POZLAR + ["Toplam"]:
        tablo[("hucum_sira", p)] = tablo[("hucum", p)].rank(ascending=False, method="min")
        tablo[("savunma_sira", p)] = tablo[("savunma", p)].rank(ascending=True, method="min")
    tablo[("mac", "")] = mac_sayisi
    ad = k.drop_duplicates("takim_id").set_index("takim_id")["takim"]
    tablo.index = tablo.index.map(lambda t: ad.get(t, t))
    tablo.index.name = "Takım"
    return tablo


def kayit(kutu, takim_id, saha=None):
    """Takımın galibiyet-mağlubiyet kaydı ('5-1')."""
    t = takim_maclari(kutu)
    t = t[t["takim_id"] == takim_id]
    if saha:
        t = t[t["saha"] == saha]
    g = int(t["G"].sum())
    return f"{g}-{len(t) - g}"


def oyuncu_ozeti(kutu, n=None, en_az_mac=1):
    """Oyuncu başına (son n maç) ortalamalar ve isabet yüzdeleri."""
    k = kutu.sort_values("ts")
    if n:
        k = k.groupby("oyuncu_id").tail(n)
    g = k.groupby("oyuncu_id")
    son_takim = k.sort_values("ts").groupby("oyuncu_id")[["takim", "takim_id", "poz_ad", "oyuncu"]].last()
    o = pd.DataFrame({
        "Maç": g.size(), "İlk 5": g["ilk5"].sum(), "Dk": g["dk"].mean(),
        "Sayı": g["sayi"].mean(), "Rib": g["rib"].mean(), "Ast": g["ast"].mean(),
        "TÇ": g["tc"].mean(), "Blok": g["blok"].mean(), "TK": g["tk"].mean(), "Faul": g["faul"].mean(),
        "3S": g["uc_i"].mean(), "3SD": g["uc_a"].mean(),
        "SRA": g["pra"].mean(),
        "SG%": (g["iki_i"].sum() + g["uc_i"].sum()) / (g["iki_a"].sum() + g["uc_a"].sum()).replace(0, np.nan),
        "2S%": g["iki_i"].sum() / g["iki_a"].sum().replace(0, np.nan),
        "3S%": g["uc_i"].sum() / g["uc_a"].sum().replace(0, np.nan),
        "SA%": g["sa_i"].sum() / g["sa_a"].sum().replace(0, np.nan),
    })
    o = son_takim.join(o)
    return o[o["Maç"] >= en_az_mac].sort_values("Sayı", ascending=False)


def barem_isabeti(seri, cizgi):
    """Seride çizginin üstüne çıkılan maç oranı."""
    seri = pd.Series(seri).dropna()
    return float((seri > cizgi).mean()) if len(seri) else np.nan


# ================================================================ oyuncu baremleri (global bahis piyasası)
# Kaynak: Kambi'nin herkese açık oran servisi (Unibet ve birçok Avrupa bahis sitesinin altyapısı). Ücretsiz, anahtar
# gerektirmez. Maç listesi 1 istek + maç başına 1 istek. Oranlar ondalık; çizgiler (ör. 24.5) Alt/Üst.
KAMBI = "https://eu-offering-api.kambicdn.com/offering/v2018/ub"
KAMBI_PARAM = {"lang": "en_GB", "market": "GB"}
PROP_STAT = [  # (anahtar kelime, istatistik) — özelden genele
    (("points, rebounds", "assists"), "pra"), (("points", "rebounds"), "pr"), (("points", "assists"), "pa"),
    (("rebounds", "assists"), "ra"), (("three",), "uc_i"), (("3-point",), "uc_i"), (("3 point",), "uc_i"),
    (("points",), "sayi"), (("rebounds",), "rib"), (("assists",), "ast"), (("steals",), "tc"),
    (("blocks",), "blok"), (("turnovers",), "tk"),
]
PROP_DISLA = ("quarter", "half", "1st", "2nd", "3rd", "4th", "first", "double", "triple", "most", "highest",
              "race", "team", "minute")


def _kambi(yol, params=None):
    if "kambi" not in _kambi_durum:
        try:
            from curl_cffi import requests as creq
            s = creq.Session(impersonate="chrome")
        except ImportError:
            import requests as creq
            s = creq.Session()
        _kambi_durum["kambi"] = s
    import time
    bekle = _kambi_durum.get("son", 0) + 0.6 - time.monotonic()
    if bekle > 0:
        time.sleep(bekle)
    _kambi_durum["son"] = time.monotonic()
    r = _kambi_durum["kambi"].get(f"{KAMBI}/{yol}", params={**KAMBI_PARAM, **(params or {})}, timeout=30)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"Kambi {r.status_code}: {yol}")
    return r.json()


_kambi_durum = {}


def _prop_stat(etiket):
    a = (etiket or "").lower()
    if "player" not in a or any(x in a for x in PROP_DISLA) or re.match(r"\s*\d+\+", a) \
            or "steals & blocks" in a:
        return None  # "10+ sayı" gibi Evet/Hayır pazarları ve birleşik top çalma+blok alınmaz
    for kelimeler, stat in PROP_STAT:
        if all(k in a for k in kelimeler):
            return stat
    return None


def _oyuncu_adi(ad):
    """'Maxey, Tyrese' -> 'Tyrese Maxey'."""
    ad = (ad or "").strip()
    if "," in ad:
        soyad, ad_ = ad.split(",", 1)
        ad = f"{ad_.strip()} {soyad.strip()}"
    return ad


def prop_ayristir(veri):
    """Kambi betoffer/event yanıtı -> [{oyuncu, stat, cizgi, ust_oran, alt_oran, pazar}]."""
    sonuc = {}
    for b in (veri or {}).get("betOffers", []) or []:
        kriter = b.get("criterion") or {}
        stat = _prop_stat(kriter.get("englishLabel") or kriter.get("label"))
        if not stat:
            continue
        for o in b.get("outcomes", []) or []:
            if o.get("line") is None or not o.get("odds") or o.get("status", "OPEN") != "OPEN":
                continue
            tur = (o.get("type") or "").upper()
            etiket = (o.get("englishLabel") or o.get("label") or "").lower()
            yon = "ust" if tur == "OT_OVER" or etiket.startswith("over") else \
                  "alt" if tur == "OT_UNDER" or etiket.startswith("under") else None
            oyuncu = _oyuncu_adi(o.get("participant") or b.get("participant") or "")
            if not yon or not oyuncu:
                continue
            k = (oyuncu, stat, o["line"] / 1000)
            sonuc.setdefault(k, {"oyuncu": oyuncu, "stat": stat, "cizgi": o["line"] / 1000,
                                 "pazar": kriter.get("englishLabel") or kriter.get("label")})
            sonuc[k][f"{yon}_oran"] = o["odds"] / 1000
    return [r for r in sonuc.values() if r.get("ust_oran") and r.get("alt_oran")]


def baremleri_guncelle(yazdir=print):
    """Oynanacak NBA maçlarının oyuncu baremlerini (Alt/Üst ve oranlar) Kambi'den çeker; nba_prop tablosuna yazar."""
    liste = _kambi("listView/basketball/nba.json") or {}
    olaylar = [e.get("event") or {} for e in liste.get("events", [])]
    olaylar = [e for e in olaylar if e.get("state") == "NOT_STARTED" and e.get("id")]
    yazdir(f"Global piyasa (Kambi): {len(olaylar)} oynanacak NBA maçı.")
    satirlar, cekilme = [], datetime.now().strftime("%Y-%m-%d %H:%M")
    for e in olaylar:
        try:
            veri = _kambi(f"betoffer/event/{e['id']}.json", {"includeParticipants": "true"})
        except Exception as hata:
            yazdir(f"  {e.get('name')}: alınamadı ({hata})")
            continue
        props = prop_ayristir(veri)
        ev, dep = e.get("homeName"), e.get("awayName")
        if not (ev and dep) and " - " in (e.get("name") or ""):
            ev, dep = e["name"].split(" - ", 1)
        for p in props:
            satirlar.append({"kambi_id": e["id"], "baslangic": e.get("start"), "ev": ev, "dep": dep, **p,
                             "cekilme": cekilme})
        yazdir(f"  {ev} – {dep}: {len(props)} oyuncu baremi")
    with sqlite3.connect(DB) as con:
        pd.DataFrame(satirlar, columns=["kambi_id", "baslangic", "ev", "dep", "oyuncu", "stat", "cizgi", "pazar",
                                        "ust_oran", "alt_oran", "cekilme"]).to_sql("nba_prop", con,
                                                                                  if_exists="replace", index=False)
    yazdir(f"{len(satirlar)} oyuncu baremi kaydedildi.")
    return len(satirlar)


def _ad_norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", " ", s.replace("'", "").replace(".", ""))
    return " ".join(t for t in s.split() if t not in {"jr", "sr", "ii", "iii", "iv"})


def _takim_norm(s):
    s = _ad_norm(s)
    return set(re.sub(r"\bla\b", "los angeles", s).split())


def props_yukle():
    try:
        with sqlite3.connect(DB) as con:
            return pd.read_sql("SELECT * FROM nba_prop", con)
    except Exception:
        return pd.DataFrame()


def props_esle(props, kutu):
    """Kambi oyuncu/takım adlarını veritabanındaki oyuncu ve takımlarla eşleştirir
    (oyuncu_id, takim_id, rakip_id sütunları eklenir; eşleşmeyen satırlar düşer)."""
    import difflib
    if props.empty or kutu.empty:
        return props.iloc[0:0]
    son = kutu.sort_values("ts").groupby("oyuncu_id")[["oyuncu", "takim_id", "takim"]].last()
    adlar = {}
    for oid, r in son.iterrows():
        adlar.setdefault(_ad_norm(r["oyuncu"]), []).append(oid)
    anahtarlar = list(adlar)
    takimlar = kutu.drop_duplicates("takim_id").set_index("takim_id")["takim"]

    def takim_bul(ad):
        t = _takim_norm(ad)
        en, puan = None, 0
        for tid, mk_ad in takimlar.items():
            k = _takim_norm(mk_ad)
            p = len(k & t) / max(len(k), 1)
            if p > puan:
                en, puan = tid, p
        return en if puan >= 0.5 else None

    t_ev = {a: takim_bul(a) for a in props["ev"].dropna().unique()}
    t_dep = {a: takim_bul(a) for a in props["dep"].dropna().unique()}

    def oyuncu_bul(ad, aday_takimlar):
        n = _ad_norm(ad)
        adaylar = adlar.get(n)
        if not adaylar:
            yakin = difflib.get_close_matches(n, anahtarlar, n=3, cutoff=0.82)
            adaylar = [o for y in yakin for o in adlar[y]]
        adaylar = [o for o in adaylar if son.loc[o, "takim_id"] in aday_takimlar] or adaylar
        return adaylar[0] if adaylar else None

    satirlar = []
    for r in props.itertuples():
        ev, dep = t_ev.get(r.ev), t_dep.get(r.dep)
        oid = oyuncu_bul(r.oyuncu, {ev, dep})
        if oid is None:
            continue
        tid = son.loc[oid, "takim_id"]
        if (ev or dep) and tid not in {ev, dep}:
            continue  # takası olmuş ya da yanlış eşleşmiş oyuncu: bu maçın takımlarında değil
        rakip = dep if tid == ev else ev if tid == dep else None
        satirlar.append({**r._asdict(), "oyuncu_id": oid, "takim_id": tid, "takim": son.loc[oid, "takim"],
                         "db_oyuncu": son.loc[oid, "oyuncu"], "rakip_id": rakip,
                         "rakip": takimlar.get(rakip) if rakip else None})
    return pd.DataFrame(satirlar).drop(columns=["Index"], errors="ignore")


def _normal_ust(ort, sd, cizgi):
    from math import erf, sqrt
    sd = max(sd, 1e-6)
    return 1 - 0.5 * (1 + erf((cizgi - ort) / (sd * sqrt(2))))


ONSEL_MAC = 5


def deger_tablosu(props, kutu, n=10, savunma_agirlik=0.5):
    """Her oyuncu baremi için: piyasanın (oranlardan, kâr payı ayıklanmış) Üst olasılığı, oyuncunun son n maçta
    çizgiyi geçme oranı, model olasılığı (son n maç ortalaması ve dağılımı, rakibin oyuncunun rolüne karşı
    savunmasıyla düzeltilmiş) ve Üst/Alt için beklenen getiri."""
    p = props_esle(props, kutu)
    if p.empty:
        return p
    k = kutu.sort_values("ts")
    rol_sik = k.groupby("oyuncu_id")["rol"].agg(lambda s: s.value_counts().index[0])
    savunma = {}
    satirlar = []
    oyuncu_gruplari = dict(tuple(k.groupby("oyuncu_id", sort=False)))
    bos = k.iloc[0:0]
    for r in p.itertuples():
        og = oyuncu_gruplari.get(r.oyuncu_id, bos)
        x = og[r.stat].tail(n)
        dk = og["dk"].tail(n)
        if len(x) == 0:
            continue
        ort, sd = float(x.mean()), float(x.std(ddof=1)) if len(x) > 1 else float(np.sqrt(max(x.mean(), 1)))
        sd = max(sd, np.sqrt(max(ort, 0.5)) * 0.8)
        rol = rol_sik.get(r.oyuncu_id)
        carpan = 1.0
        if r.rakip_id and rol:
            if r.stat not in savunma:
                savunma[r.stat] = pozisyon_tablosu(kutu, r.stat, None)
            pt_ = savunma[r.stat]
            rakip_ad = r.rakip
            if rakip_ad in pt_.index:
                lig = pt_[("savunma", rol)].mean()
                if lig > 0:
                    carpan = 1 + savunma_agirlik * (pt_.loc[rakip_ad, ("savunma", rol)] / lig - 1)
        # Az maçlık örnekte piyasanın çizgisine doğru çekilir (sezon başında 2-3 maçla aşırı sonuç çıkmasın):
        # n maçlık ortalama, ÖNSEL_MAC maç değerinde piyasa çizgisiyle harmanlanır.
        beklenen = (len(x) * ort * carpan + ONSEL_MAC * r.cizgi) / (len(x) + ONSEL_MAC)
        sd = sd * np.sqrt(1 + ONSEL_MAC / (len(x) + ONSEL_MAC))
        p_model = _normal_ust(beklenen, sd, r.cizgi)
        ters_u, ters_a = 1 / r.ust_oran, 1 / r.alt_oran
        p_piyasa = ters_u / (ters_u + ters_a)
        isabet = float((x > r.cizgi).mean())
        ev_u, ev_a = p_model * r.ust_oran - 1, (1 - p_model) * r.alt_oran - 1
        satirlar.append({"Maç": f"{r.ev} – {r.dep}", "baslangic": r.baslangic, "Oyuncu": r.db_oyuncu,
                         "Takım": r.takim, "Rakip": r.rakip, "Rol": rol, "stat": r.stat,
                         "İstatistik": STATLAR[r.stat][0], "Barem": r.cizgi, "Üst oran": r.ust_oran,
                         "Alt oran": r.alt_oran, "Piyasa Üst": p_piyasa,
                         "Ort.": ort, "Dk": float(dk.mean()), "Maç sayısı": len(x),
                         "Üst isabeti": isabet, "Rakip etkisi": carpan - 1, "Beklenen": beklenen,
                         "Model Üst": p_model, "Fark": p_model - p_piyasa, "EV Üst": ev_u, "EV Alt": ev_a,
                         "oyuncu_id": r.oyuncu_id})
    d = pd.DataFrame(satirlar)
    if d.empty:
        return d
    d["Öneri"] = np.where(d["EV Üst"] > d["EV Alt"], "Üst", "Alt")
    d["En iyi EV"] = d[["EV Üst", "EV Alt"]].max(axis=1)
    return d.sort_values("En iyi EV", ascending=False)



# ================================================================ oyuncu kartı
def savunma_ozeti(kutu, stat):
    """Takım başına rakiplere maç başı yedirdiği stat: rol bazında ve toplam, evde/deplasmanda; her biri için
    'kolaylık' sırası (#1 = en çok yediren, yani Üst için en elverişli rakip)."""
    k = kutu
    uretim = k.groupby(["mac_id", "takim_id", "rakip_id", "saha", "rol"])[stat].sum().reset_index()
    # savunan takım = rakip_id; savunanın sahası, üretenin sahasının tersi
    uretim["savunan_saha"] = np.where(uretim["saha"] == "Ev", "Dep", "Ev")
    rol = uretim.groupby(["rakip_id", "mac_id", "rol"])[stat].sum().unstack("rol").reindex(columns=POZLAR)
    rol = rol.fillna(0).groupby("rakip_id").mean()
    mac = uretim.groupby(["rakip_id", "mac_id", "savunan_saha"])[stat].sum().reset_index()
    toplam = mac.groupby("rakip_id")[stat].mean()
    ev = mac[mac["savunan_saha"] == "Ev"].groupby("rakip_id")[stat].mean()
    dep = mac[mac["savunan_saha"] == "Dep"].groupby("rakip_id")[stat].mean()
    d = rol.copy()
    d["Toplam"], d["Ev"], d["Dep"] = toplam, ev, dep
    for c in list(d.columns):
        d[f"{c}_sira"] = d[c].rank(ascending=False, method="min")
    d["mac"] = mac.groupby("rakip_id").size()
    return d


def sonraki_rakip(takim_id_, bulten, props_eslesmis=None):
    """Takımın sıradaki maçı: (rakip_id, 'Ev'/'Dep', tarih metni) ya da None."""
    if bulten is not None and len(bulten):
        b = bulten[(bulten["ev_id"] == takim_id_) | (bulten["dep_id"] == takim_id_)].sort_values("tarih")
        if len(b):
            r = b.iloc[0]
            return (r["dep_id"], "Ev", r["tarih"]) if r["ev_id"] == takim_id_ else (r["ev_id"], "Dep", r["tarih"])
    if props_eslesmis is not None and len(props_eslesmis):
        p = props_eslesmis[(props_eslesmis["takim_id"] == takim_id_) & props_eslesmis["rakip_id"].notna()]
        if len(p):
            return p.iloc[0]["rakip_id"], None, str(p.iloc[0].get("baslangic") or "")
    return None


def not_harfi(sira, toplam=30):
    """Rakip kolaylık sırasından harf notu (#1 = Üst için en elverişli)."""
    if pd.isna(sira):
        return "–"
    harfler = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "F"]
    return harfler[min(int((sira - 1) / max(toplam, 1) * len(harfler)), len(harfler) - 1)]


# ================================================================ barem tarayıcı (Alt/Üst isabetleri)
def barem_tarayici(props, kutu, n=10, savunma_agirlik=0.5):
    """Her oyuncu baremi için Üst ve Alt ayrı satır: oyuncunun son n maçta o tarafı tutturma oranı (Form), sezon
    oranı, rakibin aynı roldeki ilk 5 oyuncularına son n maçta o tarafı yaşatma oranı (Rakip izin), model olasılığı,
    oran ve beklenen getiri."""
    d = deger_tablosu(props, kutu, n, savunma_agirlik)
    if d.empty:
        return d
    k = kutu.sort_values("ts")
    ilk5 = k[k["ilk5"] == 1]
    takim_id_ = k.drop_duplicates("takim").set_index("takim")["takim_id"]
    satirlar = []
    oyuncu_gruplari = dict(tuple(k.groupby("oyuncu_id", sort=False)))
    bos = k.iloc[0:0]
    for _, r in d.iterrows():
        x = oyuncu_gruplari.get(r["oyuncu_id"], bos)[r["stat"]]
        son = x.tail(n)
        rakip_id = takim_id_.get(r["Rakip"])
        izin = ilk5.loc[(ilk5["rakip_id"] == rakip_id) & (ilk5["rol"] == r["Rol"]), r["stat"]].tail(n) \
            if rakip_id is not None else pd.Series(dtype=float)
        for yon in ("Üst", "Alt"):
            ust = yon == "Üst"

            def oran(s):
                return float(((s > r["Barem"]) if ust else (s < r["Barem"])).mean()) if len(s) else np.nan

            satirlar.append({
                "Oyuncu": r["Oyuncu"], "Takım": r["Takım"], "Rakip": r["Rakip"], "Maç": r["Maç"],
                "baslangic": r["baslangic"], "Rol": r["Rol"], "stat": r["stat"], "İstatistik": r["İstatistik"],
                "Yön": yon, "Çizgi": r["Barem"], "Oran": r["Üst oran"] if ust else r["Alt oran"],
                "Form": oran(son), "Maç sayısı": len(son), "Sezon": oran(x), "Sezon maç": len(x),
                "Rakip izin": oran(izin), "Rakip maç": len(izin), "Ort.": float(son.mean()),
                "Rakip izin ort.": float(izin.mean()) if len(izin) else np.nan,
                "Model": r["Model Üst"] if ust else 1 - r["Model Üst"],
                "Değer": r["EV Üst"] if ust else r["EV Alt"], "oyuncu_id": r["oyuncu_id"]})
    return pd.DataFrame(satirlar)
