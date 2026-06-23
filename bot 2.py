import os, logging, asyncio, json
from datetime import datetime, timedelta
from pathlib import Path
import pytz, yfinance as yf, pandas as pd, ta, feedparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── Config ───────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))
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
    "GOOGL": "GOOGL", "AMD": "AMD", "BABA": "BABA",
}

# ── Portfolio Storage ─────────────────────────────────────────────────────
def load_portfolio() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"holdings": {}, "pending_reminders": []}

def save_portfolio(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# ── News Fetcher ──────────────────────────────────────────────────────────
def get_news_sentiment(symbol: str) -> tuple[str, list[str]]:
    """Fetch RSS news and return (sentiment, headlines)"""
    sym = symbol.replace(".IS", "")
    feeds = [
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=TR&lang=tr-TR",
        f"https://news.google.com/rss/search?q={sym}+borsa&hl=tr&gl=TR&ceid=TR:tr",
    ]
    headlines = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                headlines.append(entry.title[:80])
        except:
            pass

    if not headlines:
        return "⚪ Nötr", []

    positive_words = ["yüksel", "artış", "rekor", "güçlü", "büyüme", "kâr", "al", "pozitif", "rally", "gain", "up", "rise", "beat", "surge"]
    negative_words = ["düşüş", "kayıp", "zarar", "satış", "kriz", "negatif", "sell", "down", "fall", "loss", "miss", "drop", "weak"]

    pos = sum(1 for h in headlines for w in positive_words if w in h.lower())
    neg = sum(1 for h in headlines for w in negative_words if w in h.lower())

    if pos > neg + 1:
        sentiment = "🟢 Pozitif"
    elif neg > pos + 1:
        sentiment = "🔴 Negatif"
    else:
        sentiment = "⚪ Nötr"

    return sentiment, headlines[:3]

# ── Fundamental Analysis ──────────────────────────────────────────────────
def get_fundamentals(ticker_obj) -> dict:
    try:
        info = ticker_obj.info
        return {
            "pe":       info.get("trailingPE"),
            "pb":       info.get("priceToBook"),
            "ps":       info.get("priceToSalesTrailing12Months"),
            "roe":      info.get("returnOnEquity"),
            "margin":   info.get("profitMargins"),
            "debt_eq":  info.get("debtToEquity"),
            "rev_growth": info.get("revenueGrowth"),
            "sector":   info.get("sector", ""),
            "name":     info.get("shortName", ""),
        }
    except:
        return {}

def score_fundamentals(f: dict) -> tuple[int, list[str]]:
    score = 0
    reasons = []
    if f.get("pe"):
        if f["pe"] < 10:
            score += 2; reasons.append(f"F/K düşük ({f['pe']:.1f}) — ucuz")
        elif f["pe"] < 20:
            score += 1; reasons.append(f"F/K makul ({f['pe']:.1f})")
        elif f["pe"] > 40:
            score -= 1; reasons.append(f"F/K yüksek ({f['pe']:.1f}) — pahalı")
    if f.get("pb"):
        if f["pb"] < 1:
            score += 2; reasons.append(f"PD/DD < 1 ({f['pb']:.2f}) — defter altı")
        elif f["pb"] < 2:
            score += 1; reasons.append(f"PD/DD makul ({f['pb']:.2f})")
    if f.get("roe"):
        roe_pct = f["roe"] * 100
        if roe_pct > 20:
            score += 1; reasons.append(f"ROE yüksek (%{roe_pct:.0f})")
        elif roe_pct < 5:
            score -= 1; reasons.append(f"ROE düşük (%{roe_pct:.0f})")
    if f.get("debt_eq") and f["debt_eq"] > 200:
        score -= 1; reasons.append(f"Yüksek borç/özkaynak ({f['debt_eq']:.0f}%)")
    return score, reasons

# ── Technical Analysis ────────────────────────────────────────────────────
def analyze_ticker(symbol: str, full: bool = False) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo", interval="1d")
        if df.empty or len(df) < 20:
            return None

        close = df["Close"].squeeze()
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()
        vol   = df["Volume"].squeeze()

        # Technical indicators
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

        price   = close.iloc[-1]
        prev    = close.iloc[-2]
        change  = (price - prev) / prev * 100
        week_lo = close.tail(5).min()
        week_hi = close.tail(5).max()

        # Technical score
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

        # Fundamental score
        fund_score, fund_reasons = 0, []
        fundamentals = {}
        if full:
            fundamentals = get_fundamentals(ticker)
            fund_score, fund_reasons = score_fundamentals(fundamentals)

        total_score = score + fund_score

        if total_score >= 5:
            signal = "🟢 GÜÇLÜ AL"
        elif total_score >= 2:
            signal = "🔵 AL"
        elif total_score <= -4:
            signal = "🔴 GÜÇLÜ SAT"
        elif total_score <= -1:
            signal = "🟠 SAT"
        else:
            signal = "⚪ BEKLE"

        # Order suggestions
        stop_loss  = round(price - 2 * atr, 2)
        target1    = round(price + 2 * atr, 2)
        target2    = round(price + 4 * atr, 2)
        limit_buy  = round(price * 0.995, 2)   # 0.5% altından limit
        limit_sell = round(price * 1.005, 2)

        return {
            "symbol": symbol, "price": price, "change": change,
            "rsi": rsi_val, "macd": macd_val, "macd_sig": macd_sig,
            "bb_pct": bb_pct, "bb_upper": bb_upper, "bb_lower": bb_lower,
            "stoch_k": stoch_k, "ema20": ema20, "ema50": ema50,
            "vol_ratio": vol_ratio, "atr": atr,
            "signal": signal, "score": total_score,
            "reasons": reasons, "fund_reasons": fund_reasons,
            "fundamentals": fundamentals,
            "stop_loss": stop_loss, "target1": target1, "target2": target2,
            "limit_buy": limit_buy, "limit_sell": limit_sell,
            "week_lo": week_lo, "week_hi": week_hi,
        }
    except Exception as e:
        log.error(f"analyze_ticker {symbol}: {e}")
        return None

# ── Order Card ────────────────────────────────────────────────────────────
def format_order_card(r: dict) -> str:
    cur = "₺" if r["symbol"].endswith(".IS") else "$"
    sym = r["symbol"].replace(".IS", "")
    is_buy = "AL" in r["signal"]
    is_sell = "SAT" in r["signal"]

    card = (
        f"<b>📋 EMİR KARTI — {sym}</b>\n"
        f"{'─'*28}\n"
        f"📍 Güncel Fiyat:  {cur}{r['price']:.2f}\n\n"
    )

    if is_buy:
        card += (
            f"✅ <b>AL EMİRLERİ</b>\n"
            f"  🎯 Limit Alış:   {cur}{r['limit_buy']:.2f}\n"
            f"  🎯 Hedef 1:      {cur}{r['target1']:.2f} (+{((r['target1']/r['price'])-1)*100:.1f}%)\n"
            f"  🎯 Hedef 2:      {cur}{r['target2']:.2f} (+{((r['target2']/r['price'])-1)*100:.1f}%)\n"
            f"  🛑 Stop Loss:    {cur}{r['stop_loss']:.2f} (-{((1-(r['stop_loss']/r['price'])))*100:.1f}%)\n"
        )
    elif is_sell:
        card += (
            f"🔻 <b>SAT EMİRLERİ</b>\n"
            f"  🎯 Limit Satış:  {cur}{r['limit_sell']:.2f}\n"
            f"  📉 Hedef:        {cur}{r['stop_loss']:.2f}\n"
        )
    else:
        card += f"⏸ <b>Şu an işlem önerilmiyor.</b>\n"

    card += (
        f"\n📊 <b>Göstergeler</b>\n"
        f"  RSI: {r['rsi']:.0f}  |  Stoch: {r['stoch_k']:.0f}  |  Hacim: ×{r['vol_ratio']:.1f}\n"
        f"  Bollinger: %{r['bb_pct']*100:.0f}  |  ATR: {cur}{r['atr']:.2f}\n"
        f"\n⚠️ <i>Bilgi amaçlıdır, yatırım tavsiyesi değildir.</i>"
    )
    return card

# ── Full Analysis Message ─────────────────────────────────────────────────
def format_full_analysis(r: dict, news_sentiment: str, headlines: list) -> str:
    cur = "₺" if r["symbol"].endswith(".IS") else "$"
    sym = r["symbol"].replace(".IS", "")
    f   = r.get("fundamentals", {})
    macd_dir = "↑" if r["macd"] > r["macd_sig"] else "↓"

    text = (
        f"<b>🔍 {sym} TAM ANALİZ</b>\n"
        f"<i>{datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}</i>\n\n"
        f"💰 <b>Fiyat:</b> {cur}{r['price']:.2f} ({r['change']:+.2f}%)\n"
        f"📅 Haftalık: {cur}{r['week_lo']:.2f} – {cur}{r['week_hi']:.2f}\n\n"
        f"━━━ 📈 TEKNİK ANALİZ ━━━\n"
        f"  RSI(14):     {r['rsi']:.1f}\n"
        f"  MACD:        {macd_dir} {'pozitif' if r['macd'] > r['macd_sig'] else 'negatif'} kesişim\n"
        f"  Bollinger:   %{r['bb_pct']*100:.0f} (alt:{cur}{r['bb_lower']:.2f} üst:{cur}{r['bb_upper']:.2f})\n"
        f"  Stochastic:  {r['stoch_k']:.0f}\n"
        f"  EMA20/50:    {cur}{r['ema20']:.2f} / {cur}{r['ema50']:.2f}\n"
        f"  Hacim:       ×{r['vol_ratio']:.1f} (20g ort.)\n"
        f"  ATR:         {cur}{r['atr']:.2f}\n\n"
    )

    if f:
        text += f"━━━ 📊 TEMEL ANALİZ ━━━\n"
        if f.get("pe"):    text += f"  F/K:         {f['pe']:.1f}\n"
        if f.get("pb"):    text += f"  PD/DD:       {f['pb']:.2f}\n"
        if f.get("roe"):   text += f"  ROE:         %{f['roe']*100:.1f}\n"
        if f.get("margin"):text += f"  Kâr Marjı:  %{f['margin']*100:.1f}\n"
        if f.get("debt_eq"):text += f"  Borç/Özk:   {f['debt_eq']:.0f}%\n"
        if f.get("rev_growth"): text += f"  Gelir Büyüme:%{f['rev_growth']*100:.1f}\n"
        text += "\n"

    text += f"━━━ 📰 HABER ANALİZİ ━━━\n"
    text += f"  Duygu: {news_sentiment}\n"
    if headlines:
        for h in headlines:
            text += f"  • {h[:70]}\n"
    else:
        text += "  • Haber bulunamadı\n"

    text += f"\n━━━ 💡 SEBEPLER ━━━\n"
    for s in r["reasons"]:
        text += f"  • {s}\n"
    if r.get("fund_reasons"):
        for s in r["fund_reasons"]:
            text += f"  • {s}\n"

    text += f"\n<b>Sinyal: {r['signal']}</b>\n\n"
    text += format_order_card(r)
    return text

# ── Report Formatter ──────────────────────────────────────────────────────
def format_report(results: list, title: str) -> str:
    lines = [f"<b>{title}</b>\n<i>{datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}</i>\n"]
    for r in results:
        if not r: continue
        cur = "₺" if r["symbol"].endswith(".IS") else "$"
        sym = r["symbol"].replace(".IS", "")
        lines.append(
            f"{r['signal']} <b>{sym}</b>\n"
            f"  💰 {cur}{r['price']:.2f} ({r['change']:+.2f}%)\n"
            f"  📊 RSI:{r['rsi']:.0f} | Stoch:{r['stoch_k']:.0f} | Hacim:×{r['vol_ratio']:.1f}\n"
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
                f"Az önce sana <b>{rem['signal']}</b> sinyali göndermiştim.\n"
                f"📌 Hisse: <b>{rem['symbol']}</b>\n"
                f"💰 Önerilen fiyat: {rem['price']}\n\n"
                f"✅ İşlemi gerçekleştirdin mi?\n"
                f"/evet_{rem['symbol']} — Evet, yaptım\n"
                f"/hayir_{rem['symbol']} — Hayır, geçtim",
                parse_mode="HTML"
            )
        else:
            remaining.append(rem)
    data["pending_reminders"] = remaining
    save_portfolio(data)

def add_reminder(symbol: str, signal: str, price: str, minutes: int = 30):
    data = load_portfolio()
    due  = datetime.now(TZ) + timedelta(minutes=minutes)
    data.setdefault("pending_reminders", []).append({
        "symbol": symbol, "signal": signal,
        "price": price, "due": due.isoformat()
    })
    save_portfolio(data)

# ── Scheduled Jobs ─────────────────────────────────────────────────────────
async def send_bist_report(app: Application):
    await app.bot.send_message(CHAT_ID, "⏳ BIST sabah analizi hazırlanıyor…", parse_mode="HTML")
    results = [analyze_ticker(v) for v in BIST_STOCKS.values()]
    results = [r for r in results if r]
    results.sort(key=lambda x: x["score"], reverse=True)
    await app.bot.send_message(CHAT_ID, format_report(results, "🌅 BIST Sabah Raporu"), parse_mode="HTML")

async def send_us_report(app: Application):
    await app.bot.send_message(CHAT_ID, "⏳ ABD seans öncesi analiz hazırlanıyor…", parse_mode="HTML")
    results = [analyze_ticker(v) for v in US_STOCKS.values()]
    results = [r for r in results if r]
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
        "/ekle THYAO 10 245.50 — Portföye ekle\n\n"
        "Aşağıdan hızlı seç 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kbd)
    )

async def cmd_bist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ BIST analizi yapılıyor…")
    results = [analyze_ticker(v) for v in BIST_STOCKS.values()]
    results = [r for r in results if r]
    results.sort(key=lambda x: x["score"], reverse=True)
    await msg.edit_text(format_report(results, "📊 BIST Anlık Analiz"), parse_mode="HTML")

async def cmd_us(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ ABD hisseleri analiz ediliyor…")
    results = [analyze_ticker(v) for v in US_STOCKS.values()]
    results = [r for r in results if r]
    results.sort(key=lambda x: x["score"], reverse=True)
    await msg.edit_text(format_report(results, "🇺🇸 ABD Anlık Analiz"), parse_mode="HTML")

async def cmd_analiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Kullanım: /analiz THYAO veya /analiz AAPL")
        return
    sym_input = ctx.args[0].upper()
    if sym_input in BIST_STOCKS:
        ticker = BIST_STOCKS[sym_input]
    elif sym_input in US_STOCKS:
        ticker = US_STOCKS[sym_input]
    else:
        ticker = sym_input + ".IS" if not sym_input.endswith(".IS") else sym_input

    msg = await update.message.reply_text(f"⏳ {sym_input} tam analiz yapılıyor (haber + temel + teknik)…")
    r = analyze_ticker(ticker, full=True)
    if not r:
        await msg.edit_text(f"❌ {sym_input} için veri alınamadı.")
        return

    sentiment, headlines = get_news_sentiment(ticker)
    full_text = format_full_analysis(r, sentiment, headlines)
    await msg.edit_text(full_text, parse_mode="HTML")

    # Add reminder 30 min later
    cur = "₺" if ticker.endswith(".IS") else "$"
    add_reminder(sym_input, r["signal"], f"{cur}{r['price']:.2f}", minutes=30)
    await update.message.reply_text(
        f"⏰ <b>Hatırlatıcı kuruldu!</b>\n30 dakika sonra işlem yaptın mı diye soracağım.",
        parse_mode="HTML"
    )

async def cmd_portfoy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_portfolio()
    holdings = data.get("holdings", {})
    if not holdings:
        await update.message.reply_text(
            "💼 Portföyün boş.\n\nEklemek için:\n/ekle THYAO 10 245.50\n(sembol, adet, alış fiyatı)"
        )
        return

    lines = ["<b>💼 PORTFÖYÜm</b>\n"]
    total_cost = 0
    total_value = 0
    for sym, pos in holdings.items():
        ticker_sym = BIST_STOCKS.get(sym, US_STOCKS.get(sym, sym + ".IS"))
        r = analyze_ticker(ticker_sym)
        cur = "₺" if ticker_sym.endswith(".IS") else "$"
        cost = pos["adet"] * pos["alis"]
        total_cost += cost
        if r:
            value = pos["adet"] * r["price"]
            total_value += value
            pnl = value - cost
            pnl_pct = (r["price"] / pos["alis"] - 1) * 100
            lines.append(
                f"<b>{sym}</b> {pos['adet']} adet\n"
                f"  Alış: {cur}{pos['alis']:.2f} → Şimdi: {cur}{r['price']:.2f}\n"
                f"  Kâr/Zarar: {'+' if pnl >= 0 else ''}{cur}{pnl:.2f} ({pnl_pct:+.1f}%)\n"
                f"  Sinyal: {r['signal']}\n"
            )
        else:
            lines.append(f"<b>{sym}</b> {pos['adet']} adet — veri yok\n")

    if total_value:
        total_pnl = total_value - total_cost
        lines.append(f"\n<b>Toplam K/Z: {'+' if total_pnl >= 0 else ''}{total_pnl:.2f} ({(total_value/total_cost-1)*100:+.1f}%)</b>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_ekle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 3:
        await update.message.reply_text("Kullanım: /ekle THYAO 10 245.50")
        return
    sym   = ctx.args[0].upper()
    adet  = float(ctx.args[1])
    alis  = float(ctx.args[2])
    data  = load_portfolio()
    data.setdefault("holdings", {})[sym] = {"adet": adet, "alis": alis}
    save_portfolio(data)
    await update.message.reply_text(f"✅ {sym} portföye eklendi: {adet} adet @ {alis}")

async def cmd_yardim(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>📋 Komut Listesi</b>\n\n"
        "/start — Ana menü\n"
        "/bist — BIST anlık analiz\n"
        "/us — ABD anlık analiz\n"
        "/analiz THYAO — Tam analiz (teknik + temel + haber + emir kartı)\n"
        "/portfoy — Portföy takibi ve K/Z durumu\n"
        "/ekle THYAO 10 245.50 — Portföye hisse ekle\n"
        "/yardim — Bu ekran\n\n"
        "<b>⏰ Otomatik Raporlar</b>\n"
        "🌅 09:30 — BIST sabah raporu\n"
        "🌍 16:15 — ABD seans öncesi\n"
        "⏰ Sinyal sonrası 30dk — İşlem hatırlatması\n\n"
        "<b>📊 İzlenen BIST:</b>\nTHYAO EREGL SASA BIMAS KCHOL ASELS GARAN AKBNK TUPRS FROTO PETKM TOASO\n\n"
        "<b>🇺🇸 İzlenen ABD:</b>\nAAPL NVDA MSFT AMZN TSLA META GOOGL AMD BABA",
        parse_mode="HTML"
    )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "bist":
        await q.message.reply_text("⏳ BIST analiz ediliyor…")
        results = [analyze_ticker(v) for v in BIST_STOCKS.values()]
        results = [r for r in results if r]
        results.sort(key=lambda x: x["score"], reverse=True)
        await q.message.reply_text(format_report(results, "📊 BIST Anlık Analiz"), parse_mode="HTML")
    elif q.data == "us":
        await q.message.reply_text("⏳ ABD analiz ediliyor…")
        results = [analyze_ticker(v) for v in US_STOCKS.values()]
        results = [r for r in results if r]
        results.sort(key=lambda x: x["score"], reverse=True)
        await q.message.reply_text(format_report(results, "🇺🇸 ABD Anlık Analiz"), parse_mode="HTML")
    elif q.data == "portfoy":
        await cmd_portfoy(update, ctx)
    elif q.data == "yardim":
        await cmd_yardim(update, ctx)

# ── Main ──────────────────────────────────────────────────────────────────
def main():
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
    scheduler.add_job(send_bist_report,  "cron", hour=9,  minute=30, args=[app])
    scheduler.add_job(send_us_report,    "cron", hour=16, minute=15, args=[app])
    scheduler.add_job(check_reminders,   "interval", minutes=5, args=[app])
    scheduler.start()

    log.info("MidasAI Signal v2 starting…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
