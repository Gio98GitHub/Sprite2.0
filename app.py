import os
import psycopg2
import hashlib
import hmac
import json
from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, send_from_directory
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import asyncio
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

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
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT spiritello, variante FROM collezione WHERE user_id = %s", (user_id,))
        rows = c.fetchall()
        conn.close()
        return [{"spiritello": r[0], "variante": r[1]} for r in rows]
    except Exception as e:
        print("Errore get_collezione: " + str(e))
        return []

def verifica_init_data(init_data):
    try:
        if not init_data:
            return None
        parsed = dict(parse_qsl(init_data))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(k + "=" + v for k, v in sorted(parsed.items()))
        secret_key = hmac.new("WebAppData".encode(), BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None
        user_data = parsed.get("user")
        if not user_data:
            return {"id": 0, "username": "utente"}
        return json.loads(user_data)
    except Exception as e:
        print("Errore verifica: " + str(e))
        return {"id": 0, "username": "utente"}

def genera_immagine_collezione(user, collezione):
    larghezza = 700
    riga_altezza = 60
    header_altezza = 100
    altezza = header_altezza + riga_altezza * len(SPIRITELLI)

    img = Image.new("RGB", (larghezza, altezza), (10, 10, 40))
    draw = ImageDraw.Draw(img)

    try:
        font_titolo = ImageFont.truetype("static/font_titolo.ttf", 36)
        font_testo = ImageFont.truetype("static/font_testo.ttf", 20)
        font_piccolo = ImageFont.truetype("static/font_testo.ttf", 14)
    except Exception:
        font_titolo = ImageFont.load_default()
        font_testo = ImageFont.load_default()
        font_piccolo = ImageFont.load_default()

    nome = user.get("first_name", "Giocatore")
    draw.text((20, 15), "SpriteBot 2.0", font=font_titolo, fill=(255, 0, 255))
    draw.text((20, 60), "Collezione di " + nome, font=font_piccolo, fill=(0, 255, 255))

    col_larghezza = (larghezza - 150) // len(VARIANTI)
    for i, variante in enumerate(VARIANTI):
        x = 150 + i * col_larghezza
        draw.text((x + 10, header_altezza - 25), variante, font=font_piccolo, fill=(0, 255, 255))

    for riga, spiritello in enumerate(SPIRITELLI):
        y = header_altezza + riga * riga_altezza
        draw.text((15, y + 18), spiritello, font=font_testo, fill=(255, 0, 255))

        for i, variante in enumerate(VARIANTI):
            x = 150 + i * col_larghezza
            possiede = any(s["spiritello"] == spiritello and s["variante"] == variante for s in collezione)
            colore = (0, 220, 0) if possiede else (40, 40, 60)
            draw.rectangle([x + 10, y + 8, x + 10 + 44, y + 8 + 44], fill=colore, outline=(0, 255, 255))

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

async def invia_foto_collezione(chat_id, immagine):
    bot = Bot(token=BOT_TOKEN)
    try:
        await bot.send_photo(chat_id=chat_id, photo=immagine, caption="La mia collezione di Spiritelli!")
        return True
    except Exception as e:
        print("Errore invio foto: " + str(e))
        return False

async def rispondi_comando_start(chat_id, message_id):
    bot = Bot(token=BOT_TOKEN)
    bot_username = "sprite2bot"
    link_chat = "https://t.me/" + bot_username + "?start=open"
    testo_risposta = "SpriteBot 2.0\n\nApri la chat con il bot per gestire la tua collezione!"
    testo_bottone = "Apri Chat"
    tastiera = InlineKeyboardMarkup([[InlineKeyboardButton(testo_bottone, url=link_chat)]])
    try:
        await bot.send_message(chat_id=chat_id, text=testo_risposta, parse_mode="HTML", reply_to_message_id=message_id, reply_markup=tastiera)
    except Exception as e:
        print("Errore invio messaggio: " + str(e))

async def rispondi_comando_chat_privata(chat_id, message_id):
    bot = Bot(token=BOT_TOKEN)
    link_web_app = "https://sprite2-0.onrender.com/"
    testo_risposta = "SpriteBot 2.0\n\nGestisci la tua collezione di Spiritelli!"
    testo_bottone = "Apri App"
    from telegram import WebAppInfo
    webapp_info = WebAppInfo(url=link_web_app)
    tastiera = InlineKeyboardMarkup([[InlineKeyboardButton(testo_bottone, web_app=webapp_info)]])
    try:
        await bot.send_message(chat_id=chat_id, text=testo_risposta, parse_mode="HTML", reply_markup=tastiera)
    except Exception as e:
        print("Errore invio messaggio: " + str(e))

@app.route("/")
def home():
    return send_from_directory("static", "index.html")

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
    try:
        body = request.get_json()
        user = verifica_init_data(body.get("initData", ""))
        if not user or user.get("id") == 0:
            return jsonify({"error": "non autorizzato"}), 401
        collezione = get_collezione(user["id"])
        return jsonify({"collezione": collezione, "user": user})
    except Exception as e:
        print("Errore collezione: " + str(e))
        return jsonify({"error": str(e)}), 500

@app.route("/api/toggle", methods=["POST"])
@limiter.limit("20 per minute")
def api_toggle():
    try:
        body = request.get_json()
        user = verifica_init_data(body.get("initData", ""))
        if not user or user.get("id") == 0:
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
    except Exception as e:
        print("Errore toggle: " + str(e))
        return jsonify({"error": str(e)}), 500

@app.route("/api/condividi", methods=["POST"])
@limiter.limit("5 per minute")
def api_condividi():
    try:
        body = request.get_json()
        user = verifica_init_data(body.get("initData", ""))
        if not user or user.get("id") == 0:
            return jsonify({"error": "non autorizzato"}), 401

        collezione = get_collezione(user["id"])
        immagine = genera_immagine_collezione(user, collezione)
        ok = asyncio.run(invia_foto_collezione(user["id"], immagine))

        if not ok:
            return jsonify({"error": "invio fallito"}), 500
        return jsonify({"ok": True})
    except Exception as e:
        print("Errore condividi: " + str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
