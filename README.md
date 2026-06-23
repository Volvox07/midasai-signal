# MidasAI Signal 🤖📊

Midas üzerinden yatırım yaparken AI destekli sinyal sistemi.

## Ne Yapar?
- Her sabah 09:30'da BIST analiz raporu gönderir
- Her gün 16:15'te ABD seans öncesi raporu gönderir
- İstediğin zaman /analiz THYAO yazarak anlık analiz alırsın
- RSI, MACD, Bollinger Bands ve Hacim analizini birleştirir

## Railway'e Deploy (ÜCRETSİZ, 7/24 Çalışır)

### Adım 1 — GitHub'a Yükle
1. https://github.com adresine git, ücretsiz hesap aç
2. "New Repository" → isim: `midasai-signal` → Public → Create
3. Bu klasördeki tüm dosyaları yükle (Upload files)

### Adım 2 — Railway'e Deploy
1. https://railway.app adresine git
2. "Login with GitHub" ile giriş yap
3. "New Project" → "Deploy from GitHub repo" → `midasai-signal` seç
4. Deploy başlar, bekle (2-3 dk)

### Adım 3 — Environment Variables Ekle
Railway dashboard'unda projeye tıkla → "Variables" sekmesi → şunları ekle:
```
BOT_TOKEN = [BotFather'dan aldığın token]
CHAT_ID   = 1302111261
```

### Adım 4 — Test
Telegram'da botuna /start yaz → menü gelirse kurulum tamam! 🎉

## Komutlar
| Komut | Açıklama |
|-------|----------|
| /start | Ana menü |
| /bist | Tüm BIST hisseleri analizi |
| /us | Tüm ABD hisseleri analizi |
| /analiz THYAO | Tek hisse detaylı analiz |
| /yardim | Komut listesi |

## İzlenen Hisseler
**BIST:** THYAO, EREGL, SASA, BIMAS, KCHOL, ASELS, GARAN, AKBNK, TUPRS, FROTO  
**ABD:** AAPL, NVDA, MSFT, AMZN, TSLA, META, GOOGL

## Hisse Eklemek/Çıkarmak
bot.py dosyasında `BIST_STOCKS` veya `US_STOCKS` sözlüklerini düzenle.

## Önemli Not
Bu sistem bilgi amaçlıdır. Son karar her zaman senin.  
Token'ını kimseyle paylaşma!
