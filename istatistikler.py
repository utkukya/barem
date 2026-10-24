"""İstatistik anahtarları, Türkçe etiketleri ve türleri (arayüz ve veritabanı ortak kullanır)."""

# anahtar: (Türkçe etiket, tür)
#   sayac  -> maç başına sayılan olay (şut, korner, taç...)  -> sayım dağılımıyla tahmin
#   oran   -> yüzde (topa sahip olma, ikili mücadele...)     -> ortalama ile tahmin
#   surekli-> ondalıklı ölçüm (xG, km, reyting...)           -> ortalama ile tahmin
ISTATISTIKLER = {
    "gol": ("Gol", "sayac"),
    "expectedGoals": ("Beklenen Gol (xG)", "surekli"),
    "expectedGoalsOnTarget": ("İsabetli Şut xG (xGOT)", "surekli"),
    "totalShotsOnGoal": ("Toplam Şut", "sayac"),
    "shotsOnGoal": ("İsabetli Şut", "sayac"),
    "shotsOffGoal": ("İsabetsiz Şut", "sayac"),
    "blockedScoringAttempt": ("Engellenen Şut", "sayac"),
    "totalShotsInsideBox": ("Ceza Sahası İçi Şut", "sayac"),
    "totalShotsOutsideBox": ("Ceza Sahası Dışı Şut", "sayac"),
    "hitWoodwork": ("Direkten Dönen", "sayac"),
    "bigChanceCreated": ("Net Pozisyon", "sayac"),
    "bigChanceScored": ("Gole Çevrilen Net Pozisyon", "sayac"),
    "bigChanceMissed": ("Kaçırılan Net Pozisyon", "sayac"),
    "touchesInOppBox": ("Rakip Ceza Sahasında Topla Buluşma", "sayac"),
    "cornerKicks": ("Korner", "sayac"),
    "throwIns": ("Taç", "sayac"),
    "freeKicks": ("Serbest Vuruş", "sayac"),
    "goalKicks": ("Kale Vuruşu", "sayac"),
    "offsides": ("Ofsayt", "sayac"),
    "fouls": ("Faul", "sayac"),
    "fouledFinalThird": ("Son Üçte Birde Kazanılan Faul", "sayac"),
    "kart": ("Kart (Sarı + Kırmızı)", "sayac"),
    "yellowCards": ("Sarı Kart", "sayac"),
    "redCards": ("Kırmızı Kart", "sayac"),
    "passes": ("Pas", "sayac"),
    "accuratePasses": ("İsabetli Pas", "sayac"),
    "accurateLongBalls": ("İsabetli Uzun Top", "sayac"),
    "accurateCross": ("İsabetli Orta", "sayac"),
    "accurateThroughBall": ("İsabetli Ara Pas", "sayac"),
    "finalThirdEntries": ("Son Üçte Bire Giriş", "sayac"),
    "finalThirdPhaseStatistic": ("Son Üçte Birde Pas", "sayac"),
    "totalTackle": ("Top Kapma Girişimi", "sayac"),
    "interceptionWon": ("Pas Arası", "sayac"),
    "ballRecovery": ("Top Kazanma", "sayac"),
    "totalClearance": ("Uzaklaştırma", "sayac"),
    "dispossessed": ("Top Kaybı", "sayac"),
    "errorsLeadToShot": ("Şutla Sonuçlanan Hata", "sayac"),
    "errorsLeadToGoal": ("Golle Sonuçlanan Hata", "sayac"),
    "goalkeeperSaves": ("Kaleci Kurtarışı", "sayac"),
    "diveSaves": ("Plonjonlu Kurtarış", "sayac"),
    "punches": ("Yumrukla Uzaklaştırma", "sayac"),
    "highClaims": ("Havadan Top Alma", "sayac"),
    "penaltySaves": ("Kurtarılan Penaltı", "sayac"),
    "numberOfSprints": ("Sprint Sayısı", "sayac"),
    "ballPossession": ("Topa Sahip Olma (%)", "oran"),
    "duelWonPercent": ("Kazanılan İkili Mücadele (%)", "oran"),
    "groundDuelsPercentage": ("Kazanılan Yer Mücadelesi (%)", "oran"),
    "aerialDuelsPercentage": ("Kazanılan Hava Topu (%)", "oran"),
    "dribblesPercentage": ("Başarılı Çalım (%)", "oran"),
    "wonTacklePercent": ("Başarılı Top Kapma (%)", "oran"),
    "goalsPrevented": ("Önlenen Gol", "surekli"),
    "kilometersCovered": ("Koşu Mesafesi (km)", "surekli"),
    "avgRating": ("Ortalama Reyting", "surekli"),
}

# İki takımın toplamı 100 olan istatistikler
TOPLAMI_100 = {"ballPossession", "duelWonPercent", "groundDuelsPercentage", "aerialDuelsPercentage"}

# SofaScore sıfır olduğunda bu sayaçları göndermiyor; istatistiği olan maçlarda boşluk = 0
SIFIRSA_BOS = ["yellowCards", "redCards", "hitWoodwork", "bigChanceCreated", "bigChanceScored",
               "bigChanceMissed", "errorsLeadToShot", "errorsLeadToGoal", "penaltySaves", "punches",
               "highClaims", "diveSaves", "offsides", "accurateThroughBall", "fouledFinalThird",
               "shotsOnGoal", "blockedScoringAttempt", "goalkeeperSaves"]

PERIYOTLAR = {"Tüm maç": "", "İlk yarı": "IY_", "İkinci yarı": "2Y_"}
