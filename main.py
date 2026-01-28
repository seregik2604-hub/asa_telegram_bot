import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, request
import requests

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

app = Flask(__name__)

def init_db():
    conn = sqlite3.connect('trades.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS trades
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  direction TEXT,
                  symbol TEXT,
                  timeframe TEXT,
                  entry REAL,
                  sl REAL,
                  tp REAL,
                  rr REAL,
                  h1poi TEXT,
                  h4poi TEXT,
                  result TEXT,
                  pnl REAL)''')
    conn.commit()
    conn.close()

init_db()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_stats():
    conn = sqlite3.connect('trades.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM trades WHERE result IS NOT NULL")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trades WHERE result = 'TP'")
    tp_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trades WHERE result = 'SL'")
    sl_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM trades WHERE result = 'BE'")
    be_count = c.fetchone()[0]
    c.execute("SELECT SUM(pnl) FROM trades WHERE pnl IS NOT NULL")
    total_pnl = c.fetchone()[0] or 0
    conn.close()
    winrate = (tp_count / total * 100) if total > 0 else 0
    return f"""
📊 *Trading Statistics*

Total Trades: {total}
Win Rate: {winrate:.1f}%
Total PnL: {total_pnl:+.1f} pips

✅ Take Profit: {tp_count}
❌ Stop Loss: {sl_count}
🔄 Breakeven: {be_count}
"""

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            raw = request.data.decode('utf-8')
            data = json.loads(raw)
        
        alert_type = data.get('t', '')
        conn = sqlite3.connect('trades.db')
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
            
            c.execute("""INSERT INTO trades 
                         (timestamp, direction, symbol, timeframe, entry, sl, tp, rr, h1poi, h4poi)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (datetime.now().isoformat(), direction, symbol, tf, entry, sl, tp, rr, h1, h4))
            conn.commit()
            
            emoji = "🟢" if direction == "LONG" else "🔴"
            sl_pips = abs(entry - sl) * 10000
            tp_pips = abs(tp - entry) * 10000
            
            message = f"""
{emoji} *{direction} SIGNAL*

💱 *{symbol}* | {tf}
━━━━━━━━━━━━━━━
💰 Entry: `{entry}`
🛑 SL: `{sl}` ({sl_pips:.1f} pips)
🎯 TP: `{tp}` ({tp_pips:.1f} pips)
📊 RR: 1:{rr}
━━━━━━━━━━━━━━━
📈 H1: {h1 if h1 else 'N/A'}
📈 H4: {h4 if h4 else 'N/A'}
"""
            send_telegram(message)
            
        elif alert_type == 'CLOSE':
            direction = data.get('d', '')
            symbol = data.get('s', '')
            result = data.get('r', '')
            pnl = float(data.get('pnl', 0))
            
            c.execute("""UPDATE trades SET result = ?, pnl = ?
                         WHERE symbol = ? AND result IS NULL
                         ORDER BY id DESC LIMIT 1""",
                      (result, pnl, symbol))
            conn.commit()
            
            if result == 'TP':
                emoji = "✅🎯"
                result_text = "TAKE PROFIT"
            elif result == 'SL':
                emoji = "❌🛑"
                result_text = "STOP LOSS"
            elif result == 'BE':
                emoji = "🔄"
                result_text = "BREAKEVEN"
            else:
                emoji = "📤"
                result_text = result
            
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            
            message = f"""
{emoji} *POSITION CLOSED*

💱 *{symbol}*
━━━━━━━━━━━━━━━
📋 Result: *{result_text}*
{pnl_emoji} PnL: *{pnl:+.1f} pips*
📊 Direction: {direction}
"""
            send_telegram(message)
            
        elif alert_type == 'BE':
            direction = data.get('d', '')
            symbol = data.get('s', '')
            new_sl = data.get('sl', '')
            
            message = f"""
🔄 *BREAKEVEN ACTIVATED*

💱 *{symbol}*
━━━━━━━━━━━━━━━
📊 Direction: {direction}
🛑 New SL: `{new_sl}`
✅ Position protected!
"""
            send_telegram(message)
        
        conn.close()
        return 'OK', 200
        
    except Exception as e:
        print(f"Error: {e}")
        return f'Error: {e}', 500

@app.route('/bot', methods=['POST'])
def bot_handler():
    try:
        data = request.get_json()
        if 'message' in data:
            chat_id = data['message']['chat']['id']
            text = data['message'].get('text', '')
            
            if text == '/start':
                reply = "🤖 *ASA Trading Bot*\n\nCommands:\n/stats - View statistics\n/help - Show help"
            elif text == '/stats':
                reply = get_stats()
            elif text == '/help':
                reply = "📚 *Help*\n\n/stats - Show trading statistics\n/help - Show this message"
            else:
                reply = "Unknown command. Use /help"
            
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": chat_id, "text": reply, "parse_mode": "Markdown"})
        return 'OK', 200
    except Exception as e:
        print(f"Bot error: {e}")
        return 'OK', 200

@app.route('/')
def home():
    return 'ASA Trading Bot is running!'

@app.route('/health')
def health():
    return 'OK'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
