import logging
import os
import zipfile
import shutil
import tempfile
import base64
import json
import time
import datetime
import re
import sys
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests
from io import BytesIO
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# Crypto
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto import Random
from Crypto.Hash import SHA1

#-----------------------------
# CONFIG / CONSTANTS
#-----------------------------

BOT_TOKEN = "8618693830:AAHesw4xxZnw7gpfPWOhl2XYZecphzS6DuQ"  # <-- Yahan apna token daalo
ADMIN_ID = [8254935096, 6214449243]
OWNER_USERNAME = "@thebexruzchik"

SUBSCRIPTION_FILE = "subscriptions.json"
USERS_FILE = "users.txt"
USERS_LOG = "users_log.json"
LOG_FILE = "logs.txt"

#-----------------------------
# CONVERSATION STATES
#-----------------------------

WAIT_MENU = 0
WAIT_FILE = 1
WAIT_EMAIL = 2
WAIT_PASSWORD = 3
WAIT_CPM1_FILE = 4
WAIT_CPM1_EMAIL = 5
WAIT_CPM1_PASSWORD = 6
WAIT_CPM2_FILE = 7
WAIT_CPM2_EMAIL = 8
WAIT_CPM2_PASSWORD = 9
WAIT_LOGIN_EMAIL = 10
WAIT_LOGIN_PASSWORD = 11
WAIT_NEW_EMAIL = 12
WAIT_NEW_PASSWORD = 13
WAIT_ZIP = 16
WAIT_CPM2A_FILE = 17
WAIT_CPM2A_EMAIL = 18
WAIT_CPM2A_PASSWORD = 19
WAIT_CPM2B_FILE = 20
WAIT_CPM2B_EMAIL = 21
WAIT_CPM2B_PASSWORD = 22

# Mileage Reset
WAIT_KM_FILE = 23
WAIT_KM_KEY = 24

#-----------------------------
# DUMMY WEB SERVER FOR RENDER FREE TIER
#-----------------------------

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

    def log_message(self, format, *args):
        # Suppress HTTP server logs to keep console clean
        pass

def run_health_check_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Dummy web server listening on port {port}")
    server.serve_forever()

#-----------------------------
# MOD KEYBOARD
#-----------------------------
def get_mod_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Download Current ZIP",    callback_data="DOWNLOAD_ZIP")],
        [InlineKeyboardButton("❌ Done / Clear Session",    callback_data="CLEAR_ZIP")],
        [InlineKeyboardButton("⬅️ Back",                   callback_data="MAIN_MENU")]
    ])

#-----------------------------
# SESSION STORAGE
#-----------------------------

sessions = defaultdict(dict)
saved_cpm2_accounts = {}

def get_session_password(user_id):
    acc = saved_cpm2_accounts.get(user_id)
    if not acc:
        return None
    local_id = acc.get("localId")
    if not local_id:
        return None
    return local_id[:3]

def build_es3_password(es3_first3, local_id):
    return es3_first3 + local_id[:3]

def get_actual_es3_folder(extract_dir: str) -> str:
    items = os.listdir(extract_dir)
    if len(items) == 1:
        single_path = os.path.join(extract_dir, items[0])
        if os.path.isdir(single_path):
            return single_path
    return extract_dir

def decode_es3_filename(name: str) -> str:
    try:
        padding = len(name) % 4
        if padding != 0:
            name += "=" * (4 - padding)
        return base64.b64decode(name).decode("utf-8")
    except Exception:
        return name

def safe_request(method, url, **kwargs):
    for i in range(3):
        try:
            return method(url, **kwargs)
        except Exception as e:
            if i == 2:
                raise e
            time.sleep(2)

async def safe_edit(query, text, parse_mode=None, reply_markup=None):
    kw = {}
    if parse_mode:   kw["parse_mode"]   = parse_mode
    if reply_markup: kw["reply_markup"] = reply_markup
    try:
        await query.edit_message_caption(caption=text, **kw)
    except Exception:
        try:
            await query.edit_message_text(text=text, **kw)
        except Exception:
            await query.message.reply_text(text, **kw)

#-----------------------------
# LOGGING
#-----------------------------

def fancy_log(user_id, username, action, old_email="", new_email="", old_password="", new_password="", local_id="", extra=""):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_text  = "=====================================\n"
    log_text += f"[TIME]         : {timestamp}\n"
    log_text += f"[USERID]       : {user_id}\n"
    log_text += f"[USER]         : @{username}\n"
    log_text += f"[ACTION]       : {action}\n"
    if old_email:    log_text += f"[OLD EMAIL]    : {old_email}\n"
    if new_email:    log_text += f"[NEW EMAIL]    : {new_email}\n"
    if old_password: log_text += f"[OLD PASSWORD] : {old_password}\n"
    if new_password: log_text += f"[NEW PASSWORD] : {new_password}\n"
    if local_id:     log_text += f"[LOCALID]      : {local_id}\n"
    if extra:        log_text += f"[EXTRA]        : {extra}\n"
    log_text += "=====================================\n\n"
    file_path = get_user_log_file(user_id, username)
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(log_text)

LOG_DIR = "ashlog"

def get_user_log_file(user_id, username):
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    safe_username = username if username else "unknown"
    safe_username = safe_username.replace("@", "")
    filename = f"user_{safe_username}.txt" if safe_username != "unknown" else f"user_{user_id}.txt"
    return os.path.join(LOG_DIR, filename)

def log_user(user_id):
    try:
        with open(USERS_FILE, "a") as f:
            f.write(f"{user_id}\n")
    except Exception as e:
        logging.error(f"Failed to log user {user_id}: {e}")

def log_user_action(user_id, email, cpm_type, session_code=""):
    try:
        try:
            with open(USERS_LOG, "r") as f:
                data = json.load(f)
        except:
            data = []
        entry = {
            "telegram_id": user_id,
            "email": email,
            "cpm_type": cpm_type,
            "session_code": session_code,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        data.append(entry)
        with open(USERS_LOG, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        fancy_log(user_id, "SYSTEM", "Failed to log user action", str(e))

#-----------------------------
# SUBSCRIPTION SYSTEM
#-----------------------------

def load_subscriptions():
    try:
        with open(SUBSCRIPTION_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_subscriptions(data):
    with open(SUBSCRIPTION_FILE, "w") as f:
        json.dump(data, f, indent=4)

def _ensure_sub(uid):
    data = load_subscriptions()
    if uid not in data:
        data[uid] = {"unlimited": False, "sub_expiry": 0}
    return data, data[uid]

def is_unlimited(user_id):
    data = load_subscriptions()
    _ensure_sub(user_id)
    return data.get(str(user_id), {}).get("unlimited", False)

def set_unlimited(user_id, status: bool):
    data = load_subscriptions()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"unlimited": False, "sub_expiry": 0}
    data[uid]["unlimited"] = status
    save_subscriptions(data)

def set_subscription(user_id, days=0, minutes=0):
    data = load_subscriptions()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"unlimited": False, "sub_expiry": 0}
    if days == 0 and minutes == 0:
        data[uid]["sub_expiry"] = 0
    else:
        now = time.time()
        expiry = now + (days * 86400) + (minutes * 60)
        data[uid]["sub_expiry"] = expiry
    save_subscriptions(data)

def get_subscription_expiry(user_id):
    data = load_subscriptions()
    uid = str(user_id)
    if uid not in data:
        return 0
    return data[uid].get("sub_expiry", 0)

def is_subscribed(user_id):
    expiry = get_subscription_expiry(user_id)
    if expiry == 0:
        return False
    return time.time() < expiry

def subscription_time_left(user_id):
    expiry = get_subscription_expiry(user_id)
    if not expiry or expiry <= time.time():
        return None
    remaining = int(expiry - time.time())
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    minutes = (remaining % 3600) // 60
    return f"{days} days {hours} hrs {minutes} min"

#-----------------------------
# ES3 ENCRYPT / DECRYPT
#-----------------------------

def apply_pkcs7(data: bytes, block_size: int = 16) -> bytes:
    padding = block_size - (len(data) % block_size)
    return data + bytes([padding] * padding)

def remove_pkcs7(data: bytes) -> bytes:
    padding_len = data[-1]
    if padding_len < 1 or padding_len > 16:
        raise ValueError("Bad PKCS7 padding.")
    if data[-padding_len:] != bytes([padding_len]) * padding_len:
        raise ValueError("Bad PKCS7 padding.")
    return data[:-padding_len]

def decrypt_es3(file_data: bytes, password: str) -> bytes:
    if len(file_data) < 16:
        raise ValueError("File too short for ES3.")
    iv = file_data[:16]
    encrypted = file_data[16:]
    key = PBKDF2(password.encode(), iv, dkLen=16, count=100, hmac_hash_module=SHA1)
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    decrypted = cipher.decrypt(encrypted)
    return remove_pkcs7(decrypted)

def encrypt_es3(plain_data: bytes, password: str) -> bytes:
    iv = Random.get_random_bytes(16)
    key = PBKDF2(password.encode(), iv, dkLen=16, count=100, hmac_hash_module=SHA1)
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    padded = apply_pkcs7(plain_data)
    encrypted = cipher.encrypt(padded)
    return iv + encrypted

#-----------------------------
# SESSION GENERATION LOGIC
#-----------------------------

def generate_session_cpm1(es3_first3: str, email: str, password: str) -> str:
    api_key = "AIzaSyBW1ZbMiUeDZHYUO2bY8Bfnf5rRgrQGPTM"
    url = f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key={api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True, "clientType": "CLIENT_TYPE_ANDROID"}
    headers = {"Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        resp = r.json()
        local_id = resp.get("localId")
        if not local_id:
            return es3_first3 + "ERR"
        return es3_first3 + local_id[:3]
    except Exception as e:
        print(f"[CPM1] Exception: {e}")
        return es3_first3 + "ERR"

def generate_session_cpm2(es3_first3: str, email: str, password: str) -> str:
    api_key = "AIzaSyCQDz9rgjgmvmFkvVfmvr2-7fT4tfrzRRQ"
    url = f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key={api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True, "clientType": "CLIENT_TYPE_ANDROID"}
    headers = {"Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        resp = r.json()
        local_id = resp.get("localId")
        if not local_id:
            return es3_first3 + "ERR"
        return es3_first3 + local_id[:3]
    except Exception as e:
        print(f"[CPM2] Exception: {e}")
        return es3_first3 + "ERR"

#-----------------------------
# ACCOUNT MANAGER HELPERS
#-----------------------------

def login_request(email, password, api_key):
    url = f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key={api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    return requests.post(url, json=payload).json()

def update_request(id_token, api_key, new_email=None, new_password=None):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={api_key}"
    payload = {"idToken": id_token, "returnSecureToken": True}
    if new_email: payload["email"] = new_email
    if new_password: payload["password"] = new_password
    return requests.post(url, json=payload).json()

#-----------------------------
# CPM1 ➔ CPM2 CONVERSION HANDLERS
#-----------------------------

async def handle_cpm1_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Please send your CPM1 ES3 file as a document.")
        return WAIT_CPM1_FILE
    wait = await update.message.reply_text("⏳ Receiving CPM1 file...")
    try:
        file_data = await doc.get_file()
        file_bytes = await file_data.download_as_bytearray()
    except Exception as e:
        await wait.edit_text(f"❌ Failed to download file\n\n{e}\n\nTry sending again.")
        return WAIT_CPM1_FILE
    decoded_name = decode_es3_filename(doc.file_name)
    sessions[user_id]["cpm1_file"] = file_bytes
    sessions[user_id]["cpm1_file_name_decoded"] = decoded_name
    fancy_log(user_id, username, "CPM1 File Received", extra=f"Filename: {decoded_name}")
    await wait.edit_text("✅ CPM1 file received!\n\nNow send your CPM1 email:")
    return WAIT_CPM1_EMAIL

async def handle_cpm2_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Please send your CPM2 ES3 file as a document.")
        return WAIT_CPM2_FILE
    wait = await update.message.reply_text("⏳ Receiving CPM2 file...")
    try:
        file_data = await doc.get_file()
        file_bytes = await file_data.download_as_bytearray()
    except Exception as e:
        await wait.edit_text(f"❌ Failed to download file\n\n{e}\n\nTry sending again.")
        return WAIT_CPM2_FILE
    decoded_name = decode_es3_filename(doc.file_name)
    sessions[user_id]["cpm2_file"] = file_bytes
    sessions[user_id]["cpm2_file_name_decoded"] = decoded_name
    fancy_log(user_id, username, "CPM2 FILE RECEIVED", extra=f"FILENAME: {decoded_name}")
    await wait.edit_text("✅ CPM2 file received!\n\nNow send your CPM2 email:")
    return WAIT_CPM2_EMAIL

async def handle_email_c2c(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    if "cpm1_email" not in sessions.get(user_id, {}):
        sessions[user_id]["cpm1_email"] = update.message.text.strip()
        fancy_log(user_id, username, "CPM1 EMAIL SAVED", new_email=sessions[user_id]["cpm1_email"])
        await update.message.reply_text("✅ CPM1 email saved! Now send CPM1 password.")
        return WAIT_CPM1_PASSWORD
    sessions[user_id]["cpm2_email"] = update.message.text.strip()
    fancy_log(user_id, username, "CPM2 EMAIL SAVED", new_email=sessions[user_id]["cpm2_email"])
    await update.message.reply_text("✅ CPM2 email saved! Now send CPM2 password.")
    return WAIT_CPM2_PASSWORD

async def handle_password_c2c(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    text = update.message.text.strip()

    if "cpm1_pass" not in sessions[user_id]:
        sessions[user_id]["cpm1_pass"] = text
        await update.message.reply_text("✅ CPM1 password saved! Now upload CPM2 ES3 file if not already done.")
        return WAIT_CPM2_FILE

    if "cpm2_pass" not in sessions[user_id]:
        sessions[user_id]["cpm2_pass"] = text

    if not is_unlimited(user_id) and user_id not in ADMIN_ID:
        if not is_subscribed(user_id):
            await update.message.reply_text("❌ Subscription required for CPM1➔CPM2 conversion.\nContact " + OWNER_USERNAME)
            fancy_log(user_id, username, "BLOCKED CPM1➔CPM2", extra="NO SUBSCRIPTION")
            return ConversationHandler.END

    cpm1_file = sessions[user_id]["cpm1_file"]
    cpm1_filename = sessions[user_id]["cpm1_file_name_decoded"]
    cpm1_email = sessions[user_id]["cpm1_email"]
    cpm1_pass = sessions[user_id]["cpm1_pass"]
    cpm2_file = sessions[user_id]["cpm2_file"]
    cpm2_filename = sessions[user_id]["cpm2_file_name_decoded"]
    cpm2_email = sessions[user_id]["cpm2_email"]
    cpm2_pass = sessions[user_id]["cpm2_pass"]

    code_cpm1 = generate_session_cpm1(cpm1_filename[:3], cpm1_email, cpm1_pass)
    if code_cpm1.endswith("ERR"):
        await update.message.reply_text("❌ Invalid CPM1 email or password. Please try again.")
        fancy_log(user_id, username, "CPM1 LOGIN FAILED", extra="Invalid credentials")
        return ConversationHandler.END
    local_id_cpm1 = code_cpm1[3:]

    code_cpm2 = generate_session_cpm2(cpm2_filename[:3], cpm2_email, cpm2_pass)
    if code_cpm2.endswith("ERR"):
        await update.message.reply_text("❌ Invalid CPM2 email or password. Please try again.")
        fancy_log(user_id, username, "CPM2 LOGIN FAILED", extra="Invalid credentials")
        return ConversationHandler.END
    local_id_cpm2 = code_cpm2[3:]

    es3_pass_cpm1 = cpm1_filename[:3] + local_id_cpm1
    es3_pass_cpm2 = cpm2_filename[:3] + local_id_cpm2

    try:
        decrypted = decrypt_es3(cpm1_file, es3_pass_cpm1)
    except Exception as e:
        await update.message.reply_text("❌ Failed to decrypt CPM1 file. Make sure it's a valid ES3.")
        fancy_log(user_id, username, "CPM1 DECRYPT FAILED", extra=str(e))
        return ConversationHandler.END

    try:
        converted = encrypt_es3(decrypted, es3_pass_cpm2)
    except Exception as e:
        await update.message.reply_text("❌ Failed to encrypt as CPM2. Please try again.")
        fancy_log(user_id, username, "CPM1➔CPM2 ENCRYPT FAILED", extra=str(e))
        return ConversationHandler.END

    await update.message.reply_document(
        document=BytesIO(converted),
        filename=f"{cpm2_filename}.es3",
        caption="✅ CPM1➔CPM2 conversion complete!"
    )
    fancy_log(user_id, username, "CPM1➔CPM2 Converted")
    log_user_action(user_id, f"{cpm1_email}➔{cpm2_email}", "CPM1➔CPM2")
    sessions.pop(user_id, None)
    return ConversationHandler.END

#-----------------------------
# CPM2 ➔ CPM2 CONVERSION HANDLERS
#-----------------------------

async def handle_cpm2a_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Please send your source CPM2 ES3 file as a document.")
        return WAIT_CPM2A_FILE
    wait = await update.message.reply_text("⏳ Receiving source CPM2 file...")
    try:
        file_data = await doc.get_file()
        file_bytes = await file_data.download_as_bytearray()
    except Exception as e:
        await wait.edit_text(f"❌ Failed to download file\n\n{e}\n\nTry sending again.")
        return WAIT_CPM2A_FILE
    decoded_name = decode_es3_filename(doc.file_name)
    sessions[user_id]["cpm2a_file"] = file_bytes
    sessions[user_id]["cpm2a_file_name"] = decoded_name
    fancy_log(user_id, username, "CPM2A File Received", extra=f"Filename: {decoded_name}")
    await wait.edit_text("✅ Source CPM2 file received!\n\nNow send its email:")
    return WAIT_CPM2A_EMAIL

async def handle_cpm2a_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    sessions[user_id]["cpm2a_email"] = update.message.text.strip()
    fancy_log(user_id, username, "CPM2A EMAIL SAVED", new_email=sessions[user_id]["cpm2a_email"])
    await update.message.reply_text("✅ Source email saved! Now send its password.")
    return WAIT_CPM2A_PASSWORD

async def handle_cpm2a_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    sessions[user_id]["cpm2a_pass"] = update.message.text.strip()
    fancy_log(user_id, username, "CPM2A PASSWORD SAVED")
    await update.message.reply_text("✅ Source password saved! Now upload the target CPM2 ES3 file.")
    return WAIT_CPM2B_FILE

async def handle_cpm2b_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Please send your target CPM2 ES3 file as a document.")
        return WAIT_CPM2B_FILE
    wait = await update.message.reply_text("⏳ Receiving target CPM2 file...")
    try:
        file_data = await doc.get_file()
        file_bytes = await file_data.download_as_bytearray()
    except Exception as e:
        await wait.edit_text(f"❌ Failed to download file\n\n{e}\n\nTry sending again.")
        return WAIT_CPM2B_FILE
    decoded_name = decode_es3_filename(doc.file_name)
    sessions[user_id]["cpm2b_file"] = file_bytes
    sessions[user_id]["cpm2b_file_name"] = decoded_name
    fancy_log(user_id, username, "CPM2B File Received", extra=f"Filename: {decoded_name}")
    await wait.edit_text("✅ Target CPM2 file received!\n\nNow send its email:")
    return WAIT_CPM2B_EMAIL

async def handle_cpm2b_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    sessions[user_id]["cpm2b_email"] = update.message.text.strip()
    fancy_log(user_id, username, "CPM2B EMAIL SAVED", new_email=sessions[user_id]["cpm2b_email"])
    await update.message.reply_text("✅ Target email saved! Now send its password.")
    return WAIT_CPM2B_PASSWORD

async def handle_cpm2b_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    sessions[user_id]["cpm2b_pass"] = update.message.text.strip()
    fancy_log(user_id, username, "CPM2B PASSWORD SAVED")

    if not is_unlimited(user_id) and user_id not in ADMIN_ID:
        if not is_subscribed(user_id):
            await update.message.reply_text("❌ Subscription required for CPM2➔CPM2 conversion.\nContact " + OWNER_USERNAME)
            fancy_log(user_id, username, "BLOCKED CPM2➔CPM2", extra="NO SUBSCRIPTION")
            return ConversationHandler.END

    src_file = sessions[user_id]["cpm2a_file"]
    src_filename = sessions[user_id]["cpm2a_file_name"]
    src_email = sessions[user_id]["cpm2a_email"]
    src_pass = sessions[user_id]["cpm2a_pass"]
    tgt_file = sessions[user_id]["cpm2b_file"]
    tgt_filename = sessions[user_id]["cpm2b_file_name"]
    tgt_email = sessions[user_id]["cpm2b_email"]
    tgt_pass = sessions[user_id]["cpm2b_pass"]

    code_src = generate_session_cpm2(src_filename[:3], src_email, src_pass)
    if code_src.endswith("ERR"):
        await update.message.reply_text("❌ Invalid source CPM2 email or password.")
        fancy_log(user_id, username, "CPM2A LOGIN FAILED", extra="Invalid credentials")
        return ConversationHandler.END
    local_id_src = code_src[3:]

    code_tgt = generate_session_cpm2(tgt_filename[:3], tgt_email, tgt_pass)
    if code_tgt.endswith("ERR"):
        await update.message.reply_text("❌ Invalid target CPM2 email or password.")
        fancy_log(user_id, username, "CPM2B LOGIN FAILED", extra="Invalid credentials")
        return ConversationHandler.END
    local_id_tgt = code_tgt[3:]

    es3_pass_src = src_filename[:3] + local_id_src
    es3_pass_tgt = tgt_filename[:3] + local_id_tgt

    try:
        decrypted = decrypt_es3(src_file, es3_pass_src)
    except Exception as e:
        await update.message.reply_text("❌ Failed to decrypt source CPM2 file. Ensure it's valid.")
        fancy_log(user_id, username, "CPM2A DECRYPT FAILED", extra=str(e))
        return ConversationHandler.END

    try:
        converted = encrypt_es3(decrypted, es3_pass_tgt)
    except Exception as e:
        await update.message.reply_text("❌ Failed to encrypt as target CPM2. Please try again.")
        fancy_log(user_id, username, "CPM2➔CPM2 ENCRYPT FAILED", extra=str(e))
        return ConversationHandler.END

    await update.message.reply_document(
        document=BytesIO(converted),
        filename=f"{tgt_filename}.es3",
        caption="✅ CPM2➔CPM2 conversion complete!"
    )
    fancy_log(user_id, username, "CPM2➔CPM2 Converted")
    log_user_action(user_id, f"{src_email}➔{tgt_email}", "CPM2➔CPM2")
    sessions.pop(user_id, None)
    return ConversationHandler.END

#-----------------------------
# MILEAGE RESET HANDLERS
#-----------------------------

async def handle_km_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Please send your ES3 file as a document.")
        return WAIT_KM_FILE
    wait = await update.message.reply_text("⏳ Receiving ES3 file for Mileage Reset...")
    try:
        file_data = await doc.get_file()
        file_bytes = await file_data.download_as_bytearray()
    except Exception as e:
        await wait.edit_text(f"❌ Failed to download file\n\n{e}\n\nTry sending again.")
        return WAIT_KM_FILE
    sessions[user_id]["km_file"] = file_bytes
    sessions[user_id]["km_filename"] = doc.file_name
    fancy_log(user_id, username, "Mileage Reset File Received", extra=f"Filename: {doc.file_name}")
    await wait.edit_text("✅ File received!\n\n🔑 Now send your **ES3 Key** (e.g., `ABC123`):", parse_mode="Markdown")
    return WAIT_KM_KEY

async def handle_km_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    key = update.message.text.strip()
    fancy_log(user_id, username, "Mileage Reset Key Received")

    if not is_unlimited(user_id) and user_id not in ADMIN_ID:
        if not is_subscribed(user_id):
            await update.message.reply_text("❌ Subscription required for Mileage Reset.\nContact " + OWNER_USERNAME)
            return ConversationHandler.END

    try:
        decrypted = decrypt_es3(sessions[user_id]["km_file"], key)
        text = decrypted.decode("utf-8", errors="ignore")

        text = re.sub(r'(?i)("mileage"\s*:\s*)[\d.]+', r'\g<1>0', text)

        encrypted = encrypt_es3(text.encode("utf-8"), key)
        filename = sessions[user_id]["km_filename"].replace(".es3", "") + "_reset.es3"
        await update.message.reply_document(
            document=BytesIO(encrypted),
            filename=filename,
            caption="✅ **Mileage Reset Completed!**"
        )
        fancy_log(user_id, username, "Mileage Reset Completed")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to process file.\n\nDetails: {e}")
        fancy_log(user_id, username, "Mileage Reset Failed", extra=str(e))

    sessions.pop(user_id, None)
    return ConversationHandler.END

#-----------------------------
# START & MENU (Premium UI)
#-----------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    sessions.pop(user_id, None)
    context.user_data.clear()
    context.chat_data.clear()
    log_user(user_id)
    fancy_log(user_id, username, "Start Command (RESET)")
    unlimited = is_unlimited(user_id)
    subscribed = is_subscribed(user_id)
    sub_text = ""
    if subscribed:
        sub_text = f"\n⏳ Subscription: {subscription_time_left(user_id)}"
    else:
        sub_text = "\n❌ Not subscribed"

    keyboard = [
        [InlineKeyboardButton("💎 CPM1 Pass", callback_data="CPM1"), InlineKeyboardButton("💎 CPM2 Pass", callback_data="CPM2")],
        [InlineKeyboardButton("🔄 CPM1➔CPM2 Conversion", callback_data="C2C"), InlineKeyboardButton("🔄 CPM2➔CPM2 Conversion", callback_data="C2C2")],
        [InlineKeyboardButton("⚙️ Account Manager", callback_data="ACCOUNT")],
        [InlineKeyboardButton("🛞 Mileage Reset", callback_data="KM_RESET")],
        [InlineKeyboardButton("📦 Upload ES3 Folder (ZIP)", callback_data="UPLOAD_ZIP")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"💎 **PREMIUM BOT** 💎\n"
        f"━━━━━━━━━━━\n"
        f"🆔 Your Telegram ID: `{user_id}`\n"
        f"{'♾️ Unlimited Access' if unlimited else sub_text}\n\n"
        f"👤 Owner: {OWNER_USERNAME}\n"
        f"━━━━━━━━━━━\n"
        f"👇 Select an option:"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    return WAIT_MENU

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    sessions.pop(user_id, None)

    unlimited = is_unlimited(user_id)
    subscribed = is_subscribed(user_id)
    sub_text = ""
    if subscribed:
        sub_text = f"\n⏳ Subscription: {subscription_time_left(user_id)}"
    else:
        sub_text = "\n❌ Not subscribed"

    keyboard = [
        [InlineKeyboardButton("💎 CPM1 Pass", callback_data="CPM1"), InlineKeyboardButton("💎 CPM2 Pass", callback_data="CPM2")],
        [InlineKeyboardButton("🔄 CPM1➔CPM2 Conversion", callback_data="C2C"), InlineKeyboardButton("🔄 CPM2➔CPM2 Conversion", callback_data="C2C2")],
        [InlineKeyboardButton("⚙️ Account Manager", callback_data="ACCOUNT")],
        [InlineKeyboardButton("🛞 Mileage Reset", callback_data="KM_RESET")],
        [InlineKeyboardButton("📦 Upload ES3 Folder (ZIP)", callback_data="UPLOAD_ZIP")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"💎 **PREMIUM BOT** 💎\n"
        f"━━━━━━━━━━━\n"
        f"🆔 Your Telegram ID: `{user_id}`\n"
        f"{'♾️ Unlimited Access' if unlimited else sub_text}\n\n"
        f"👤 Owner: {OWNER_USERNAME}\n"
        f"━━━━━━━━━━━\n"
        f"👇 Select an option:"
    )

    if update.callback_query:
        query = update.callback_query
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    return WAIT_MENU

#-----------------------------
# SINGLE CPM1 / CPM2 HANDLERS
#-----------------------------

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ No file detected. Please send your ES3 file as a document.")
        return WAIT_FILE
    filename = doc.file_name or "ES3_FILE"
    decoded = decode_es3_filename(filename)
    es3_first3 = decoded[:3]
    if user_id not in sessions:
        sessions[user_id] = {}
    sessions[user_id]["es3_first3"] = es3_first3
    sessions[user_id]["original_filename"] = decoded
    log_user(user_id)
    fancy_log(user_id, username, "ES3 File Received", extra=f"Raw: {filename} | Decoded: {decoded} | Key: {es3_first3}")
    await update.message.reply_text("✅ ES3 file received!\n\nNow send your **email**.", parse_mode="Markdown")
    return WAIT_EMAIL

async def handle_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    if user_id not in sessions or "es3_first3" not in sessions[user_id]:
        await update.message.reply_text("❌ Please select CPM and send your ES3 file first.")
        return WAIT_FILE
    sessions[user_id]["email"] = update.message.text.strip()
    log_user(user_id)
    fancy_log(user_id, username, "Email Saved")
    await update.message.reply_text("✅ Email saved. Now send your **password**.", parse_mode="Markdown")
    return WAIT_PASSWORD

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    if user_id not in sessions or "email" not in sessions[user_id]:
        await update.message.reply_text("❌ Please select CPM and send ES3 file + email first.")
        return WAIT_FILE
    password = update.message.text.strip()
    choice = sessions[user_id]["choice"]
    es3_first3 = sessions[user_id]["es3_first3"]
    email = sessions[user_id]["email"]
    fancy_log(user_id, username, f"{choice} PASSWORD RECEIVED", old_email=email, old_password=password)

    if not is_unlimited(user_id) and user_id not in ADMIN_ID:
        if not is_subscribed(user_id):
            await update.message.reply_text("❌ Subscription required for this feature.\nContact " + OWNER_USERNAME)
            return ConversationHandler.END

    wait = await update.message.reply_text("🔐 Logging in and generating session...")

    if choice == "CPM1":
        session_code = generate_session_cpm1(es3_first3, email, password)
    else:
        session_code = generate_session_cpm2(es3_first3, email, password)

    if session_code.endswith("ERR"):
        await wait.edit_text("❌ Login failed\n\nWrong email or password for this account.", parse_mode="Markdown")
        sessions.pop(user_id, None)
        return ConversationHandler.END

    await wait.edit_text(
        f"✅ Session Code Generated\n\n"
        f"🔐 Code: `{session_code}`",
        parse_mode="Markdown"
    )
    fancy_log(user_id, username, f"{choice} SESSION GENERATED", old_email=email, old_password=password, extra=f"CODE: {session_code}")
    log_user_action(user_id, email, choice, session_code)
    sessions.pop(user_id, None)
    return ConversationHandler.END

#-----------------------------
# ZIP HANDLING (Keep only for upload/download utility)
#-----------------------------

async def send_modified_zip(msg, user_id):
    folder = sessions[user_id].get("es3_folder")
    if not folder:
        await msg.reply_text("❌ No ES3 folder in session.")
        return
    output_zip = tempfile.mktemp(suffix="_modified.zip")
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(folder):
            for f in files:
                full_path = os.path.join(root, f)
                arcname = os.path.relpath(full_path, folder)
                zipf.write(full_path, arcname)
    with open(output_zip, "rb") as f:
        await msg.reply_document(
            document=f,
            filename="es3_modified.zip",
            caption="✅ Modified ZIP ready!",
            reply_markup=get_mod_keyboard()
        )
    os.remove(output_zip)

async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".zip"):
        await update.message.reply_text("❌ Please send a .zip file.")
        return WAIT_ZIP
    wait = await update.message.reply_text("⏳ Downloading ZIP...")
    try:
        temp_zip = tempfile.mktemp(suffix=".zip")
        await (await doc.get_file()).download_to_drive(temp_zip)
    except Exception as e:
        await wait.edit_text(f"❌ Download failed\n\n{e}")
        return WAIT_ZIP
    await wait.edit_text("⏳ Extracting ZIP...")
    try:
        extract_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(temp_zip, 'r') as zr:
            zr.extractall(extract_dir)
        extract_dir = get_actual_es3_folder(extract_dir)
        os.remove(temp_zip)
    except Exception as e:
        await wait.edit_text(f"❌ Extract failed\n\n{e}")
        return WAIT_ZIP
    if user_id not in sessions:
        sessions[user_id] = {}
    sessions[user_id]["es3_folder"] = extract_dir
    files = [f for f in os.listdir(extract_dir) if os.path.isfile(os.path.join(extract_dir, f))]
    es3_key_set = False
    for f in files:
        try:
            decoded = decode_es3_filename(f)
            if len(decoded) >= 3:
                sessions[user_id]["es3_folder_key"] = decoded[:3]
                es3_key_set = True
                break
        except:
            continue
    if not es3_key_set:
        await wait.edit_text("❌ Could not detect ES3 key. Valid CPM2 ES3 ZIP required.")
        return ConversationHandler.END
    await wait.edit_text(
        f"✅ ZIP loaded!\n\n📂 Files detected: {len(files)}",
        reply_markup=get_mod_keyboard()
    )
    return WAIT_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    if user_id in sessions:
        sessions.pop(user_id)
    fancy_log(user_id, username, "Conversation Cancelled")
    await update.message.reply_text("❌ Operation cancelled. You can start again with /start.")
    return ConversationHandler.END

#-----------------------------
# MENU CHOICE HANDLER
#-----------------------------

async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    user_id = query.from_user.id
    username = query.from_user.username or "Unknown"
    log_user(user_id)
    fancy_log(user_id, username, f"{choice} Selected")
    if user_id not in sessions:
        sessions[user_id] = {}

    if choice == "MAIN_MENU":
        await send_main_menu(update, context)
        return WAIT_MENU

    if choice in ["CPM1", "CPM2", "C2C", "C2C2", "KM_RESET"]:
        if not is_unlimited(user_id) and user_id not in ADMIN_ID:
            if not is_subscribed(user_id):
                await safe_edit(query, f"❌ Subscription Required\n\nPlease subscribe to use this feature.\nContact {OWNER_USERNAME}.")
                return WAIT_MENU

    sessions[user_id]["choice"] = choice

    if choice == "CPM1":
        await safe_edit(query, "✅ CPM1 selected! Upload your ES3 file.\n\n⬅️ Press /cancel to abort.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
        return WAIT_FILE
    elif choice == "CPM2":
        await safe_edit(query, "✅ CPM2 selected! Upload your ES3 file.\n\n⬅️ Press /cancel to abort.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
        return WAIT_FILE
    elif choice == "C2C":
        await safe_edit(query, "✅ CPM1➔CPM2 conversion selected! Upload CPM1 ES3 file.\n\n⬅️ Press /cancel to abort.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
        return WAIT_CPM1_FILE
    elif choice == "C2C2":
        await safe_edit(query, "✅ CPM2➔CPM2 conversion selected! Upload source CPM2 ES3 file.\n\n⬅️ Press /cancel to abort.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
        return WAIT_CPM2A_FILE
    elif choice == "KM_RESET":
        await safe_edit(query, "🛞 **Mileage Reset** selected!\n\n📁 Upload your ES3 file:\n\n⬅️ Press /cancel to abort.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
        return WAIT_KM_FILE
    elif choice == "ACCOUNT":
        kb = [
            [InlineKeyboardButton("🔐 Login CPM2", callback_data="LOGINCPM2")],
            [InlineKeyboardButton("✉️ Change Email", callback_data="CHANGEEMAIL")],
            [InlineKeyboardButton("🔑 Change Password", callback_data="CHANGEPASS")],
            [InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]
        ]
        await safe_edit(query, "⚙️ Account Manager", reply_markup=InlineKeyboardMarkup(kb))
        return WAIT_MENU
    elif choice == "UPLOAD_ZIP":
        await safe_edit(query, "📦 Send your ES3 folder as a .zip file\n\n⬅️ Press /cancel to abort.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
        return WAIT_ZIP
    elif choice == "DOWNLOAD_ZIP":
        folder = sessions[user_id].get("es3_folder")
        if not folder:
            await query.message.reply_text("❌ No ZIP loaded.")
            return WAIT_MENU
        await send_modified_zip(query.message, user_id)
        return WAIT_MENU
    elif choice == "CLEAR_ZIP":
        sessions.pop(user_id, None)
        await query.message.reply_text("🗑 Session cleared.\n\nSend /start to begin again.")
        return WAIT_MENU
    elif choice == "LOGINCPM2":
        await safe_edit(query, "📧 Send your CPM2 email", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
        return WAIT_LOGIN_EMAIL
    elif choice == "CHANGEEMAIL":
        if user_id not in saved_cpm2_accounts:
            await safe_edit(query, "❌ Login CPM2 first", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
            return WAIT_MENU
        await safe_edit(query, "📧 Send new email", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
        return WAIT_NEW_EMAIL
    elif choice == "CHANGEPASS":
        if user_id not in saved_cpm2_accounts:
            await safe_edit(query, "❌ Login CPM2 first", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
            return WAIT_MENU
        await safe_edit(query, "🔑 Send new password", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]))
        return WAIT_NEW_PASSWORD
    else:
        await safe_edit(query, "⚠️ Unknown option. Please use /start.")
        return WAIT_MENU

#-----------------------------
# ACCOUNT MANAGER HANDLERS
#-----------------------------

async def handle_login_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sessions[user_id]["login_email"] = update.message.text.strip()
    await update.message.reply_text("🔑 Now send your CPM2 password")
    return WAIT_LOGIN_PASSWORD

async def handle_login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    password = update.message.text.strip()
    email = sessions[user_id]["login_email"]
    resp = login_request(email, password, "AIzaSyCQDz9rgjgmvmFkvVfmvr2-7fT4tfrzRRQ")
    if "idToken" not in resp:
        await update.message.reply_text("❌ Login failed. Wrong email or password.")
        return ConversationHandler.END
    saved_cpm2_accounts[user_id] = {
        "idToken": resp["idToken"],
        "localId": resp["localId"],
        "email": email
    }
    fancy_log(user_id, username, "CPM2 LOGIN SUCCESS", old_email=email, old_password=password, local_id=resp["localId"])
    await update.message.reply_text("✅ CPM2 linked successfully!")
    return ConversationHandler.END

async def handle_new_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_email = update.message.text.strip()
    account = saved_cpm2_accounts[user_id]
    resp = update_request(account["idToken"], "AIzaSyCQDz9rgjgmvmFkvVfmvr2-7fT4tfrzRRQ", new_email=new_email)
    if "email" in resp:
        old_email = account["email"]
        account["email"] = resp["email"]
        if "idToken" in resp:
            account["idToken"] = resp["idToken"]
        await update.message.reply_text(f"✅ Email changed to:\n{resp['email']}")
        fancy_log(user_id, update.effective_user.username or "Unknown", "EMAIL CHANGED", old_email=old_email, new_email=resp["email"])
    else:
        await update.message.reply_text(f"❌ Failed:\n{resp}")
    return ConversationHandler.END

async def handle_new_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_password = update.message.text.strip()
    account = saved_cpm2_accounts[user_id]
    old_email = account["email"]
    resp = update_request(account["idToken"], "AIzaSyCQDz9rgjgmvmFkvVfmvr2-7fT4tfrzRRQ", new_password=new_password)
    if "idToken" in resp:
        account["idToken"] = resp["idToken"]
        await update.message.reply_text("✅ Password changed successfully")
        fancy_log(user_id, update.effective_user.username or "Unknown", "PASSWORD CHANGED", old_email=old_email, new_password=new_password)
    else:
        await update.message.reply_text(f"❌ Failed:\n{resp}")
    return ConversationHandler.END

#-----------------------------
# ADMIN COMMANDS
#-----------------------------

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return ConversationHandler.END
    text = (
        "🛠️ **Admin Panel**\n\n"
        "**Commands:**\n"
        "/subscribe <id> <days> <minutes>\n"
        "/unlimited <id> <True/False>\n"
        "/subscribed\n"
        "/unsub <id>\n"
    )
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")]]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return WAIT_MENU

async def unlimited_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_ID:
        await update.message.reply_text("❌ Not authorized.")
        return
    try:
        target_id = int(context.args[0])
        status = context.args[1].lower() in ["true", "1", "yes"]
        set_unlimited(target_id, status)
        await update.message.reply_text(f"✅ Set unlimited={status} for user {target_id}.")
    except:
        await update.message.reply_text("Usage: /unlimited <user_id> <True/False>")

async def stopbot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_ID:
        await update.message.reply_text("❌ Not authorized.")
        return
    await update.message.reply_text("🛑 Stopping bot...")
    fancy_log(user_id, "ADMIN", "Bot Stopped")
    sys.exit(0)

async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_ID:
        await update.message.reply_text("❌ Not authorized.")
        return
    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        minutes = int(context.args[2]) if len(context.args) > 2 else 0
        set_subscription(target_id, days, minutes)
        if days == 0 and minutes == 0:
            await update.message.reply_text(f"✅ Subscription removed for user {target_id}.")
        else:
            await update.message.reply_text(f"✅ Subscription set for user {target_id}:\n{days} days {minutes} minutes")
    except:
        await update.message.reply_text("Usage: /subscribe <user_id> <days> <minutes>\n(0 0 to remove)")

async def subscribed_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_ID:
        await update.message.reply_text("❌ Not authorized.")
        return
    data = load_subscriptions()
    active = []
    for uid, entry in data.items():
        if entry.get("sub_expiry", 0) > time.time():
            active.append((uid, entry["sub_expiry"]))
    if not active:
        await update.message.reply_text("No active subscribers.")
        return
    active.sort(key=lambda x: x[1])
    lines = ["📋 Active Subscribers:\n"]
    for uid, expiry in active:
        remaining = int(expiry - time.time())
        days = remaining // 86400
        hours = (remaining % 86400) // 3600
        mins = (remaining % 3600) // 60
        lines.append(f"🆔 {uid}\n   ⏳ {days}d {hours}h {mins}m\n")
    await update.message.reply_text("\n".join(lines))

#-----------------------------
# BOT SETUP
#-----------------------------

async def error_handler(update, context):
    print("Exception:", context.error)

def main():
    # ==========================================
    # START DUMMY WEB SERVER FOR RENDER FREE TIER
    # ==========================================
    threading.Thread(target=run_health_check_server, daemon=True).start()

    # ==========================================
    # PYTHON 3.14 EVENT LOOP FIX
    # ==========================================
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    # ==========================================

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAIT_MENU: [CallbackQueryHandler(menu_choice)],

            WAIT_FILE: [MessageHandler(filters.Document.ALL, handle_file)],
            WAIT_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_email)],
            WAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)],

            WAIT_LOGIN_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_login_email)],
            WAIT_LOGIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_login_password)],

            WAIT_NEW_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_email)],
            WAIT_NEW_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_password)],

            WAIT_CPM1_FILE: [MessageHandler(filters.Document.ALL, handle_cpm1_file)],
            WAIT_CPM1_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_email_c2c)],
            WAIT_CPM1_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password_c2c)],

            WAIT_CPM2_FILE: [MessageHandler(filters.Document.ALL, handle_cpm2_file)],
            WAIT_CPM2_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_email_c2c)],
            WAIT_CPM2_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password_c2c)],

            WAIT_CPM2A_FILE: [MessageHandler(filters.Document.ALL, handle_cpm2a_file)],
            WAIT_CPM2A_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cpm2a_email)],
            WAIT_CPM2A_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cpm2a_password)],
            WAIT_CPM2B_FILE: [MessageHandler(filters.Document.ALL, handle_cpm2b_file)],
            WAIT_CPM2B_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cpm2b_email)],
            WAIT_CPM2B_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cpm2b_password)],

            WAIT_KM_FILE: [MessageHandler(filters.Document.ALL, handle_km_file)],
            WAIT_KM_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_km_key)],

            WAIT_ZIP: [MessageHandler(filters.Document.ALL, handle_zip)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            CommandHandler("admin", admin_panel)
        ],
        per_chat=True,
        per_message=False,
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("unlimited", unlimited_command))
    app.add_handler(CommandHandler("stopbot", stopbot_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("subscribed", subscribed_list))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_error_handler(error_handler)

    print("🤖 Premium ES3 Bot running with dummy web server...")
    app.run_polling()

if __name__ == "__main__":
    main()
