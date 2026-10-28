"""
Streamlit Cloud'da yapılan güncellemeleri kalıcı yapar: güncellenen veritabanı dosyaları GitHub deposuna commit
edilir. Uygulama yeniden başlasa ya da yeni sürüm gelse de veriler depodan geri gelir.

Gerekenler (Streamlit Cloud → Manage app → Settings → Secrets):
    github_token = "github_pat_..."     # yalnızca bu depoya "Contents: Read and write" izni olan token
    github_repo  = "utkukya/barem"      # isteğe bağlı (varsayılan bu)
    github_dal   = "main"               # isteğe bağlı

Yerel bilgisayarda hiçbir şey yapmaz (orada yayinla.bat kullanılır).
"""
import base64
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

KLASOR = Path(__file__).resolve().parent
API = "https://api.github.com"


def _ayar(ad, varsayilan=None):
    try:
        import streamlit as st
        return st.secrets.get(ad, varsayilan)
    except Exception:
        return varsayilan


def etkin():
    from ortam import bulutta_mi
    return bulutta_mi() and bool(_ayar("github_token"))


def _git_sha(icerik):
    """GitHub'daki dosyanın blob kimliğiyle karşılaştırmak için (aynıysa yeniden yüklenmez)."""
    return hashlib.sha1(b"blob %d\0" % len(icerik) + icerik).hexdigest()


def kaydet(dosyalar, mesaj, yazdir=print):
    """dosyalar: KLASOR'e göre yollar (ör. ["futbol.db"]). Değişenleri tek commit'te GitHub'a gönderir.
    True: kaydedildi ya da değişiklik yok; False: kaydedilemedi."""
    if not etkin():
        return False
    import requests
    token = _ayar("github_token")
    repo = _ayar("github_repo", "utkukya/barem")
    dal = _ayar("github_dal", "main")
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    oturum = requests.Session()
    oturum.headers.update(h)
    adres = f"{API}/repos/{repo}"

    def istek(yontem, yol, **k):
        r = oturum.request(yontem, f"{adres}{yol}", timeout=300, **k)
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub {r.status_code}: {r.text[:200]}")
        return r.json()

    icerikler = {}
    for d in dosyalar:
        yol = KLASOR / d
        if not yol.exists():
            continue
        try:  # silinen tablolardan kalan boşluğu at (dosya küçülür, yükleme hızlanır)
            with sqlite3.connect(yol) as con:
                con.execute("VACUUM")
        except sqlite3.Error:
            pass
        icerikler[str(Path(d).as_posix())] = yol.read_bytes()
    if not icerikler:
        return True

    try:
        for deneme in range(3):
            son = istek("GET", f"/git/ref/heads/{dal}")["object"]["sha"]
            agac_sha = istek("GET", f"/git/commits/{son}")["tree"]["sha"]
            mevcut = {e["path"]: e["sha"] for e in istek("GET", f"/git/trees/{agac_sha}?recursive=1")["tree"]}
            degisen = {p: b for p, b in icerikler.items() if mevcut.get(p) != _git_sha(b)}
            if not degisen:
                yazdir("Kalıcı kayıt: GitHub'daki veriler zaten güncel.")
                return True
            agac = []
            for p, b in degisen.items():
                yazdir(f"Kalıcı kayıt: {p} GitHub'a yükleniyor ({len(b) / 1e6:.1f} MB)…")
                blob = istek("POST", "/git/blobs", json={"content": base64.b64encode(b).decode(), "encoding": "base64"})
                agac.append({"path": p, "mode": "100644", "type": "blob", "sha": blob["sha"]})
            yeni_agac = istek("POST", "/git/trees", json={"base_tree": agac_sha, "tree": agac})["sha"]
            commit = istek("POST", "/git/commits", json={"message": mesaj, "tree": yeni_agac, "parents": [son]})
            try:
                istek("PATCH", f"/git/refs/heads/{dal}", json={"sha": commit["sha"], "force": False})
                yazdir(f"Kalıcı kayıt tamam: {', '.join(degisen)} GitHub'a kaydedildi.")
                return True
            except RuntimeError as hata:
                if "422" in str(hata) and deneme < 2:  # bu arada başka bir commit geldi: üstüne yeniden dene
                    continue
                raise
    except Exception as hata:
        yazdir(f"⚠ Kalıcı kayıt yapılamadı ({hata}). Veriler bu oturumda görünür ama uygulama yeniden "
               "başlarsa kaybolur.")
        return False
    return False


def guncellemeyi_kaydet(dosyalar, ad, yazdir=print):
    """Sayfalardan çağrılır: bulutta ve token tanımlıysa kaydeder; değilse sessizce geçer."""
    if not etkin():
        return False  # GitHub bağlantısı (github_token) tanımlı değil: veri yalnızca çalışan uygulamada güncellenir
    from zoneinfo import ZoneInfo
    zaman = datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M")
    return kaydet(dosyalar, f"Buluttan güncelleme: {ad} {zaman}", yazdir)
