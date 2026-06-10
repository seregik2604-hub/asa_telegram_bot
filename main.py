import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, request
import requests

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
# Сигналы с RR ниже этого порога отклоняются и не считаются торговыми
MIN_RR = float(os.environ.get('MIN_RR', '2.0'))
# На Heroku/Render файловая система эфемерная: задайте DB_PATH на
# примонтированный диск (например /data/trades.db), иначе журнал
# сделок будет стираться при каждом деплое/рестарте
DB_PATH = os.environ.get('DB_PATH', 'trades.db')

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS trades
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT, direction TEXT, symbol TEXT,
                  timeframe TEXT, entry REAL, sl REAL, tp REAL,
                  rr REAL, h1poi TEXT, h4poi TEXT, session TEXT,
                  status TEXT DEFAULT 'OPEN',
                  result TEXT, pnl REAL)''')
    conn.commit()
    return conn


def pip_size(symbol):
    s = (symbol or '').upper()
    if 'JPY' in s:
        return 0.01
    if 'XAU' in s or 'GOLD' in s:
        return 0.1
    if 'XAG' in s or 'SILVER' in s:
        return 0.01
    if 'BTC' in s:
        return 1.0
    if 'ETH' in s:
        return 0.1
    return 0.0001


def trading_session(dt):
    h = dt.hour  # UTC
    if 7 <= h < 12:
        return 'London'
    if 12 <= h < 16:
        return 'NY-Overlap'
    if 16 <= h < 21:
        return 'NY'
    return 'Asia'


def send_telegram(text, chat_id=None):
    if not BOT_TOKEN or not (chat_id or CHAT_ID):
        print("Missing BOT_TOKEN or CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id or CHAT_ID,
                                 "text": text, "parse_mode": "Markdown"},
                      timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")


@app.route('/')
def home():
    return 'ASA Trading Bot is running!'


@app.route('/health')
def health():
    return 'OK'


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True) or json.loads(request.data.decode('utf-8'))
        alert_type = data.get('t', '')
        conn = get_db()
        c = conn.cursor()

        if alert_type == 'ENTRY':
            direction = data.get('d', '')
            symbol = data.get('s', '')
            tf = data.get('tf', '')
            entry = float(data.get('e', 0))
            sl = float(data.get('sl', 0))
            tp = float(data.get('tp', 0))
            rr = float(data.get('rr', 0))
            h1 = data.get('h1', '')
            h4 = data.get('h4', '')
            now = datetime.utcnow()
            session = trading_session(now)

            # Пересчитываем RR сами — не доверяем значению из алерта
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            real_rr = round(reward / risk, 2) if risk > 0 else 0.0

            if real_rr < MIN_RR:
                c.execute("""INSERT INTO trades
                    (timestamp,direction,symbol,timeframe,entry,sl,tp,rr,h1poi,h4poi,session,status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,'FILTERED')""",
                          (now.isoformat(), direction, symbol, tf, entry, sl, tp,
                           real_rr, h1, h4, session))
                conn.commit()
                send_telegram(
                    f"⚠️ *SIGNAL FILTERED*\n\n💱 {symbol} {direction} | {tf}\n"
                    f"📊 RR 1:{real_rr} < минимум 1:{MIN_RR}\n"
                    f"Сделка пропущена — не входить.")
                conn.close()
                return 'FILTERED', 200

            c.execute("""INSERT INTO trades
                (timestamp,direction,symbol,timeframe,entry,sl,tp,rr,h1poi,h4poi,session,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'OPEN')""",
                      (now.isoformat(), direction, symbol, tf, entry, sl, tp,
                       real_rr, h1, h4, session))
            conn.commit()

            emoji = "🟢" if direction == "LONG" else "🔴"
            ps = pip_size(symbol)
            sl_pips = abs(entry - sl) / ps
            tp_pips = abs(tp - entry) / ps

            msg = (f"{emoji} *{direction} SIGNAL*\n\n"
                   f"💱 *{symbol}* | {tf} | {session}\n"
                   f"━━━━━━━━━━━━\n"
                   f"💰 Entry: `{entry}`\n"
                   f"🛑 SL: `{sl}` ({sl_pips:.1f} pips)\n"
                   f"🎯 TP: `{tp}` ({tp_pips:.1f} pips)\n"
                   f"📊 RR: 1:{real_rr}")
            send_telegram(msg)

        elif alert_type == 'CLOSE':
            direction = data.get('d', '')
            symbol = data.get('s', '')
            result = data.get('r', '')
            pnl = float(data.get('pnl', 0))

            # Подзапрос вместо UPDATE..ORDER BY..LIMIT: тот синтаксис
            # работает не во всех сборках SQLite и молча ронял запись
            # результатов — из-за этого статистика винрейта не велась
            c.execute("""UPDATE trades SET result=?, pnl=?, status='CLOSED'
                         WHERE id = (SELECT id FROM trades
                                     WHERE symbol=? AND (direction=? OR ?='')
                                       AND status='OPEN'
                                     ORDER BY id DESC LIMIT 1)""",
                      (result, pnl, symbol, direction, direction))
            conn.commit()

            emoji = "✅🎯" if result == 'TP' else "❌🛑" if result == 'SL' else "🔄"
            msg = (f"{emoji} *CLOSED*\n\n💱 *{symbol}*\n"
                   f"📋 Result: *{result}*\n💵 PnL: *{pnl:+.1f} pips*")
            send_telegram(msg)

        elif alert_type == 'BE':
            symbol = data.get('s', '')
            direction = data.get('d', '')
            new_sl = data.get('sl', '')
            msg = (f"🔄 *BREAKEVEN*\n\n💱 *{symbol}*\n"
                   f"📊 {direction}\n🛑 New SL: `{new_sl}`")
            send_telegram(msg)

        conn.close()
        return 'OK', 200
    except Exception as e:
        print(f"Error: {e}")
        return str(e), 500


def build_stats():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM trades WHERE result IS NOT NULL")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trades WHERE result='TP'")
    tp = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trades WHERE result='SL'")
    sl = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trades WHERE result='BE'")
    be = c.fetchone()[0]
    c.execute("SELECT SUM(pnl) FROM trades WHERE pnl IS NOT NULL")
    pnl = c.fetchone()[0] or 0
    c.execute("SELECT SUM(pnl) FROM trades WHERE pnl > 0")
    gross_win = c.fetchone()[0] or 0
    c.execute("SELECT SUM(pnl) FROM trades WHERE pnl < 0")
    gross_loss = abs(c.fetchone()[0] or 0)
    c.execute("SELECT AVG(rr) FROM trades WHERE status != 'FILTERED'")
    avg_rr = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM trades WHERE status='FILTERED'")
    filtered = c.fetchone()[0]
    conn.close()

    decisive = tp + sl  # BE не считаем ни выигрышем, ни проигрышем
    wr = (tp / decisive * 100) if decisive > 0 else 0
    pf = (gross_win / gross_loss) if gross_loss > 0 else float('inf') if gross_win > 0 else 0
    expectancy = (pnl / total) if total > 0 else 0
    # Минимальный винрейт для безубытка при данном среднем RR
    be_wr = (100 / (1 + avg_rr)) if avg_rr > 0 else 0

    reply = (f"📊 *Stats*\n\n"
             f"Trades: {total}\n"
             f"Win Rate: {wr:.1f}% (нужно >{be_wr:.0f}% при RR 1:{avg_rr:.1f})\n"
             f"PnL: {pnl:+.1f} pips\n"
             f"Profit Factor: {pf:.2f}\n"
             f"Expectancy: {expectancy:+.1f} pips/trade\n\n"
             f"✅ TP: {tp}\n❌ SL: {sl}\n🔄 BE: {be}\n"
             f"⚠️ Filtered (RR<{MIN_RR}): {filtered}")
    return reply


def build_breakdown():
    conn = get_db()
    c = conn.cursor()
    lines = ["📈 *Breakdown*\n"]
    for col, title in (('symbol', 'By Symbol'), ('session', 'By Session'),
                       ('h4poi', 'By H4 POI'), ('h1poi', 'By H1 POI')):
        c.execute(f"""SELECT {col},
                             SUM(CASE WHEN result='TP' THEN 1 ELSE 0 END),
                             SUM(CASE WHEN result='SL' THEN 1 ELSE 0 END),
                             COALESCE(SUM(pnl),0)
                      FROM trades WHERE result IS NOT NULL AND {col} != ''
                      GROUP BY {col} ORDER BY 4 DESC""")
        rows = c.fetchall()
        if rows:
            lines.append(f"\n*{title}:*")
            for name, w, l, p in rows:
                d = w + l
                wr = (w / d * 100) if d > 0 else 0
                lines.append(f"  {name}: {wr:.0f}% WR, {p:+.1f} pips ({w}W/{l}L)")
    conn.close()
    return "\n".join(lines)


@app.route('/bot', methods=['POST'])
def bot_handler():
    try:
        data = request.get_json()
        if 'message' in data:
            chat_id = data['message']['chat']['id']
            text = data['message'].get('text', '')

            if text == '/start':
                reply = ("🤖 *ASA Trading Bot*\n\n"
                         "/stats - Statistics\n"
                         "/breakdown - Stats by symbol/session/POI\n"
                         "/help - Help")
            elif text == '/stats':
                reply = build_stats()
            elif text == '/breakdown':
                reply = build_breakdown()
            elif text == '/help':
                reply = ("/stats - общая статистика\n"
                         "/breakdown - разбивка по парам, сессиям и POI\n"
                         f"Фильтр RR: сигналы ниже 1:{MIN_RR} отклоняются")
            else:
                reply = "Use /stats, /breakdown or /help"

            send_telegram(reply, chat_id=chat_id)
        return 'OK', 200
    except Exception as e:
        print(f"Bot error: {e}")
        return 'OK', 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
