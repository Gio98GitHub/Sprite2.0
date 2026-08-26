import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import psycopg2
import hashlib
import hmac
import json
from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, send_from_directory
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
import asyncio
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__, static_folder="static")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

limiter = Limiter(get_remote_address, app=app, default_limits=["60 per minute"], storage_uri="memory://")

VARIANTI = ["Base", "Oro", "Maestro dei Trucchi"]
SPIRITELLI = ["Sonic", "8-Bit", "Corona", "Cespuglio", "Klombo", "Tails", "Shadow", "Avventura", "Killswitch", "Jackrabbit", "Jonesy"]

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def aggiungi_spiritello(user_id, username, spiritello, variante):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO collezione (user_id, username, spiritello, variante) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, spiritello, variante) DO NOTHING", (user_id, username, spiritello, variante))
    conn.commit()
    inserted = c.rowcount > 0
    conn.close()
    return inserted

def elimina_spiritello(user_id, spiritello, variante):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM collezione WHERE user_id = %s AND spiritello = %s AND variante = %s", (user_id, spiritello, variante))
    conn.commit()
    deleted = c.rowcount > 0
    conn.close()
    return deleted

def get_collezione(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT spiritello, variante FROM collezione WHERE user_id = %s", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"spiritello": r[0], "variante": r[1]} for r in rows]

def verifica_init_data(init_data: str):
    try:
        parsed = dict(parse_qsl(init_data))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new("WebAppData".encode(), BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None
        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None

async def rispondi_comando_start(chat_id, message_id):
    bot = Bot(token=BOT_TOKEN)
    bot_username = "sprite2bot"
    link_chat = f"https://t.me/{bot_username}?start=open"
    testo_risposta = "✨ <b>SpriteBot 2.0</b> ✨\n\nApri la chat con il bot per gestire la tua collezione!"
    testo_bottone = "💬 Apri Chat"
    tastiera = InlineKeyboardMarkup([[InlineKeyboardButton(testo_bottone, url=link_chat)]])
    try:
        await bot.send_message(chat_id=chat_id, text=testo_risposta, parse_mode="HTML", reply_to_message_id=message_id, reply_markup=tastiera)
    except Exception as e:
        print(f"Errore invio messaggio: {e}")

async def rispondi_comando_chat_privata(chat_id, message_id):
    bot = Bot(token=BOT_TOKEN)
    link_web_app = f"https://{request.host}/"
    testo_risposta = "✨ <b>SpriteBot 2.0</b> ✨\n\nGestisci la tua collezione di Spiritelli!"
    testo_bottone = "🎮 Apri App"
    webapp_info = WebAppInfo(url=link_web_app)
    tastiera = InlineKeyboardMarkup([[InlineKeyboardButton(testo_bottone, web_app=webapp_info)]])
    try:
        await bot.send_message(chat_id=chat_id, text=testo_risposta, parse_mode="HTML", reply_markup=tastiera)
    except Exception as e:
        print(f"Errore invio messaggio: {e}")

@app.route("/")
def home():
    try:
        return send_from_directory("static", "index.html")
    except:
        return "<!DOCTYPE html><html><body><h1>Errore: file non trovato</h1></body></html>", 500

@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    if WEBHOOK_SECRET:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(header_secret, WEBHOOK_SECRET):
            return jsonify({"status": "forbidden"}), 403
    update = request.get_json()
    if update and "message" in update:
        message = update["message"]
        text = message.get("text", "")
        chat_id = message["chat"]["id"]
        message_id = message["message_id"]
        chat_type = message["chat"].get("type")
        
        if text.startswith("/start"):
            if chat_type == "group" or chat_type == "supergroup":
                asyncio.run(rispondi_comando_start(chat_id, message_id))
            elif chat_type == "private":
                asyncio.run(rispondi_comando_chat_privata(chat_id, message_id))
    return jsonify({"status": "ok"})

@app.route("/api/spiritelli")
def api_spiritelli():
    return jsonify({"spiritelli": SPIRITELLI, "varianti": VARIANTI})

@app.route("/api/collezione", methods=["POST"])
@limiter.limit("30 per minute")
def api_collezione():
    body = request.get_json()
    user = verifica_init_data(body.get("initData", ""))
    if not user:
        return jsonify({"error": "non autorizzato"}), 401
    collezione = get_collezione(user["id"])
    return jsonify({"collezione": collezione})

@app.route("/api/toggle", methods=["POST"])
@limiter.limit("20 per minute")
def api_toggle():
    body = request.get_json()
    user = verifica_init_data(body.get("initData", ""))
    if not user:
        return jsonify({"error": "non autorizzato"}), 401
    spiritello = body.get("spiritello")
    variante = body.get("variante")
    if spiritello not in SPIRITELLI or variante not in VARIANTI:
        return jsonify({"error": "dati non validi"}), 400
    username = user.get("username", "utente")
    collezione = get_collezione(user["id"])
    esiste = any(s["spiritello"] == spiritello and s["variante"] == variante for s in collezione)
    if esiste:
        ok = elimina_spiritello(user["id"], spiritello, variante)
        azione = "rimosso"
    else:
        ok = aggiungi_spiritello(user["id"], username, spiritello, variante)
        azione = "aggiunto"
    return jsonify({"ok": ok, "azione": azione})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
