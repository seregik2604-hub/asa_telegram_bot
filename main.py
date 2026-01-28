import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, request
import requests

BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

app = Flask(__name__)

def get_db():
    conn = sqlite3.connect('trades.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS trades
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT, direction TEXT, symbol TEXT,
                  timeframe TEXT, entry REAL, sl REAL, tp REAL,
                  rr REAL, h1poi TEXT, h4poi TEXT, result TEXT, pnl REAL)''')
    conn.commit()
    return conn

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
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
        data = request.get_json() or json.loads(request.data.decode('utf-8'))
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
            
            c.execute("INSERT INTO trades (timestamp,direction,symbol,timeframe,entry,sl,tp,rr,h1poi,h4poi) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (datetime.now().isoformat(), direction, symbol, tf, entry, sl, tp, rr, h1, h4))
            conn.commit()
            
            emoji = "🟢" if direction == "LONG" else "🔴"
            sl_pips = abs(entry - sl) * 10000
            tp_pips = abs(tp - entry) * 10000
            
            msg = f"{emoji} *{direction} SIGNAL*\n\n💱 *{symbol}* | {tf}\n━━━━━━━━━━━━\n💰 Entry: `{entry}`\n🛑 SL: `{sl}` ({sl_pips:.1f} pips)\n🎯 TP: `{tp}` ({tp_pips:.1f} pips)\n📊 RR: 1:{rr}"
            send_telegram(msg)
            
        elif alert_type == 'CLOSE':
            direction = data.get('d', '')
            symbol = data.get('s', '')
            result = data.get('r', '')
            pnl = float(data.get('pnl', 0))
            
            c.execute("UPDATE trades SET result=?, pnl=? WHERE symbol=? AND result IS NULL ORDER BY id DESC LIMIT 1", (result, pnl, symbol))
            conn.commit()
            
            emoji = "✅🎯" if result == 'TP' else "❌🛑" if result == 'SL' else "🔄"
            msg = f"{emoji} *CLOSED*\n\n💱 *{symbol}*\n📋 Result: *{result}*\n💵 PnL: *{pnl:+.1f} pips*"
            send_telegram(msg)
            
        elif alert_type == 'BE':
            symbol = data.get('s', '')
            direction = data.get('d', '')
            new_sl = data.get('sl', '')
            msg = f"🔄 *BREAKEVEN*\n\n💱 *{symbol}*\n📊 {direction}\n🛑 New SL: `{new_sl}`"
            send_telegram(msg)
        
        conn.close()
        return 'OK', 200
    except Exception as e:
        print(f"Error: {e}")
        return str(e), 500

@app.route('/bot', methods=['POST'])
def bot_handler():
    try:
        data = request.get_json()
        if 'message' in data:
            chat_id = data['message']['chat']['id']
            text = data['message'].get('text', '')
            
            if text == '/start':
                reply = "🤖 *ASA Trading Bot*\n\n/stats - Statistics\n/help - Help"
            elif text == '/stats':
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
                conn.close()
                wr = (tp/total*100) if total > 0 else 0
                reply = f"📊 *Stats*\n\nTrades: {total}\nWin Rate: {wr:.1f}%\nPnL: {pnl:+.1f} pips\n\n✅ TP: {tp}\n❌ SL: {sl}\n🔄 BE: {be}"
            else:
                reply = "Use /stats or /help"
            
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                         data={"chat_id": chat_id, "text": reply, "parse_mode": "Markdown"})
        return 'OK', 200
    except Exception as e:
        print(f"Bot error: {e}")
        return 'OK', 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
```

**4. Commit changes**

---

**Также измени Procfile обратно на:**
```
web: gunicorn main:app
