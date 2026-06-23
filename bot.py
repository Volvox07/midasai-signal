import os, logging, asyncio, json, requests, time
from datetime import datetime, timedelta
from pathlib import Path
import pytz, pandas as pd, ta, feedparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── Config ───────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))
AV_KEY    = os.getenv("AV_KEY", "")   # Alpha Vantage API key
TZ        = pytz.timezone("Europe/Istanbul")
DATA_FILE = Path("portfolio.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Watchlists ────────────────────────────────────────────────────────────
BIST_STOCKS = {
    "THYAO": "THYAO.IS", "EREGL": "EREGL.IS", "SASA": "SASA.IS",
    "BIMAS": "BIMAS.IS", "KCHOL": "KCHOL.IS", "ASELS": "ASELS.IS",
    "GARAN": "GARAN.IS", "AKBNK": "AKBNK.IS", "TUPRS": "TUPRS.IS",
    "FROTO": "FROTO.IS", "PETKM": "PETKM.IS", "TOASO": "TOASO.IS",
}
US_STOCKS = {
    "AAPL": "AAPL", "NVDA": "NVDA", "MSFT": "MSFT",
    "AMZN": "AMZN", "TSLA": "TSLA", "META": "META",
    "GOOGL": "GOOGL", "AMD": "AMD",
}

# ── Data Fetcher (Alpha Vantage) ──────────────────────────────────────────
_cache = {}

def fetch_daily(symbol: str) -> pd.DataFrame | None:
    """Fetch daily OHLCV from Alpha Vantage, with 1h cache."""
    cache_key = symbol
    if cache_key in _cache:
        ts, df = _cache[cache_key]
        if time.time() - ts < 3600:
            return df

    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=TIME_SERIES_DAILY&symbol={symbol}"
            f"&outputsize=compact&apikey={AV_KEY}"
        )
        r = requests.get(url, timeout=15)
        data = r.json()

        if "Time Series (Daily)" not in data:
            log.error(f"AV no data for {symbol}: {list(data.keys())}")
            return None

        ts_data = data["Time Series (Daily)"]
        rows = []
        for date_str, vals in ts_data.items():
            rows.append({
                "Date":   pd.to_datetime(date_str),
                "Open":   float(vals["1. open"]),
                "High":   float(vals["2. high"]),
                "Low":    float(vals["3. low"]),
                "Close":  float(vals["4. close"]),
                "Volume": float(vals["5. volume"]),
            })
        df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
        _cache[cache_key] = (time.time(), df)
        return df

    except Exception as e:
        log.error(f"fetch_daily {symbol}: {e}")
        return None

def fetch_quote(symbol: str) -> dict | None:
    """Fetch latest quote from Alpha Vantage."""
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=GLOBAL_QUOTE&symbol={symbol}&apikey={AV_KEY}"
        )
        r = requests.get(url, timeout=10)
        q = r.json().get("Global Quote", {})
        if not q:
            return None
        return {
            "price":  float(q.get("05. price", 0)),
            "change": float(q.get("10. change percent", "0%").replace("%", "")),
            "volume": float(q.get("06. volume", 0)),
        }
    except Exception as e:
        log.error(f"fetch_quote {symbol}: {e}")
        return None

# ── Portfolio Storage ─────────────────────────────────────────────────────
def load_portfolio() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"holdings": {}, "pending_reminders": []}

def save_portfolio(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# ── News Sentiment ────────────────────────────────────────────────────────
def get_news_sentiment(symbol: str) -> tuple:
    sym = symbol.replace(".IS", "")
    headlines = []
    try:
        url = f"https://news.google.com/rss/search?q={sym}+hisse+borsa&hl=tr&gl=TR&ceid=TR:tr"
        feed = feedparser.parse(url)
        for entry in feed.entries[:4]:
            headlines.append(entry.title[:80])
    except:
        pass

    pos_words = ["yüksel", "artış", "rekor", "güçlü", "kâr", "pozitif", "gain", "rise", "beat", "surge", "up"]
    neg_words = ["düşüş", "kayıp", "zarar", "kriz", "negatif", "sell", "fall", "loss", "drop", "weak", "down"]
    pos = sum(1 for h in headlines for w in pos_words if w in h.lower())
    neg = sum(1 for h in headlines for w in neg_words if w in h.lower())

    if pos > neg + 1:   sentiment = "🟢 Pozitif"
    elif neg > pos + 1: sentiment = "🔴 Negatif"
    else:               sentiment = "⚪ Nötr"
    return sentiment, headlines[:3]

# ── Analysis Engine ───────────────────────────────────────────────────────
def analyze_ticker(symbol: str) -> dict | None:
    df = fetch_daily(symbol)
    if df is None or len(df) < 20:
        return None
    try:
        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]
        vol   = df["Volume"]

        rsi_val  = ta.momentum.RSIIndicator(close, 14).rsi().iloc[-1]
        macd_obj = ta.trend.MACD(close)
        macd_val = macd_obj.macd().iloc[-1]
        macd_sig = macd_obj.macd_signal().iloc[-1]
        bb       = ta.volatility.BollingerBands(close, 20)
        bb_pct   = bb.bollinger_pband().iloc[-1]
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]
        stoch    = ta.momentum.StochasticOscillator(high, low, close)
        stoch_k  = stoch.stoch().iloc[-1]
        ema20    = ta.trend.EMAIndicator(close, 20).ema_indicator().iloc[-1]
        ema50    = ta.trend.EMAIndicator(close, 50).ema_indicator().iloc[-1]
        atr      = ta.volatility.AverageTrueRange(high, low, close).average_true_range().iloc[-1]
        vol_avg  = vol.rolling(20).mean().iloc[-1]
        vol_ratio = vol.iloc[-1] / vol_avg if vol_avg else 1

        price  = close.iloc[-1]
        prev   = close.iloc[-2]
        change = (price - prev) / prev * 100

        score = 0
        reasons = []

        if rsi_val < 30:
            score += 3; reasons.append(f"RSI aşırı satım ({rsi_val:.0f})")
        elif rsi_val < 40:
            score += 1; reasons.append(f"RSI düşük ({rsi_val:.0f})")
        elif rsi_val > 70:
            score -= 3; reasons.append(f"RSI aşırı alım ({rsi_val:.0f})")
        elif rsi_val > 60:
            score -= 1; reasons.append(f"RSI yüksek ({rsi_val:.0f})")

        if macd_val > macd_sig:
            score += 2; reasons.append("MACD ↑ pozitif kesişim")
        else:
            score -= 1; reasons.append("MACD ↓ negatif kesişim")

        if bb_pct < 0.15:
            score += 2; reasons.append("Bollinger alt band — aşırı satım")
        elif bb_pct > 0.85:
            score -= 2; reasons.append("Bollinger üst band — aşırı alım")

        if stoch_k < 20:
            score += 1; reasons.append(f"Stochastic aşırı satım ({stoch_k:.0f})")
        elif stoch_k > 80:
            score -= 1; reasons.append(f"Stochastic aşırı alım ({stoch_k:.0f})")

        if price > ema20 > ema50:
            score += 1; reasons.append("EMA20 > EMA50 — yükselen trend")
        elif price < ema20 < ema50:
            score -= 1; reasons.append("EMA20 < EMA50 — düşen trend")

        if vol_ratio > 1.8:
            score += 1; reasons.append(f"Yüksek hacim (×{vol_ratio:.1f})")

        if score >= 5:   signal = "🟢 GÜÇLÜ AL"
        elif score >= 2: signal = "🔵 AL"
        elif score <= -4: signal = "🔴 GÜÇLÜ SAT"
        elif score <= -1: signal = "🟠 SAT"
        else:            signal = "⚪ BEKLE"

        stop_loss  = round(price - 2 * atr, 2)
        target1    = round(price + 2 * atr, 2)
        target2    = round(price + 4 * atr, 2)
        limit_buy  = round(price * 0.995, 2)
        limit_sell = round(price * 1.005, 2)

        return {
            "symbol": symbol, "price": price, "change": change,
            "rsi": rsi_val, "macd": macd_val, "macd_sig": macd_sig,
            "bb_pct": bb_pct, "bb_upper": bb_upper, "bb_lower": bb_lower,
            "stoch_k": stoch_k, "ema20": ema20, "ema50": ema50,
            "vol_ratio": vol_ratio, "atr": atr,
            "signal": signal, "score": score, "reasons": reasons,
            "stop_loss": stop_loss, "target1": target1, "target2": target2,
            "limit_buy": limit_buy, "limit_sell": limit_sell,
        }
    except Exception as e:
        log.error(f"analyze_ticker calc {symbol}: {e}")
        return None

# ── Formatters ────────────────────────────────────────────────────────────
def cur_sym(symbol): return "₺" if symbol.endswith(".IS") else "$"
def clean_sym(symbol): return symbol.replace(".IS", "")

def format_order_card(r: dict) -> str:
    cur = cur_sym(r["symbol"])
    sym = clean_sym(r["symbol"])
    is_buy = "AL" in r["signal"]
    card = (
        f"\n<b>📋 EMİR KARTI — {sym}</b>\n"
        f"{'─'*26}\n"
        f"📍 Güncel Fiyat: {cur}{r['price']:.2f}\n\n"
    )
    if is_buy:
        card += (
            f"✅ <b>AL EMİRLERİ</b>\n"
            f"  🎯 Limit Alış:  {cur}{r['limit_buy']:.2f}\n"
            f"  🎯 Hedef 1:     {cur}{r['target1']:.2f} (+{((r['target1']/r['price'])-1)*100:.1f}%)\n"
            f"  🎯 Hedef 2:     {cur}{r['target2']:.2f} (+{((r['target2']/r['price'])-1)*100:.1f}%)\n"
            f"  🛑 Stop Loss:   {cur}{r['stop_loss']:.2f} (-{((1-(r['stop_loss']/r['price'])))*100:.1f}%)\n"
        )
    elif "SAT" in r["signal"]:
        card += (
            f"🔻 <b>SAT EMİRLERİ</b>\n"
            f"  🎯 Limit Satış: {cur}{r['limit_sell']:.2f}\n"
            f"  🛑 Stop Loss:   {cur}{r['stop_loss']:.2f}\n"
        )
    else:
        card += "⏸ <b>Şu an işlem önerilmiyor.</b>\n"
    card += (
        f"\n📊 RSI:{r['rsi']:.0f} | Stoch:{r['stoch_k']:.0f} | Hacim:×{r['vol_ratio']:.1f}\n"
        f"⚠️ <i>Bilgi amaçlıdır, yatırım tavsiyesi değildir.</i>"
    )
    return card

def format_full_analysis(r: dict, sentiment: str, headlines: list) -> str:
    cur = cur_sym(r["symbol"])
    sym = clean_sym(r["symbol"])
    macd_dir = "↑" if r["macd"] > r["macd_sig"] else "↓"
    text = (
        f"<b>🔍 {sym} TAM ANALİZ</b>\n"
        f"<i>{datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}</i>\n\n"
        f"💰 Fiyat: {cur}{r['price']:.2f} ({r['change']:+.2f}%)\n\n"
        f"━━━ 📈 TEKNİK ━━━\n"
        f"  RSI(14):    {r['rsi']:.1f}\n"
        f"  MACD:       {macd_dir} {'pozitif' if r['macd'] > r['macd_sig'] else 'negatif'}\n"
        f"  Bollinger:  %{r['bb_pct']*100:.0f} (alt:{cur}{r['bb_lower']:.2f} üst:{cur}{r['bb_upper']:.2f})\n"
        f"  Stochastic: {r['stoch_k']:.0f}\n"
        f"  EMA20/50:   {cur}{r['ema20']:.2f} / {cur}{r['ema50']:.2f}\n"
        f"  Hacim:      ×{r['vol_ratio']:.1f}\n\n"
        f"━━━ 📰 HABER ━━━\n"
        f"  Duygu: {sentiment}\n"
    )
    for h in (headlines or ["Haber bulunamadı"]):
        text += f"  • {h[:70]}\n"
    text += f"\n━━━ 💡 SEBEPLER ━━━\n"
    for s in r["reasons"]:
        text += f"  • {s}\n"
    text += f"\n<b>Sinyal: {r['signal']}</b>"
    text += format_order_card(r)
    return text

def format_report(results: list, title: str) -> str:
    lines = [f"<b>{title}</b>\n<i>{datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}</i>\n"]
    for r in results:
        if not r: continue
        cur = cur_sym(r["symbol"])
        sym = clean_sym(r["symbol"])
        lines.append(
            f"{r['signal']} <b>{sym}</b>\n"
            f"  💰 {cur}{r['price']:.2f} ({r['change']:+.2f}%)\n"
            f"  📊 RSI:{r['rsi']:.0f} | Stoch:{r['stoch_k']:.0f} | ×{r['vol_ratio']:.1f}\n"
            f"  🎯 Hedef:{cur}{r['target1']:.2f} 🛑 Stop:{cur}{r['stop_loss']:.2f}\n"
            f"  💡 {' | '.join(r['reasons'][:2])}\n"
        )
    lines.append("⚠️ <i>Bilgi amaçlıdır, yatırım tavsiyesi değildir.</i>")
    return "\n".join(lines)

# ── Reminder System ───────────────────────────────────────────────────────
async def check_reminders(app: Application):
    data = load_portfolio()
    now  = datetime.now(TZ)
    remaining = []
    for rem in data.get("pending_reminders", []):
        due = datetime.fromisoformat(rem["due"])
        if due.tzinfo is None:
            due = TZ.localize(due)
        if now >= due:
            await app.bot.send_message(
                CHAT_ID,
                f"⏰ <b>İŞLEM HATIRLATMASI</b>\n\n"
                f"30 dk önce <b>{rem['symbol']}</b> için sinyal göndermiştim.\n"
                f"Sinyal: {rem['signal']}\n"
                f"Fiyat: {rem['price']}\n\n"
                f"✅ İşlemi Midas'ta gerçekleştirdin mi?",
                parse_mode="HTML"
            )
        else:
            remaining.append(rem)
    data["pending_reminders"] = remaining
    save_portfolio(data)

def add_reminder(symbol, signal, price, minutes=30):
    data = load_portfolio()
    due  = datetime.now(TZ) + timedelta(minutes=minutes)
    data.setdefault("pending_reminders", []).append({
        "symbol": symbol, "signal": signal,
        "price": price, "due": due.isoformat()
    })
    save_portfolio(data)

# ── Scheduled Jobs ────────────────────────────────────────────────────────
async def send_bist_report(app: Application):
    await app.bot.send_message(CHAT_ID, "⏳ BIST sabah analizi hazırlanıyor…", parse_mode="HTML")
    results = []
    for sym, ticker in BIST_STOCKS.items():
        r = analyze_ticker(ticker)
        if r: results.append(r)
        await asyncio.sleep(12)   # AV rate limit: 5 req/min
    results.sort(key=lambda x: x["score"], reverse=True)
    await app.bot.send_message(CHAT_ID, format_report(results, "🌅 BIST Sabah Raporu"), parse_mode="HTML")

async def send_us_report(app: Application):
    await app.bot.send_message(CHAT_ID, "⏳ ABD seans öncesi analiz hazırlanıyor…", parse_mode="HTML")
    results = []
    for sym, ticker in US_STOCKS.items():
        r = analyze_ticker(ticker)
        if r: results.append(r)
        await asyncio.sleep(12)
    results.sort(key=lambda x: x["score"], reverse=True)
    await app.bot.send_message(CHAT_ID, format_report(results, "🌍 ABD Seans Öncesi Raporu"), parse_mode="HTML")

# ── Command Handlers ──────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kbd = [
        [InlineKeyboardButton("📊 BIST", callback_data="bist"),
         InlineKeyboardButton("🇺🇸 ABD", callback_data="us")],
        [InlineKeyboardButton("💼 Portföy", callback_data="portfoy"),
         InlineKeyboardButton("📋 Yardım", callback_data="yardim")],
    ]
    await update.message.reply_text(
        "👋 <b>MidasAI Signal</b> aktif!\n\n"
        "⏰ Otomatik raporlar:\n"
        "  🌅 09:30 — BIST sabah raporu\n"
        "  🌍 16:15 — ABD seans öncesi\n\n"
        "Komutlar:\n"
        "/analiz THYAO — Tam analiz + emir kartı\n"
        "/bist — BIST anlık analiz\n"
        "/us — ABD anlık analiz\n"
        "/portfoy — Portföyüm\n"
        "/ekle THYAO 10 245.50 — Portföye ekle\n"
        "/yardim — Tüm komutlar",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kbd)
    )

async def cmd_bist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ BIST analizi yapılıyor… (birkaç dk sürebilir)")
    results = []
    for ticker in BIST_STOCKS.values():
        r = analyze_ticker(ticker)
        if r: results.append(r)
        await asyncio.sleep(12)
    results.sort(key=lambda x: x["score"], reverse=True)
    await msg.edit_text(format_report(results, "📊 BIST Anlık Analiz"), parse_mode="HTML")

async def cmd_us(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ ABD hisseleri analiz ediliyor… (birkaç dk sürebilir)")
    results = []
    for ticker in US_STOCKS.values():
        r = analyze_ticker(ticker)
        if r: results.append(r)
        await asyncio.sleep(12)
    results.sort(key=lambda x: x["score"], reverse=True)
    await msg.edit_text(format_report(results, "🇺🇸 ABD Anlık Analiz"), parse_mode="HTML")

async def cmd_analiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Kullanım: /analiz THYAO veya /analiz AAPL")
        return
    sym_input = ctx.args[0].upper()
    ticker = BIST_STOCKS.get(sym_input, US_STOCKS.get(sym_input, sym_input + ".IS"))

    msg = await update.message.reply_text(f"⏳ {sym_input} analiz ediliyor…")
    r = analyze_ticker(ticker)
    if not r:
        await msg.edit_text(f"❌ {sym_input} için veri alınamadı.\nAPI limitini aştıysak birkaç dakika bekle.")
        return

    sentiment, headlines = get_news_sentiment(ticker)
    await msg.edit_text(format_full_analysis(r, sentiment, headlines), parse_mode="HTML")
    add_reminder(sym_input, r["signal"], f"{cur_sym(ticker)}{r['price']:.2f}", minutes=30)
    await update.message.reply_text(
        "⏰ 30 dakika sonra işlemi yaptın mı diye soracağım.",
        parse_mode="HTML"
    )

async def cmd_portfoy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_portfolio()
    holdings = data.get("holdings", {})
    if not holdings:
        await update.message.reply_text(
            "💼 Portföyün boş.\n\nEklemek için:\n/ekle THYAO 10 245.50\n(sembol adet alış-fiyatı)"
        )
        return
    lines = ["<b>💼 PORTFÖYÜM</b>\n"]
    for sym, pos in holdings.items():
        ticker = BIST_STOCKS.get(sym, US_STOCKS.get(sym, sym + ".IS"))
        r = analyze_ticker(ticker)
        cur = cur_sym(ticker)
        cost = pos["adet"] * pos["alis"]
        if r:
            value = pos["adet"] * r["price"]
            pnl = value - cost
            pnl_pct = (r["price"] / pos["alis"] - 1) * 100
            lines.append(
                f"<b>{sym}</b> {pos['adet']} adet\n"
                f"  Alış:{cur}{pos['alis']:.2f} → Şimdi:{cur}{r['price']:.2f}\n"
                f"  K/Z: {'+' if pnl>=0 else ''}{cur}{pnl:.2f} ({pnl_pct:+.1f}%)\n"
                f"  {r['signal']}\n"
            )
        else:
            lines.append(f"<b>{sym}</b> — veri yok\n")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_ekle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 3:
        await update.message.reply_text("Kullanım: /ekle THYAO 10 245.50")
        return
    sym  = ctx.args[0].upper()
    adet = float(ctx.args[1])
    alis = float(ctx.args[2])
    data = load_portfolio()
    data.setdefault("holdings", {})[sym] = {"adet": adet, "alis": alis}
    save_portfolio(data)
    await update.message.reply_text(f"✅ {sym} portföye eklendi: {adet} adet @ {alis}")

async def cmd_yardim(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>📋 Komut Listesi</b>\n\n"
        "/start — Ana menü\n"
        "/bist — BIST anlık analiz (tümü)\n"
        "/us — ABD anlık analiz (tümü)\n"
        "/analiz THYAO — Tam analiz + emir kartı + hatırlatıcı\n"
        "/portfoy — Portföy K/Z durumu\n"
        "/ekle THYAO 10 245.50 — Portföye ekle\n\n"
        "<b>⏰ Otomatik</b>\n"
        "🌅 09:30 BIST raporu\n"
        "🌍 16:15 ABD raporu\n"
        "⏰ Sinyal sonrası 30dk hatırlatma",
        parse_mode="HTML"
    )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    fake = type("U", (), {"message": q.message, "reply_text": q.message.reply_text})()
    if q.data == "bist":
        await cmd_bist(fake, ctx)
    elif q.data == "us":
        await cmd_us(fake, ctx)
    elif q.data == "portfoy":
        await cmd_portfoy(fake, ctx)
    elif q.data == "yardim":
        await cmd_yardim(fake, ctx)

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    if not AV_KEY:
        log.warning("AV_KEY not set! Data fetching will fail.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("bist",    cmd_bist))
    app.add_handler(CommandHandler("us",      cmd_us))
    app.add_handler(CommandHandler("analiz",  cmd_analiz))
    app.add_handler(CommandHandler("portfoy", cmd_portfoy))
    app.add_handler(CommandHandler("ekle",    cmd_ekle))
    app.add_handler(CommandHandler("yardim",  cmd_yardim))
    app.add_handler(CallbackQueryHandler(button_handler))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(send_bist_report, "cron", hour=9,  minute=30, args=[app])
    scheduler.add_job(send_us_report,   "cron", hour=16, minute=15, args=[app])
    scheduler.add_job(check_reminders,  "interval", minutes=5, args=[app])
    scheduler.start()

    log.info("MidasAI Signal v3 starting…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
