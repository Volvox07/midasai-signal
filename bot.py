import os
import logging
import asyncio
from datetime import datetime
import pytz
import yfinance as yf
import pandas as pd
import ta
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── Config ──────────────────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN", "8864247917:AAEiIdjm8i9T7ZtBdlsioL2M1vZx17HOiA4")
CHAT_ID    = int(os.getenv("CHAT_ID", "1302111261"))
TZ         = pytz.timezone("Europe/Istanbul")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Watchlists ───────────────────────────────────────────────────────────
BIST_STOCKS = {
    "THYAO": "THYAO.IS", "EREGL": "EREGL.IS", "SASA": "SASA.IS",
    "BIMAS": "BIMAS.IS", "KCHOL": "KCHOL.IS", "ASELS": "ASELS.IS",
    "GARAN": "GARAN.IS", "AKBNK": "AKBNK.IS", "TUPRS": "TUPRS.IS",
    "FROTO": "FROTO.IS",
}
US_STOCKS = {
    "AAPL": "AAPL", "NVDA": "NVDA", "MSFT": "MSFT",
    "AMZN": "AMZN", "TSLA": "TSLA", "META": "META",
    "GOOGL": "GOOGL",
}

# ── Analysis Engine ──────────────────────────────────────────────────────
def analyze_ticker(symbol: str) -> dict:
    """Fetch data and compute technical indicators."""
    try:
        df = yf.download(symbol, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 20:
            return None

        close = df["Close"].squeeze()
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()
        vol   = df["Volume"].squeeze()

        rsi_val   = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
        macd_obj  = ta.trend.MACD(close)
        macd_val  = macd_obj.macd().iloc[-1]
        macd_sig  = macd_obj.macd_signal().iloc[-1]
        bb        = ta.volatility.BollingerBands(close, window=20)
        bb_pct    = bb.bollinger_pband().iloc[-1]   # 0=lower band, 1=upper band
        vol_avg   = vol.rolling(20).mean().iloc[-1]
        vol_ratio = vol.iloc[-1] / vol_avg if vol_avg else 1

        price     = close.iloc[-1]
        prev      = close.iloc[-2]
        change_p  = (price - prev) / prev * 100

        # ── Signal logic ─────────────────────────────────────────────
        score = 0
        reasons = []

        if rsi_val < 35:
            score += 2; reasons.append(f"RSI aşırı satım ({rsi_val:.0f})")
        elif rsi_val < 45:
            score += 1; reasons.append(f"RSI düşük ({rsi_val:.0f})")
        elif rsi_val > 65:
            score -= 2; reasons.append(f"RSI aşırı alım ({rsi_val:.0f})")
        elif rsi_val > 55:
            score -= 1; reasons.append(f"RSI yüksek ({rsi_val:.0f})")

        if macd_val > macd_sig:
            score += 1; reasons.append("MACD ↑ pozitif kesişim")
        else:
            score -= 1; reasons.append("MACD ↓ negatif kesişim")

        if bb_pct < 0.2:
            score += 1; reasons.append("Bollinger alt band yakını")
        elif bb_pct > 0.8:
            score -= 1; reasons.append("Bollinger üst band yakını")

        if vol_ratio > 1.5:
            score += 1; reasons.append(f"Yüksek hacim (×{vol_ratio:.1f})")

        if score >= 3:
            signal = "🟢 GÜÇLÜ AL"
        elif score >= 1:
            signal = "🔵 AL"
        elif score <= -3:
            signal = "🔴 GÜÇLÜ SAT"
        elif score <= -1:
            signal = "🟠 SAT"
        else:
            signal = "⚪ BEKLE"

        return {
            "symbol": symbol, "price": price, "change": change_p,
            "rsi": rsi_val, "macd": macd_val, "macd_sig": macd_sig,
            "bb_pct": bb_pct, "vol_ratio": vol_ratio,
            "signal": signal, "score": score, "reasons": reasons,
        }
    except Exception as e:
        log.error(f"analyze_ticker {symbol}: {e}")
        return None


def format_report(results: list, title: str) -> str:
    lines = [f"<b>{title}</b>\n<i>{datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}</i>\n"]
    for r in results:
        if not r:
            continue
        cur = "₺" if r["symbol"].endswith(".IS") else "$"
        sym = r["symbol"].replace(".IS", "")
        lines.append(
            f"{r['signal']} <b>{sym}</b>\n"
            f"  💰 {cur}{r['price']:.2f}  ({r['change']:+.2f}%)\n"
            f"  📊 RSI:{r['rsi']:.0f}  BB:{r['bb_pct']:.0%}\n"
            f"  💡 {' | '.join(r['reasons'][:2])}\n"
        )
    lines.append("\n⚠️ <i>Bu sinyaller bilgi amaçlıdır. Son karar her zaman sizindir.</i>")
    return "\n".join(lines)


# ── Scheduled Jobs ───────────────────────────────────────────────────────
async def send_bist_report(app: Application):
    log.info("Sending BIST morning report…")
    results = [analyze_ticker(v) for v in BIST_STOCKS.values()]
    results.sort(key=lambda x: (x["score"] if x else 0), reverse=True)
    msg = format_report(results, "🌅 BIST Sabah Raporu")
    await app.bot.send_message(CHAT_ID, msg, parse_mode="HTML")


async def send_us_report(app: Application):
    log.info("Sending US pre-market report…")
    results = [analyze_ticker(v) for v in US_STOCKS.values()]
    results.sort(key=lambda x: (x["score"] if x else 0), reverse=True)
    msg = format_report(results, "🌍 ABD Seans Öncesi Raporu")
    await app.bot.send_message(CHAT_ID, msg, parse_mode="HTML")


# ── Command Handlers ──────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kbd = [
        [InlineKeyboardButton("📊 BIST Analizi", callback_data="bist"),
         InlineKeyboardButton("🇺🇸 ABD Analizi", callback_data="us")],
        [InlineKeyboardButton("🔍 Hisse Ara", callback_data="search_hint")],
    ]
    await update.message.reply_text(
        "👋 <b>MidasAI Signal</b> aktif!\n\n"
        "Komutlar:\n"
        "/bist — BIST sabah raporu\n"
        "/us — ABD raporu\n"
        "/analiz THYAO — Tek hisse analizi\n"
        "/portfoy — Portföyünü göster\n"
        "/yardim — Tüm komutlar\n\n"
        "Ya da aşağıdan seç 👇",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kbd)
    )


async def cmd_bist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ BIST analizi yapılıyor…")
    results = [analyze_ticker(v) for v in BIST_STOCKS.values()]
    results.sort(key=lambda x: (x["score"] if x else 0), reverse=True)
    report = format_report(results, "📊 BIST Anlık Analiz")
    await msg.edit_text(report, parse_mode="HTML")


async def cmd_us(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ ABD hisseleri analiz ediliyor…")
    results = [analyze_ticker(v) for v in US_STOCKS.values()]
    results.sort(key=lambda x: (x["score"] if x else 0), reverse=True)
    report = format_report(results, "🇺🇸 ABD Anlık Analiz")
    await msg.edit_text(report, parse_mode="HTML")


async def cmd_analiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Kullanım: /analiz THYAO veya /analiz AAPL")
        return
    sym_input = ctx.args[0].upper()
    # Determine ticker
    if sym_input in BIST_STOCKS:
        ticker = BIST_STOCKS[sym_input]
    elif sym_input in US_STOCKS:
        ticker = US_STOCKS[sym_input]
    else:
        ticker = sym_input + ".IS" if not sym_input.endswith(".IS") else sym_input

    msg = await update.message.reply_text(f"⏳ {sym_input} analiz ediliyor…")
    r = analyze_ticker(ticker)
    if not r:
        await msg.edit_text(f"❌ {sym_input} için veri alınamadı. Sembolü kontrol et.")
        return

    cur = "₺" if ticker.endswith(".IS") else "$"
    sym = sym_input.replace(".IS", "")
    macd_dir = "↑" if r["macd"] > r["macd_sig"] else "↓"
    text = (
        f"<b>🔍 {sym} Detaylı Analiz</b>\n"
        f"<i>{datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}</i>\n\n"
        f"💰 Fiyat: {cur}{r['price']:.2f} ({r['change']:+.2f}%)\n\n"
        f"📈 <b>Teknik Göstergeler</b>\n"
        f"  RSI (14): {r['rsi']:.1f}\n"
        f"  MACD: {macd_dir} ({'pozitif' if r['macd'] > r['macd_sig'] else 'negatif'} kesişim)\n"
        f"  Bollinger: {'Alt banda yakın 🟢' if r['bb_pct'] < 0.3 else 'Üst banda yakın 🔴' if r['bb_pct'] > 0.7 else 'Orta bölge ⚪'}\n"
        f"  Hacim: ×{r['vol_ratio']:.1f} (20g ort.)\n\n"
        f"💡 <b>Sebepler</b>\n  " + "\n  ".join(f"• {s}" for s in r["reasons"]) + "\n\n"
        f"<b>Sinyal: {r['signal']}</b>\n\n"
        f"⚠️ <i>Bilgi amaçlıdır, yatırım tavsiyesi değildir.</i>"
    )
    await msg.edit_text(text, parse_mode="HTML")


async def cmd_yardim(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>📋 Komut Listesi</b>\n\n"
        "/start — Ana menü\n"
        "/bist — Tüm BIST hisseleri analizi\n"
        "/us — Tüm ABD hisseleri analizi\n"
        "/analiz [SEMBOL] — Tek hisse detaylı analiz\n"
        "  Örnek: /analiz THYAO\n"
        "  Örnek: /analiz NVDA\n"
        "/yardim — Bu ekran\n\n"
        "<b>⏰ Otomatik Raporlar</b>\n"
        "🌅 09:30 — BIST sabah raporu\n"
        "🌍 16:15 — ABD seans öncesi raporu\n\n"
        "<b>📊 İzlenen BIST:</b> THYAO, EREGL, SASA, BIMAS, KCHOL, ASELS, GARAN, AKBNK, TUPRS, FROTO\n"
        "<b>🇺🇸 İzlenen ABD:</b> AAPL, NVDA, MSFT, AMZN, TSLA, META, GOOGL",
        parse_mode="HTML"
    )


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "bist":
        await q.message.reply_text("⏳ BIST analiz ediliyor…")
        results = [analyze_ticker(v) for v in BIST_STOCKS.values()]
        results.sort(key=lambda x: (x["score"] if x else 0), reverse=True)
        await q.message.reply_text(format_report(results, "📊 BIST Anlık Analiz"), parse_mode="HTML")
    elif q.data == "us":
        await q.message.reply_text("⏳ ABD analiz ediliyor…")
        results = [analyze_ticker(v) for v in US_STOCKS.values()]
        results.sort(key=lambda x: (x["score"] if x else 0), reverse=True)
        await q.message.reply_text(format_report(results, "🇺🇸 ABD Anlık Analiz"), parse_mode="HTML")
    elif q.data == "search_hint":
        await q.message.reply_text("Hisse analizi için: /analiz THYAO veya /analiz AAPL")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("bist",   cmd_bist))
    app.add_handler(CommandHandler("us",     cmd_us))
    app.add_handler(CommandHandler("analiz", cmd_analiz))
    app.add_handler(CommandHandler("yardim", cmd_yardim))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Scheduler
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(send_bist_report, "cron", hour=9,  minute=30, args=[app])
    scheduler.add_job(send_us_report,   "cron", hour=16, minute=15, args=[app])
    scheduler.start()

    log.info("MidasAI Signal bot starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
