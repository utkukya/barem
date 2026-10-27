"""EuroLeague: NBA sayfasının birebir aynısı (oyuncu kartı, barem tarayıcı, değer, pozisyon eşleşme ve sıralama,
oyuncular); veriler euroleague.db'den."""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "nba_sayfa.py"), init_globals={"LIG_ADI": "euroleague"},
               run_name="__main__")
