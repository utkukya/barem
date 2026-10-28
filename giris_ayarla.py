"""
Giriş kullanıcı adı ve şifresini ayarlar.

    python giris_ayarla.py

Şifre ekrana yazılmaz; yalnızca özeti üretilir. Çıktıdaki iki satırı Streamlit Cloud → Manage app → Settings →
Secrets bölümüne yapıştırın. İsterseniz aynı satırlar bu bilgisayardaki .streamlit/secrets.toml dosyasına da yazılır
(o zaman yerelde de giriş sorulur). Bu dosya depoya gönderilmez.
"""
import getpass
import json
import re
from pathlib import Path

from giris import sifre_ozeti

KLASOR = Path(__file__).resolve().parent


def main():
    ad = input("Kullanıcı adı: ").strip()
    while True:
        s1 = getpass.getpass("Şifre (ekranda görünmez): ")
        s2 = getpass.getpass("Şifre (tekrar): ")
        if s1 != s2:
            print("Şifreler aynı değil, tekrar deneyin.")
        elif len(s1) < 8:
            print("Şifre en az 8 karakter olmalı.")
        else:
            break
    satirlar = [f"giris_kullanici = {json.dumps(ad, ensure_ascii=False)}",
                f"giris_sifre_ozeti = {json.dumps(sifre_ozeti(s1))}"]
    print("\nStreamlit Cloud → Manage app → Settings → Secrets bölümüne şu iki satırı ekleyin:\n")
    print("\n".join(satirlar))
    cevap = input("\nBu bilgisayarda da giriş sorulsun mu? (e/h): ").strip().lower()
    if cevap.startswith("e"):
        yol = KLASOR / ".streamlit" / "secrets.toml"
        yol.parent.mkdir(exist_ok=True)
        eski = yol.read_text(encoding="utf-8") if yol.exists() else ""
        eski = re.sub(r"(?m)^\s*giris_(kullanici|sifre_ozeti)\s*=.*\n?", "", eski)
        yol.write_text(eski.rstrip("\n") + ("\n" if eski.strip() else "") + "\n".join(satirlar) + "\n",
                       encoding="utf-8")
        print(f"Yazıldı: {yol}")


if __name__ == "__main__":
    main()
