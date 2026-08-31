import os
import psycopg2
import psycopg2.extensions
import psycopg2.pool
import hashlib
import hmac
import json
import base64
import time
import uuid
import logging
from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, send_from_directory
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
import asyncio
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ==================== CONFIGURAZIONE LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== INIZIALIZZAZIONE APP ====================
app = Flask(__name__, static_folder="static")
app.config['MAX_CONTENT_LENGTH'] = 6 * 1024 * 1024

# ==================== VARIABILI AMBIENTE ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", f"/webhook/{uuid.uuid4().hex}")

if not all([BOT_TOKEN, GROUP_CHAT_ID, DATABASE_URL, WEBHOOK_SECRET]):
    logger.error("Variabili d'ambiente mancanti!")
    raise ValueError("Mancano variabili d'ambiente critiche")

# ==================== SECURITY HEADERS ====================
@app.after_request
def set_security_headers(response):
    """Aggiungi security headers manualmente"""
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' cdnjs.cloudflare.com telegram.org; style-src 'self' 'unsafe-inline' fonts.googleapis.com; font-src 'self' fonts.gstatic.com; img-src 'self' data: https:"
    return response

# ==================== RATE LIMITING ====================
def get_user_id():
    """Estrae l'user_id dalla richiesta per rate limiting accurato"""
    try:
        body = request.get_json(force=False, silent=True) or {}
        init_data = body.get("initData", "")
        user = verifica_init_data(init_data)
        if user:
            return str(user.get("id", get_remote_address()))
    except:
        pass
    return get_remote_address()

limiter = Limiter(
    key_func=get_user_id,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# ==================== COSTANTI ====================
VARIANTI = ["Base", "Oro", "Maestro dei Trucchi"]
SPIRITELLI = ["Sonic", "8-Bit", "Corona", "Cespuglio", "Klombo", "Tails", "Shadow", "Avventura", "Killswitch", "Jackrabbit", "Jonesy", "Tempesta"]
INITDATA_EXPIRY = 3600

# ==================== DATABASE ====================
db_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=2,
    maxconn=15,
    dsn=DATABASE_URL,
    connect_timeout=5
)

def get_db_connection():
    """Preleva una connessione dal pool (thread-safe)"""
    try:
        return db_pool.getconn()
    except psycopg2.Error as e:
        logger.error(f"Errore ottenimento connessione dal pool: {str(e)}")
        return None

def release_db_connection(conn):
    """Rilascia la connessione nel pool"""
    if conn:
        try:
            db_pool.putconn(conn)
        except Exception as e:
            logger.error(f"Errore rilascio connessione al pool: {str(e)}")

def log_audit(user_id, action, details="", conn=None):
    """Registra le azioni dell'utente per audit"""
    conn_locale = conn is None
    try:
        if conn_locale:
            conn = get_db_connection()
            if not conn:
                logger.warning(f"Impossibile registrare audit: DB non disponibile")
                return
        
        c = conn.cursor()
        c.execute(
            "INSERT INTO audit_log (user_id, action, details, timestamp) VALUES (%s, %s, %s, NOW())",
            (user_id, action, details)
        )
        if conn_locale:
            conn.commit()
        c.close()
        if conn_locale:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Errore audit log: {str(e)}")

def upsert_utente(user_id, username, conn=None):
    """Inserisce l'utente se non esiste, altrimenti aggiorna lo username"""
    conn_locale = conn is None
    try:
        if conn_locale:
            conn = get_db_connection()
            if not conn:
                return False
        c = conn.cursor()
        try:
            c.execute(
                "INSERT INTO utenti (user_id, username) VALUES (%s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, aggiornato_il = NOW()",
                (user_id, username)
            )
            if conn_locale:
                conn.commit()
            return True
        except psycopg2.Error as e:
            if conn_locale:
                conn.rollback()
            logger.error(f"Errore upsert utente: {str(e)}")
            return False
        finally:
            c.close()
            if conn_locale:
                release_db_connection(conn)
    except Exception as e:
        logger.error(f"Errore DB upsert_utente: {str(e)}")
        return False

def get_collezione(user_id, conn=None):
    """Recupera la collezione dell'utente con stato mastered"""
    conn_locale = conn is None
    try:
        if conn_locale:
            conn = get_db_connection()
            if not conn:
                return []
        
        c = conn.cursor()
        c.execute("SELECT spiritello, variante, mastered FROM collezione WHERE user_id = %s", (user_id,))
        rows = c.fetchall()
        c.close()
        if conn_locale:
            release_db_connection(conn)
        return [{"spiritello": r[0], "variante": r[1], "mastered": r[2]} for r in rows]
    except Exception as e:
        logger.error(f"Errore get_collezione: {str(e)}")
        return []

def get_statistiche_utente(user_id):
    """Recupera statistiche dell'utente"""
    try:
        conn = get_db_connection()
        if not conn:
            return None
        
        c = conn.cursor()
        c.execute("""
            SELECT 
                COUNT(*) as posseduti,
                SUM(CASE WHEN mastered = true THEN 1 ELSE 0 END) as masterati
            FROM collezione 
            WHERE user_id = %s
        """, (user_id,))
        result = c.fetchone()
        c.close()
        release_db_connection(conn)
        
        posseduti = result[0] if result[0] else 0
        masterati = result[1] if result[1] else 0
        
        return {
            "posseduti": posseduti,
            "masterati": masterati,
            "totali": 36,
            "percentuale": round((posseduti / 36) * 100, 1)
        }
    except Exception as e:
        logger.error(f"Errore get_statistiche: {str(e)}")
        return None

def get_top_5_leaderboard():
    """Recupera top 5 utenti per spiritelli masterati"""
    try:
        conn = get_db_connection()
        if not conn:
            return []
        
        c = conn.cursor()
        c.execute("""
            SELECT 
                user_id,
                username,
                COUNT(*) as totali,
                SUM(CASE WHEN mastered = true THEN 1 ELSE 0 END) as masterati
            FROM collezione
            GROUP BY user_id, username
            ORDER BY masterati DESC
            LIMIT 5
        """)
        rows = c.fetchall()
        c.close()
        release_db_connection(conn)
        
        return [
            {
                "user_id": r[0],
                "username": r[1],
                "totali": r[2],
                "masterati": r[3]
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Errore get_top_5: {str(e)}")
        return []

# ==================== AUTENTICAZIONE ====================
def verifica_init_data(init_data):
    """Verifica e decodifica l'initData di Telegram"""
    try:
        if not init_data or not isinstance(init_data, str):
            return None
        
        parsed = dict(parse_qsl(init_data))
        received_hash = parsed.pop("hash", None)
        auth_date = parsed.get("auth_date")
        
        if not received_hash or not auth_date:
            logger.warning("Hash o auth_date mancanti in initData")
            return None
        
        try:
            auth_timestamp = int(auth_date)
            current_time = int(time.time())
            if current_time - auth_timestamp > INITDATA_EXPIRY:
                logger.info(f"InitData scaduto: {current_time - auth_timestamp}s fa")
                return None
        except ValueError:
            logger.warning("auth_date non e' un numero valido")
            return None
        
        data_check_string = "\n".join(k + "=" + v for k, v in sorted(parsed.items()))
        secret_key = hmac.new("WebAppData".encode(), BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(calculated_hash, received_hash):
            logger.warning("Firma HMAC non valida")
            return None
        
        user_data = parsed.get("user")
        if not user_data:
            logger.warning("Nessun dato utente in initData")
            return None
        
        try:
            user = json.loads(user_data)
            if not isinstance(user.get("id"), int) or user.get("id") <= 0:
                logger.warning(f"ID utente non valido: {user.get('id')}")
                return None
            return user
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Errore parsing JSON user data: {str(e)}")
            return None
            
    except Exception as e:
        logger.error(f"Errore verifica initData: {str(e)}")
        return None

def verifica_gruppo_autorizzato(chat_id):
    """Verifica che il comando sia eseguito nel gruppo autorizzato"""
    if str(chat_id) != str(GROUP_CHAT_ID):
        logger.info(f"Tentativo in gruppo non autorizzato: {chat_id}")
        return False
    return True

# ==================== BOT COMMANDS ====================
async def rispondi_comando_spritebot(chat_id, message_id, user_id, bot):
    """Risponde a /spritebot nel gruppo"""
    
    if not verifica_gruppo_autorizzato(chat_id):
        return
    
    bot_username = "sprite2bot"
    link_chat = f"https://t.me/{bot_username}?start=open"
    testo_risposta = "SpriteBot 2.0\n\nApri la chat con il bot per gestire la tua collezione!"
    testo_bottone = "Apri Chat"
    tastiera = InlineKeyboardMarkup([[InlineKeyboardButton(testo_bottone, url=link_chat)]])
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=testo_risposta,
            parse_mode="HTML",
            reply_to_message_id=message_id,
            reply_markup=tastiera
        )
        log_audit(user_id, "used_spritebot_command", f"chat_id={chat_id}")
        logger.info(f"/spritebot eseguito da user {user_id} nel gruppo {chat_id}")
    except Exception as e:
        logger.error(f"Errore invio messaggio: {str(e)}")

async def rispondi_comando_stats(chat_id, user_id, username, bot):
    """Risponde a /stats con le statistiche dell'utente"""
    
    stats = get_statistiche_utente(user_id)
    
    if not stats:
        try:
            await bot.send_message(chat_id=chat_id, text="❌ Errore nel caricamento delle statistiche")
        except Exception as e:
            logger.error(f"Errore invio stats: {str(e)}")
        return
    
    testo = f"""📊 **Statistiche di {username}:**

🎮 Spiritelli posseduti: **{stats['posseduti']}/{stats['totali']}**
⭐ Spiritelli masterati: **{stats['masterati']}/{stats['totali']}**
📈 Completamento: **{stats['percentuale']}%**
"""
    
    try:
        await bot.send_message(chat_id=chat_id, text=testo, parse_mode="Markdown")
        log_audit(user_id, "viewed_stats", "")
        logger.info(f"/stats visualizzato da user {user_id}")
    except Exception as e:
        logger.error(f"Errore invio stats: {str(e)}")

async def rispondi_comando_leaderboard(chat_id, bot):
    """Risponde a /leaderboard con top 5"""
    
    top_5 = get_top_5_leaderboard()
    
    if not top_5:
        try:
            await bot.send_message(chat_id=chat_id, text="❌ Nessun dato disponibile")
        except Exception as e:
            logger.error(f"Errore invio leaderboard: {str(e)}")
        return
    
    testo = "🏆 **Top 5 utenti con più spiritelli masterati:**\n\n"
    
    for i, user in enumerate(top_5, 1):
        testo += f"{i}. {user['username']} - ⭐ {user['masterati']}/36\n"
    
    try:
        await bot.send_message(chat_id=chat_id, text=testo, parse_mode="Markdown")
        logger.info(f"/leaderboard visualizzato nel chat {chat_id}")
    except Exception as e:
        logger.error(f"Errore invio leaderboard: {str(e)}")

async def rispondi_comando_mancanti(chat_id, user_id, username, bot):
    """Crea uno screenshot degli spiritelli mancanti con emoji"""
    
    collezione = get_collezione(user_id)
    posseduti = set((s["spiritello"], s["variante"]) for s in collezione)
    
    mancanti = []
    for spiritello in SPIRITELLI:
        for variante in VARIANTI:
            if (spiritello, variante) not in posseduti:
                mancanti.append((spiritello, variante))
    
    if not mancanti:
        try:
            await bot.send_message(chat_id=chat_id, text=f"✅ @{username} Non ti mancano spiritelli! Collezione completa! 🎉")
        except Exception as e:
            logger.error(f"Errore invio mancanti: {str(e)}")
        return
    
    try:
        # Crea messaggio di testo con emoji
        testo = f"🔍 **Spiritelli Mancanti di @{username}**\n\n"
        testo += f"📊 Mancanti: **{len(mancanti)}/36** spiritelli\n\n"
        
        current_spiritello = None
        for spiritello, variante in mancanti:
            if spiritello != current_spiritello:
                testo += f"\n🎮 **{spiritello}:**\n"
                current_spiritello = spiritello
            
            # Aggiungi emoji per variante
            if variante == "Base":
                emoji = "⚪"
            elif variante == "Oro":
                emoji = "🟡"
            else:  # Maestro dei Trucchi
                emoji = "⭐"
            
            testo += f"{emoji} {variante}\n"
        
        testo += f"\n_By Fortnite_Italia_Leaks on Telegram_"
        
        await bot.send_message(chat_id=chat_id, text=testo, parse_mode="Markdown")
        log_audit(user_id, "viewed_mancanti", f"count={len(mancanti)}")
        logger.info(f"/mancanti visualizzato da user {user_id}: {len(mancanti)} mancanti")
    except Exception as e:
        logger.error(f"Errore invio mancanti: {str(e)}")

# ==================== ROUTES ====================
@app.route("/")
def home():
    """Serve la home page"""
    try:
        return send_from_directory("static", "index.html")
    except Exception as e:
        logger.error(f"Errore caricamento index.html: {str(e)}")
        return jsonify({"error": "Errore interno del server"}), 500

@app.route("/static/<path:path>")
def static_files(path):
    """Serve file statici"""
    try:
        return send_from_directory("static", path)
    except Exception as e:
        logger.error(f"Errore caricamento file statico {path}: {str(e)}")
        return jsonify({"error": "File non trovato"}), 404

@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    """Webhook per ricevere aggiornamenti da Telegram"""
    try:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not header_secret:
            logger.warning("Richiesta webhook senza header secret")
            return jsonify({"status": "forbidden"}), 403
        
        if not hmac.compare_digest(header_secret, WEBHOOK_SECRET):
            logger.warning(f"Secret token non valido")
            return jsonify({"status": "forbidden"}), 403
        
        try:
            update = request.get_json(force=False, silent=False)
        except Exception as e:
            logger.error(f"JSON non valido nel webhook: {str(e)}")
            return jsonify({"status": "invalid_json"}), 400
        
        if not isinstance(update, dict):
            logger.warning("Update non e' un dizionario")
            return jsonify({"status": "invalid_format"}), 400
        
        logger.info(f"Webhook ricevuto - Update ID: {update.get('update_id', 'unknown')}")
        
        if update and "message" in update:
            message = update["message"]
            
            if not all(k in message for k in ["chat", "text"]):
                logger.warning(f"Campi obbligatori mancanti")
                return jsonify({"status": "ok"}), 200
            
            text = message.get("text", "")
            chat_id = message["chat"].get("id")
            message_id = message.get("message_id")
            user_id = message.get("from", {}).get("id")
            chat_type = message["chat"].get("type")
            
            bot = Bot(token=BOT_TOKEN)
            
            if text.startswith("/spritebot"):
                if chat_type == "group" or chat_type == "supergroup":
                    asyncio.run(rispondi_comando_spritebot(chat_id, message_id, user_id, bot))
            elif text.startswith("/stats"):
                username = message.get("from", {}).get("username", "Utente")
                asyncio.run(rispondi_comando_stats(chat_id, user_id, username, bot))
            elif text.startswith("/leaderboard"):
                asyncio.run(rispondi_comando_leaderboard(chat_id, bot))
            elif text.startswith("/mancanti"):
                username = message.get("from", {}).get("username", "Utente")
                asyncio.run(rispondi_comando_mancanti(chat_id, user_id, username, bot))
        
        return jsonify({"status": "ok"}), 200
    
    except Exception as e:
        logger.error(f"Errore webhook: {str(e)}")
        return jsonify({"status": "error"}), 500

@app.route("/api/spiritelli")
def api_spiritelli():
    """Ritorna lista di spiritelli e varianti"""
    try:
        return jsonify({"spiritelli": SPIRITELLI, "varianti": VARIANTI})
    except Exception as e:
        logger.error(f"Errore api_spiritelli: {str(e)}")
        return jsonify({"error": "Errore interno del server"}), 500

@app.route("/api/collezione", methods=["POST"])
@limiter.limit("30 per minute")
def api_collezione():
    """Ritorna la collezione dell'utente (richiede autenticazione)"""
    try:
        body = request.get_json()
        user = verifica_init_data(body.get("initData", ""))
        
        if not user:
            logger.warning("Tentativo accesso /api/collezione senza autenticazione")
            return jsonify({"error": "Autenticazione richiesta"}), 401
        
        if not isinstance(user.get("id"), int) or user.get("id") <= 0:
            logger.warning(f"ID utente non valido: {user.get('id')}")
            return jsonify({"error": "ID utente non valido"}), 401
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Errore database"}), 500
        try:
            collezione = get_collezione(user["id"], conn=conn)
            log_audit(user["id"], "fetch_collezione", "", conn=conn)
            conn.commit()
        finally:
            release_db_connection(conn)
        
        return jsonify({"collezione": collezione, "user": user})
    except Exception as e:
        logger.error(f"Errore collezione: {str(e)}")
        return jsonify({"error": "Errore interno del server"}), 500

@app.route("/api/toggle", methods=["POST"])
@limiter.limit("30 per minute")
def api_toggle():
    """Toggle uno spiritello nella collezione (richiede autenticazione)"""
    try:
        body = request.get_json()
        user = verifica_init_data(body.get("initData", ""))
        
        if not user:
            logger.warning("Tentativo toggle senza autenticazione")
            return jsonify({"error": "Autenticazione richiesta"}), 401
        
        if not isinstance(user.get("id"), int) or user.get("id") <= 0:
            return jsonify({"error": "ID utente non valido"}), 401
        
        spiritello = body.get("spiritello")
        variante = body.get("variante")
        is_mastered = body.get("mastered", False)
        
        if not isinstance(spiritello, str) or not isinstance(variante, str):
            logger.warning(f"Tipi non validi: spiritello={type(spiritello)}, variante={type(variante)}")
            return jsonify({"error": "Tipi non validi"}), 400
        
        if len(spiritello) > 50 or len(variante) > 50:
            logger.warning("Input troppo lungo")
            return jsonify({"error": "Input troppo lungo"}), 400
        
        if spiritello not in SPIRITELLI or variante not in VARIANTI:
            logger.warning(f"Input non autorizzato: spiritello={spiritello}, variante={variante}")
            return jsonify({"error": "Dati non validi"}), 400
        
        username = user.get("username", "utente")
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Errore database"}), 500
        
        try:
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_SERIALIZABLE)
            
            upsert_utente(user["id"], username, conn=conn)
            
            c = conn.cursor()
            
            c.execute(
                "SELECT id, mastered FROM collezione WHERE user_id = %s AND spiritello = %s AND variante = %s",
                (user["id"], spiritello, variante)
            )
            result = c.fetchone()
            esiste = result is not None
            
            if not esiste:
                c.execute(
                    "INSERT INTO collezione (user_id, spiritello, variante, mastered) VALUES (%s, %s, %s, %s)",
                    (user["id"], spiritello, variante, is_mastered)
                )
                azione = "aggiunto"
                nuovo_mastered = is_mastered
            else:
                if is_mastered:
                    c.execute(
                        "UPDATE collezione SET mastered = %s WHERE user_id = %s AND spiritello = %s AND variante = %s",
                        (True, user["id"], spiritello, variante)
                    )
                    azione = "mastered"
                    nuovo_mastered = True
                else:
                    c.execute(
                        "DELETE FROM collezione WHERE user_id = %s AND spiritello = %s AND variante = %s",
                        (user["id"], spiritello, variante)
                    )
                    azione = "rimosso"
                    nuovo_mastered = False
            
            log_audit(user["id"], "toggle_sprite", f"{spiritello}-{variante}-{azione}-mastered={is_mastered}", conn=conn)
            
            conn.commit()
            c.close()
            
            logger.info(f"Toggle spiritello: user={user['id']}, spiritello={spiritello}, azione={azione}, mastered={is_mastered}")
            
            return jsonify({"ok": True, "azione": azione, "mastered": nuovo_mastered})
        
        except psycopg2.Error as e:
            conn.rollback()
            logger.error(f"Errore transazione toggle: {str(e)}")
            return jsonify({"error": "Errore database"}), 500
        finally:
            release_db_connection(conn)
    
    except Exception as e:
        logger.error(f"Errore toggle: {str(e)}")
        return jsonify({"error": "Errore interno del server"}), 500

@app.route("/api/invia-screenshot", methods=["POST"])
@limiter.limit("3 per minute")
def invia_screenshot():
    """Riceve screenshot e lo invia al bot nella chat privata"""
    try:
        body = request.get_json()
        user = verifica_init_data(body.get("initData", ""))
        
        if not user:
            logger.warning("Tentativo invia_screenshot senza autenticazione")
            return jsonify({"error": "Autenticazione richiesta"}), 401
        
        if not isinstance(user.get("id"), int) or user.get("id") <= 0:
            return jsonify({"error": "ID utente non valido"}), 401
        
        image_data = body.get("screenshot")
        if not image_data:
            logger.warning("Nessuna immagine in invia_screenshot")
            return jsonify({"error": "Nessuna immagine"}), 400
        
        try:
            if "," in image_data:
                image_bytes = base64.b64decode(image_data.split(",")[1])
            else:
                image_bytes = base64.b64decode(image_data)
        except Exception as e:
            logger.error(f"Errore decodifica base64: {str(e)}")
            return jsonify({"error": "Immagine non valida"}), 400
        
        if len(image_bytes) > 5 * 1024 * 1024:
            logger.warning(f"Immagine troppo grande: {len(image_bytes)} bytes")
            return jsonify({"error": "Immagine troppo grande (max 5MB)"}), 400
        
        bot = Bot(token=BOT_TOKEN)
        try:
            asyncio.run(bot.send_photo(
                chat_id=user["id"],
                photo=image_bytes,
                caption="Ecco la mia collezione di Spiritelli! By Fortnite_Italia_Leaks"
            ))
            log_audit(user["id"], "create_screenshot", "")
            logger.info(f"Screenshot inviato all'utente {user['id']}")
            return jsonify({"ok": True, "message": "Screenshot inviato!"})
        except Exception as e:
            logger.error(f"Errore invio immagine al bot: {str(e)}")
            return jsonify({"error": "Errore invio immagine"}), 500
    
    except Exception as e:
        logger.error(f"Errore invia_screenshot: {str(e)}")
        return jsonify({"error": "Errore interno del server"}), 500

# ==================== ERROR HANDLERS ====================
@app.errorhandler(400)
def bad_request(error):
    """Gestisce errori 400"""
    logger.warning(f"Bad request: {str(error)}")
    return jsonify({"error": "Richiesta non valida"}), 400

@app.errorhandler(404)
def not_found(error):
    """Gestisce errori 404"""
    logger.info(f"Risorsa non trovata: {request.path}")
    return jsonify({"error": "Risorsa non trovata"}), 404

@app.errorhandler(413)
def request_too_large(error):
    """Gestisce richieste troppo grandi"""
    logger.warning(f"Richiesta troppo grande da {get_remote_address()}")
    return jsonify({"error": "Richiesta troppo grande"}), 413

@app.errorhandler(429)
def ratelimit_handler(e):
    """Gestisce errori di rate limiting"""
    logger.warning(f"Rate limit superato da {get_remote_address()}")
    return jsonify({"error": "Troppe richieste. Riprova piu' tardi."}), 429

@app.errorhandler(500)
def internal_error(error):
    """Gestisce errori 500 generici"""
    logger.error(f"Errore interno non gestito: {str(error)}", exc_info=True)
    return jsonify({"error": "Errore interno del server"}), 500

# ==================== MAIN ====================
if __name__ == "__main__":
    logger.info("SpriteBot 2.0 avviato")
    logger.info(f"Webhook path: {WEBHOOK_PATH}")
    logger.info(f"Gruppo autorizzato: {GROUP_CHAT_ID}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
