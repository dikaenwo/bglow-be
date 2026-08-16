import os
import jwt
import secrets
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import base64
from typing import Optional
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection
from dotenv import load_dotenv
import json
import requests as req
from bs4 import BeautifulSoup
import re
from urllib.parse import unquote
from recommender import score_products
import io
from PIL import Image
import google.generativeai as genai

# Muat .env jika ada (untuk development lokal)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_path):
    load_dotenv(_env_path)
else:
    load_dotenv()

# ─── Midtrans Config ────────────────────────────────────────────────────────
MIDTRANS_SERVER_KEY   = os.environ.get('MIDTRANS_SERVER_KEY', '')
MIDTRANS_CLIENT_KEY   = os.environ.get('MIDTRANS_CLIENT_KEY', '')
MIDTRANS_MERCHANT_ID  = os.environ.get('MIDTRANS_MERCHANT_ID', '')
MIDTRANS_IS_PRODUCTION = os.environ.get('MIDTRANS_IS_PRODUCTION', 'False').lower() == 'true'
MIDTRANS_BASE_URL = 'https://app.midtrans.com' if MIDTRANS_IS_PRODUCTION else 'https://app.sandbox.midtrans.com'
MIDTRANS_SNAP_URL = f'{MIDTRANS_BASE_URL}/snap/v1/transactions'

if not MIDTRANS_SERVER_KEY:
    print("[WARN] MIDTRANS_SERVER_KEY belum dikonfigurasi di .env")

# Auto-initialize database tables if missing
try:
    from init_db import init_database
    init_database()
except Exception as db_init_err:
    print(f"[WARN] Gagal melakukan inisialisasi database otomatis: {db_init_err}")

app = Flask(__name__)

# ─── CORS ───────────────────────────────────────────────────────────────────
# Di production, ganti dengan origin spesifik via env var CORS_ORIGINS
_cors_origins = os.environ.get('CORS_ORIGINS', '*')
CORS(app, resources={r"/api/*": {"origins": _cors_origins, "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"], "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"]}})

# ─── JWT Config ─────────────────────────────────────────────────────────────
# Di production, WAJIB set JWT_SECRET_KEY di environment variable VPS!
# Buat dengan: python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or secrets.token_hex(32)
JWT_ALGORITHM  = 'HS256'
JWT_EXPIRY_DAYS = 7   # Token berlaku 7 hari

if not os.environ.get('JWT_SECRET_KEY'):
    print("[WARN] JWT_SECRET_KEY tidak diset di environment. "
          "Setiap restart server akan invalidate semua token yang ada. "
          "Set JWT_SECRET_KEY di file .env atau environment VPS untuk production!")


# ─── BPOM Scraping Config ───────────────────────────────────────────────────
BPOM_BASE = "https://cekbpom.pom.go.id"
BPOM_DT   = f"{BPOM_BASE}/produk-dt/all"
BPOM_PAGE = f"{BPOM_BASE}/all-produk"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def extract_na_from_query(query):
    query = query.strip()
    match = re.search(r'(N[A-E])[\s\-]?(\d{11})', query, re.IGNORECASE)
    if match:
        return match.group(1).upper() + match.group(2)
    if 'cekbpom.pom.go.id' in query or query.startswith('http'):
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(query)
            qs = parse_qs(parsed.query)
            if 'query' in qs:
                return qs['query'][0]
            segments = [s for s in parsed.path.split('/') if s]
            if segments:
                return segments[-1]
        except Exception:
            parts = query.split('/')
            return parts[-1].split('?')[0] or query
    return query

def clean_html(raw):
    if not raw:
        return ''
    return BeautifulSoup(str(raw), 'html.parser').get_text(separator=' ', strip=True)

def get_session_with_csrf():
    s = req.Session()
    r = s.get(BPOM_PAGE, headers={"User-Agent": UA}, timeout=12)
    r.raise_for_status()
    csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', r.text)
    csrf_token = csrf_match.group(1) if csrf_match else ""
    xsrf_cookie = unquote(s.cookies.get("XSRF-TOKEN", ""))
    return s, csrf_token, xsrf_cookie

def build_payload(query, csrf_token):
    is_na = bool(re.match(r'^N[A-E]\d+$', query, re.IGNORECASE))
    
    payload = {
        "draw": "1", "columns[0][data]": "PRODUCT_ID", "columns[0][name]": "", "columns[0][searchable]": "true", "columns[0][orderable]": "false", "columns[0][search][value]": "", "columns[0][search][regex]": "false",
        "columns[1][data]": "PRODUCT_REGISTER", "columns[1][name]": "", "columns[1][searchable]": "true", "columns[1][orderable]": "true", "columns[1][search][value]": "", "columns[1][search][regex]": "false",
        "columns[2][data]": "PRODUCT_NAME", "columns[2][name]": "", "columns[2][searchable]": "true", "columns[2][orderable]": "true", "columns[2][search][value]": "", "columns[2][search][regex]": "false",
        "columns[3][data]": "REGISTRAR_NAME", "columns[3][name]": "", "columns[3][searchable]": "true", "columns[3][orderable]": "true", "columns[3][search][value]": "", "columns[3][search][regex]": "false",
        "order[0][column]": "1", "order[0][dir]": "asc", "start": "0", "length": "10", "search[value]": "", "search[regex]": "false",
        "query": "", "product_register": "", "product_name": "", "product_brand": "", "product_package": "", "product_form": "", "ingredients": "",
        "submit_date_start": "", "submit_date_end": "", "product_date_start": "", "product_date_end": "", "expire_date_start": "", "expire_date_end": "",
        "manufacturer_name": "", "status": "", "release_date": "", "manufacturer": "", "registrar": "",
        "_token": csrf_token,
    }

    if is_na:
        payload["product_register"] = query
    else:
        payload["search[value]"] = query

    return payload


# ─── JWT Helpers ────────────────────────────────────────────────────────────

def generate_token(user_id: int) -> str:
    """Buat JWT Bearer token untuk user_id yang diberikan."""
    payload = {
        'sub': str(user_id),                                     # Subject (user id) must be string
        'iat': datetime.now(timezone.utc),                       # Issued at
        'exp': datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS)  # Expiry
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode dan verifikasi JWT. Return payload dict, atau None jika invalid."""
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except Exception as e:
        print(f"[ERROR] decode_token failed: {e}", flush=True)
        return None


def require_auth(f):
    """
    Decorator untuk endpoint yang butuh autentikasi.
    Membaca header: Authorization: Bearer <token>
    Menyimpan user_id yang ter-autentikasi ke g.current_user_id
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({"detail": "Token autentikasi tidak ditemukan. Silakan login terlebih dahulu."}), 401

        token = auth_header.split(' ', 1)[1]
        payload = decode_token(token)
        if payload is None:
            return jsonify({"detail": "Token tidak valid atau sudah kadaluarsa. Silakan login ulang."}), 401

        try:
            g.current_user_id = int(payload['sub'])
        except (ValueError, TypeError):
            return jsonify({"detail": "Format sub dalam token tidak valid."}), 401
        return f(*args, **kwargs)
    return decorated


# ─── Helper ─────────────────────────────────────────────────────────────────

def fetch_extra_user_data(cursor, user_id):
    # 1. Favorites
    cursor.execute("""
        SELECT product_id, product_name, product_brand, product_price, product_emoji, product_bg_color, product_rating 
        FROM user_favorites WHERE user_id = %s
    """, (user_id,))
    favs = cursor.fetchall()
    favorites_list = []
    for f in favs:
        favorites_list.append({
            "id": f["product_id"],
            "name": f["product_name"],
            "brand": f["product_brand"],
            "price": f["product_price"],
            "emoji": f["product_emoji"],
            "bgColor": f["product_bg_color"],
            "rating": float(f["product_rating"]) if f["product_rating"] is not None else 0.0
        })
    favorites_json = json.dumps(favorites_list)

    # 2. Diary entries
    cursor.execute("""
        SELECT entry_date, mood, conditions_json, products, notes, image_url 
        FROM user_diary WHERE user_id = %s ORDER BY id DESC
    """, (user_id,))
    diaries = cursor.fetchall()
    diary_list = []
    for d in diaries:
        try:
            conds = json.loads(d["conditions_json"])
        except Exception:
            conds = []
        diary_list.append({
            "date": d["entry_date"],
            "mood": d["mood"],
            "conditions": conds,
            "products": d["products"] or "-",
            "notes": d["notes"] or "-",
            "image": d["image_url"]
        })
    diary_json = json.dumps(diary_list)

    # 3. Routines
    cursor.execute("SELECT routine_data FROM user_routines WHERE user_id = %s", (user_id,))
    routine_row = cursor.fetchone()
    routine_json = routine_row["routine_data"] if routine_row else json.dumps({"morning": [], "night": []})

    # 4. Special schedules
    cursor.execute("SELECT schedule_data FROM user_special_schedules WHERE user_id = %s", (user_id,))
    sched_row = cursor.fetchone()
    special_schedule_json = sched_row["schedule_data"] if sched_row else json.dumps({"morning": {}, "night": {}})

    # 5. Streaks
    cursor.execute("SELECT streak_data FROM user_streaks WHERE user_id = %s", (user_id,))
    streak_row = cursor.fetchone()
    streak_json = streak_row["streak_data"] if streak_row else json.dumps({"current": 0, "best": 0, "lastDate": None, "completedDays": [False]*7})

    # 6. Routine progress
    cursor.execute("SELECT progress_data FROM user_routine_progress WHERE user_id = %s", (user_id,))
    prog_row = cursor.fetchone()
    routine_progress_json = prog_row["progress_data"] if prog_row else json.dumps({"date": "", "progress": {"morning": [], "night": []}})

    return {
        "favorites": favorites_json,
        "diary_entries": diary_json,
        "routine": routine_json,
        "special_schedule": special_schedule_json,
        "streak": streak_json,
        "routine_progress": routine_progress_json
    }


# ─── Public Endpoints (tidak butuh token) ───────────────────────────────────

@app.route("/api/register-otp", methods=["POST"])
def send_register_otp():
    data = request.get_json()
    if not data or 'email' not in data:
        return jsonify({"detail": "Email wajib diisi"}), 400

    email = data['email'].strip()

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        existing_user = cursor.fetchone()
        if existing_user:
            return jsonify({"detail": "Email sudah terdaftar"}), 400

        # Generate 4-digit OTP
        otp_code = str(random.randint(1000, 9999))

        # Send OTP email via SMTP
        sent = send_otp_email(email, otp_code)

        return jsonify({
            "message": "Kode OTP pendaftaran telah dikirim",
            "otp": otp_code,
            "email": email,
            "email_sent": sent
        }), 200
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route("/api/register", methods=["POST"])
def register_user():
    data = request.get_json()
    if not data or 'name' not in data or 'email' not in data or 'password' not in data:
        return jsonify({"detail": "Data tidak lengkap"}), 400

    name = data['name']
    email = data['email']
    password = data['password']

    if len(password) < 8:
        return jsonify({"detail": "Kata sandi minimal 8 karakter"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        existing_user = cursor.fetchone()
        if existing_user:
            return jsonify({"detail": "Email sudah terdaftar"}), 400

        hashed_password = generate_password_hash(password)
        cursor.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)",
            (name, email, hashed_password)
        )
        conn.commit()
        return jsonify({"message": "Pendaftaran berhasil", "status": "success"}), 201

    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/login", methods=["POST"])
def login_user():
    data = request.get_json()
    if not data or 'email' not in data or 'password' not in data:
        return jsonify({"detail": "Data tidak lengkap"}), 400

    email = data['email']
    password = data['password']

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        db_user = cursor.fetchone()

        if not db_user:
            return jsonify({"detail": "Email tidak ditemukan"}), 400

        if not check_password_hash(db_user['password_hash'], password):
            return jsonify({"detail": "Kata sandi salah"}), 400

        extra = fetch_extra_user_data(cursor, db_user['id'])
        token  = generate_token(db_user['id'])

        return jsonify({
            "message": "Login berhasil",
            "status": "success",
            "token": token,
            "user": {
                "id": db_user['id'],
                "name": db_user['name'],
                "email": db_user['email'],
                "profile_photo": db_user.get('profile_photo'),
                "skin_type": db_user.get('skin_type'),
                "acne_level": db_user.get('acne_level'),
                "oil_level": db_user.get('oil_level'),
                "pore_condition": db_user.get('pore_condition'),
                "skin_score": db_user.get('skin_score', 0),
                "sunscreen_interval": db_user.get('sunscreen_interval', 2),
                "favorites": extra["favorites"],
                "diary_entries": extra["diary_entries"],
                "routine": extra["routine"],
                "special_schedule": extra["special_schedule"],
                "streak": extra["streak"],
                "routine_progress": extra["routine_progress"]
            }
        }), 200

    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


def send_otp_email(to_email: str, otp_code: str) -> bool:
    """Kirim email OTP asli via SMTP (Gmail / SMTP Server)."""
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_EMAIL", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()

    if not smtp_user or not smtp_pass:
        print(f"[SMTP WARN] SMTP credentials (SMTP_EMAIL, SMTP_PASSWORD) belum diatur di .env. Kode OTP untuk {to_email} adalah: {otp_code}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Kode OTP Pemulihan Kata Sandi B-Glow: {otp_code}"
        msg["From"] = f"B-Glow Support <{smtp_user}>"
        msg["To"] = to_email

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8">
          <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 20px; }}
            .card {{ max-width: 480px; margin: 0 auto; background: #ffffff; border-radius: 16px; padding: 32px; box-shadow: 0 10px 25px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; text-align: center; }}
            .logo {{ font-size: 24px; font-weight: 800; color: #6366f1; margin-bottom: 8px; }}
            .title {{ font-size: 18px; font-weight: 700; color: #0f172a; margin-bottom: 12px; }}
            .otp-box {{ background: #f1f5f9; border-radius: 12px; padding: 16px; font-size: 32px; font-weight: 800; letter-spacing: 8px; color: #4f46e5; margin: 20px 0; border: 1.5px dashed #6366f1; }}
            .footer {{ font-size: 12px; color: #94a3b8; margin-top: 24px; line-height: 1.5; }}
          </style>
        </head>
        <body>
          <div class="card">
            <div class="logo">✨ B-Glow</div>
            <div class="title">Kode OTP Pemulihan Akun</div>
            <p style="font-size: 14px; color: #475569; line-height: 1.5;">
              Kami menerima permintaan untuk mengatur ulang kata sandi akun B-Glow Anda. Gunakan kode OTP di bawah ini untuk melanjutkan:
            </p>
            <div class="otp-box">{otp_code}</div>
            <p style="font-size: 13px; color: #64748b;">
              Kode ini berlaku selama 10 menit. Jangan bagikan kode ini kepada siapa pun.
            </p>
            <div class="footer">
              Jika Anda tidak merasa meminta perubahan kata sandi, abaikan email ini.<br>
              &copy; B-Glow Skincare Assistant
            </div>
          </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [to_email], msg.as_string())
        
        print(f"[SMTP SUCCESS] Email OTP berhasil dikirim ke {to_email}")
        return True
    except Exception as err:
        print(f"[SMTP ERROR] Gagal mengirim email OTP ke {to_email}: {err}")
        return False


@app.route("/api/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json()
    if not data or 'email' not in data:
        return jsonify({"detail": "Email wajib diisi"}), 400

    email = data['email'].strip()
    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        db_user = cursor.fetchone()

        if not db_user:
            return jsonify({"detail": "Email tidak terdaftar"}), 400

        # Generate random 4-digit OTP
        otp_code = str(random.randint(1000, 9999))

        # Kirim email SMTP secara otomatis jika credentials dikonfigurasi
        sent = send_otp_email(email, otp_code)

        return jsonify({
            "message": "Kode OTP telah berhasil dikirim ke email Anda" if sent else "Kode OTP telah dibuat",
            "otp": otp_code,
            "email": email,
            "email_sent": sent
        }), 200
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json()
    if not data or 'email' not in data or 'password' not in data:
        return jsonify({"detail": "Data tidak lengkap"}), 400

    email = data['email']
    password = data['password']

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        db_user = cursor.fetchone()

        if not db_user:
            return jsonify({"detail": "User tidak ditemukan"}), 404

        hashed_password = generate_password_hash(password)
        cursor.execute("UPDATE users SET password_hash = %s WHERE email = %s", (hashed_password, email))
        conn.commit()

        return jsonify({"message": "Kata sandi berhasil diperbarui", "status": "success"}), 200
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/social-login", methods=["POST"])
def social_login():
    data = request.get_json()
    if not data or 'email' not in data or 'name' not in data or 'provider' not in data:
        return jsonify({"detail": "Data tidak lengkap"}), 400

    email = data['email']
    name = data['name']
    provider = data['provider']

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        db_user = cursor.fetchone()

        if not db_user:
            dummy_hash = generate_password_hash(f"social_{provider}_dummy_pass_98765")
            cursor.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)",
                (name, email, dummy_hash)
            )
            conn.commit()
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            db_user = cursor.fetchone()

        extra = fetch_extra_user_data(cursor, db_user['id'])
        token  = generate_token(db_user['id'])

        return jsonify({
            "message": f"Login dengan {provider} berhasil",
            "status": "success",
            "token": token,
            "user": {
                "id": db_user['id'],
                "name": db_user['name'],
                "email": db_user['email'],
                "profile_photo": db_user.get('profile_photo'),
                "skin_type": db_user.get('skin_type'),
                "acne_level": db_user.get('acne_level'),
                "oil_level": db_user.get('oil_level'),
                "pore_condition": db_user.get('pore_condition'),
                "skin_score": db_user.get('skin_score', 0),
                "sunscreen_interval": db_user.get('sunscreen_interval', 2),
                "favorites": extra["favorites"],
                "diary_entries": extra["diary_entries"],
                "routine": extra["routine"],
                "special_schedule": extra["special_schedule"],
                "streak": extra["streak"],
                "routine_progress": extra["routine_progress"]
            }
        }), 200

    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/data", methods=["GET"])
def read_data():
    """Health check endpoint — public, tanpa autentikasi."""
    return jsonify({"data": "API is running"})


# ─── Protected Endpoints (wajib Bearer token) ────────────────────────────────

@app.route("/api/user/<int:user_id>", methods=["GET"])
@require_auth
def get_user_profile(user_id):
    # Pastikan user hanya bisa akses profil dirinya sendiri
    if g.current_user_id != user_id:
        return jsonify({"detail": "Akses tidak diizinkan"}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, name, email, profile_photo, cover_photo, skin_type, acne_level, oil_level, pore_condition, skin_score, sunscreen_interval 
            FROM users WHERE id = %s
        """, (user_id,))
        user_data = cursor.fetchone()
        if not user_data:
            return jsonify({"detail": "User tidak ditemukan"}), 404

        extra = fetch_extra_user_data(cursor, user_id)
        user_data.update(extra)

        return jsonify(user_data), 200
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/user/<int:user_id>", methods=["DELETE"])
@require_auth
def delete_user_account(user_id):
    """Google Play Policy Compliant Account Deletion Endpoint."""
    if g.current_user_id != user_id:
        return jsonify({"detail": "Akses tidak diizinkan"}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        return jsonify({"message": "Akun dan seluruh data Anda telah berhasil dihapus secara permanen", "status": "success"}), 200
    except Exception as e:
        return jsonify({"detail": f"Gagal menghapus akun: {str(e)}"}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/user/<int:user_id>", methods=["PUT"])
@require_auth
def update_user_profile(user_id):
    # Pastikan user hanya bisa update profil dirinya sendiri
    if g.current_user_id != user_id:
        return jsonify({"detail": "Akses tidak diizinkan"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"detail": "Data tidak boleh kosong"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        if not user_data:
            return jsonify({"detail": "User tidak ditemukan"}), 404

        # Susun update core profile secara dinamis
        update_fields = []
        params = []

        allowed_core_fields = [
            'name', 'email', 'profile_photo', 'cover_photo', 'skin_type',
            'acne_level', 'oil_level', 'pore_condition', 'skin_score',
            'sunscreen_interval', 'skin_problems'
        ]
        for field in allowed_core_fields:
            if field in data:
                update_fields.append(f"{field} = %s")
                params.append(data[field])

        if update_fields:
            params.append(user_id)
            update_query = f"UPDATE users SET {', '.join(update_fields)} WHERE id = %s"
            cursor.execute(update_query, tuple(params))

        # 1. Favorites
        if 'favorites' in data:
            cursor.execute("DELETE FROM user_favorites WHERE user_id = %s", (user_id,))
            try:
                favs_list = json.loads(data['favorites'])
                for f in favs_list:
                    cursor.execute("""
                        INSERT INTO user_favorites 
                        (user_id, product_id, product_name, product_brand, product_price, product_emoji, product_bg_color, product_rating) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        user_id,
                        f.get("id"),
                        f.get("name"),
                        f.get("brand"),
                        f.get("price"),
                        f.get("emoji"),
                        f.get("bgColor"),
                        f.get("rating")
                    ))
            except Exception as e:
                print("Error saving favorites:", e)

        # 2. Diary entries
        if 'diary_entries' in data:
            cursor.execute("DELETE FROM user_diary WHERE user_id = %s", (user_id,))
            try:
                diary_list = json.loads(data['diary_entries'])
                for d in diary_list:
                    cursor.execute("""
                        INSERT INTO user_diary 
                        (user_id, entry_date, mood, conditions_json, products, notes, image_url) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        user_id,
                        d.get("date"),
                        d.get("mood"),
                        json.dumps(d.get("conditions", [])),
                        d.get("products"),
                        d.get("notes"),
                        d.get("image")
                    ))
            except Exception as e:
                print("Error saving diary entries:", e)

        # 3. Routine
        if 'routine' in data:
            cursor.execute("""
                INSERT INTO user_routines (user_id, routine_data) 
                VALUES (%s, %s) 
                ON DUPLICATE KEY UPDATE routine_data = %s
            """, (user_id, data['routine'], data['routine']))

        # 4. Special schedule
        if 'special_schedule' in data:
            cursor.execute("""
                INSERT INTO user_special_schedules (user_id, schedule_data) 
                VALUES (%s, %s) 
                ON DUPLICATE KEY UPDATE schedule_data = %s
            """, (user_id, data['special_schedule'], data['special_schedule']))

        # 5. Streak
        if 'streak' in data:
            cursor.execute("""
                INSERT INTO user_streaks (user_id, streak_data) 
                VALUES (%s, %s) 
                ON DUPLICATE KEY UPDATE streak_data = %s
            """, (user_id, data['streak'], data['streak']))

        # 6. Routine progress
        if 'routine_progress' in data:
            cursor.execute("""
                INSERT INTO user_routine_progress (user_id, progress_data) 
                VALUES (%s, %s) 
                ON DUPLICATE KEY UPDATE progress_data = %s
            """, (user_id, data['routine_progress'], data['routine_progress']))

        conn.commit()

        cursor.execute("""
            SELECT id, name, email, profile_photo, cover_photo, skin_type, acne_level, oil_level, pore_condition, skin_score, sunscreen_interval 
            FROM users WHERE id = %s
        """, (user_id,))
        updated_user = cursor.fetchone()
        extra = fetch_extra_user_data(cursor, user_id)
        updated_user.update(extra)

        return jsonify({
            "message": "Profil berhasil diperbarui",
            "status": "success",
            "user": updated_user
        }), 200
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/bpom-history/<int:user_id>", methods=["GET"])
@require_auth
def get_bpom_history(user_id):
    if g.current_user_id != user_id:
        return jsonify({"detail": "Akses tidak diizinkan"}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, product_name, reg_no, manufacturer, status, created_at 
            FROM user_bpom_history 
            WHERE user_id = %s 
            ORDER BY id DESC LIMIT 10
        """, (user_id,))
        rows = cursor.fetchall()

        for row in rows:
            if row.get('created_at'):
                row['created_at'] = row['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')

        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/bpom-history", methods=["POST"])
@require_auth
def add_bpom_history():
    data = request.get_json()
    if not data or 'user_id' not in data or 'product_name' not in data or 'reg_no' not in data or 'status' not in data:
        return jsonify({"detail": "Data tidak lengkap"}), 400

    user_id = data['user_id']

    # Pastikan user hanya bisa simpan riwayat untuk dirinya sendiri
    if g.current_user_id != user_id:
        return jsonify({"detail": "Akses tidak diizinkan"}), 403

    product_name = data['product_name']
    reg_no = data['reg_no']
    manufacturer = data.get('manufacturer', '')
    status = data['status']

    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id FROM user_bpom_history 
            WHERE user_id = %s AND product_name = %s AND reg_no = %s
        """, (user_id, product_name, reg_no))
        existing = cursor.fetchone()

        if existing:
            cursor.execute("DELETE FROM user_bpom_history WHERE id = %s", (existing[0],))

        cursor.execute("""
            INSERT INTO user_bpom_history (user_id, product_name, reg_no, manufacturer, status) 
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, product_name, reg_no, manufacturer, status))

        # Prune older entries keeping only the top 10
        cursor.execute("""
            DELETE FROM user_bpom_history 
            WHERE user_id = %s AND id NOT IN (
                SELECT id FROM (
                    SELECT id FROM user_bpom_history 
                    WHERE user_id = %s 
                    ORDER BY id DESC LIMIT 10
                ) tmp
            )
        """, (user_id, user_id))

        conn.commit()
        return jsonify({"message": "Riwayat berhasil disimpan", "status": "success"}), 201
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/scan-bpom", methods=["GET", "POST"])
def scan_bpom():
    """BPOM scraping endpoint — public karena tidak butuh akun untuk cek produk."""
    if request.method == "POST":
        data = request.get_json()
        raw_query = data.get('query', '').strip()
    else:
        raw_query = (request.args.get('na') or request.args.get('query') or "").strip()

    if not raw_query:
        return jsonify({"detail": "Nomor registrasi/query tidak boleh kosong"}), 400

    na_number = extract_na_from_query(raw_query)

    try:
        session, csrf_token, xsrf_cookie = get_session_with_csrf()
        payload = build_payload(na_number, csrf_token)

        headers = {
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": BPOM_PAGE,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": BPOM_BASE,
            "X-CSRF-TOKEN": csrf_token,
            "X-XSRF-TOKEN": xsrf_cookie,
        }

        resp = session.post(BPOM_DT, data=payload, headers=headers, timeout=20)
        
        if resp.status_code != 200:
            return jsonify({"detail": f"BPOM API error {resp.status_code}"}), 502

        data = resp.json()
        rows = data.get('data', [])
        
        results = []
        for row in rows:
            results.append({
                "tipe": clean_html(row.get('CLASS', '') or row.get('CLASS_ID', '')),
                "nomor_registrasi": clean_html(row.get('PRODUCT_REGISTER', '')),
                "nama_produk": clean_html(row.get('PRODUCT_NAME', '')),
                "merek": clean_html(row.get('PRODUCT_BRANDS', '')),
                "kemasan": clean_html(row.get('PRODUCT_PACKAGE', '')),
                "pendaftar": clean_html(row.get('REGISTRAR', '') or row.get('MANUFACTURER_NAME', '')),
                "tanggal_terbit": clean_html(row.get('PRODUCT_DATE', '')),
            })

        hasil_scraping = {
            "query": na_number,
            "raw_query": raw_query,
            "results": results,
            "total_found": len(results)
        }
        return jsonify(hasil_scraping), 200

    except Exception as e:
        return jsonify({"detail": f"Gagal memindai data: {str(e)}"}), 500


# ─── Gemini AI Skin Scan (via Official SDK) ───────────────────────────────────

_GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
_GEMINI_MODEL = 'gemini-3.1-flash-lite'

if _GEMINI_API_KEY:
    genai.configure(api_key=_GEMINI_API_KEY)
else:
    print("[WARN] GEMINI_API_KEY tidak diset. Menggunakan mode mock untuk /api/skin-scan.")


SKIN_ANALYSIS_PROMPT = """\
Anda adalah pakar Dermatologis Klinis dan spesialis analisis citra kulit (Dermatoskopi AI).
Tugas Anda: Lakukan pemindaian medis dan analisis kondisi kulit wajah pada gambar secara akurat dan sangat teliti.

=== PANDUAN DEFINISI VISUAL KLINIS KONDISI KULIT ===
Pahami perbedaan spesifik berikut sebelum memberi label:
1. "Jerawat" (Papula/Pustula/Nodul): Benjolan menonjol, meradang, seringkali memiliki titik putih/kuning di tengah (nanah) atau kemerahan bengkak yang terisolasi.
2. "PIE" (Post-Inflammatory Erythema): Noda/bekas jerawat berwarna MERAH MUDA hingga MERAH CERAH akibat pelebaran pembuluh darah pasca-inflamasi (datar, bukan benjolan nanah).
3. "PIH" (Post-Inflammatory Hyperpigmentation): Noda/bekas jerawat berwarna COKLAT KEHITAMAN atau GELAP akibat penumpukan melanin (datar, bukan kemerahan).
4. "Aging": Garis-garis halus, kerutan, atau penurunan elastisitas/kekencangan kulit di sekitar mata, dahi, atau senyum.
5. "Kusam": Kulit yang tampak kusam, warna kulit tidak merata, melasma, atau flek kecokelatan yang kehilangan kilau alami.
6. "Kemerahan" (Erythema / Irritation): Area kemerahan yang meluas/melebar (seperti di pipi, hidung, dagu) akibat iritasi, rosacea, atau barrier kulit yang terganggu.

=== PANDUAN PENENTUAN JENIS KULIT ===
- "Berminyak": Terlihat kilap/kilatan minyak (sebum shine) merata di seluruh wajah (dahi, hidung, pipi, dagu).
- "Kombinasi": Terlihat kilap minyak terutama di area T-Zone (dahi & hidung), sedangkan area pipi cenderung normal/kering.
- "Kering": Kulit tampak kusam, bersisik/flaky, terasa kencang, tanpa kilap minyak sama sekali.
- "Normal": Produksi minyak seimbang, tekstur halus, tidak mengkilap berlebih dan tidak bersisik.

=== ATURAN BOUNDING BOX (box_2d) & CONFIDENCE ===
- Koordinat [ymin, xmin, ymax, xmax] harus PRESISI mengurung HANYA titik/area permasalahan tersebut. Skala [0-1000] relatif terhadap tinggi dan lebar gambar.
- Jangan membuat box_2d raksasa yang mencakup seluruh wajah. Setiap jerawat / noda individu atau area iritasi lokal harus memiliki box_2d masing-masing.
- Berikan skor "confidence" realistis (0.50 - 0.99) berdasarkan kejelasan visual pada gambar.
- Jika kulit bersih tanpa masalah yang jelas, kembalikan array "permasalahan": [].

=== FORMAT OUTPUT (HANYA JSON VALID) ===
Kembalikan respon HANYA berupa JSON valid tanpa markdown, tanpa teks pembuka/penutup.

Struktur JSON:
{
  "jenis_kulit": "Normal" | "Berminyak" | "Kombinasi" | "Kering",
  "permasalahan": [
    {
      "label": "Jerawat" | "PIE" | "PIH" | "Aging" | "Kusam" | "Kemerahan",
      "box_2d": [ymin, xmin, ymax, xmax],
      "confidence": 0.92
    }
  ]
}
"""


def _call_gemini_vision(b64_image: str, mime_type: str) -> dict:
    """
    Panggil Gemini via official SDK (google.generativeai) dengan PIL Image.
    Kembalikan dict hasil parse JSON dari Gemini.
    """
    img_bytes = base64.b64decode(b64_image)
    pil_img = Image.open(io.BytesIO(img_bytes))

    model = genai.GenerativeModel(
        model_name=_GEMINI_MODEL,
        generation_config={"temperature": 0.1, "top_p": 0.95}
    )
    response = model.generate_content([SKIN_ANALYSIS_PROMPT, pil_img])

    raw_text = response.text.strip()
    clean = raw_text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)



def _calculate_skin_score(permasalahan_list: list) -> int:
    """Hitung skin score (0-100) berdasarkan permasalahan yang terdeteksi."""
    score = 100
    for item in permasalahan_list:
        confidence = float(item.get('confidence', 0.5))
        if confidence >= 0.8:
            score -= 15
        elif confidence >= 0.5:
            score -= 10
        else:
            score -= 5
    return max(20, score)


def _derive_acne_level(permasalahan_list: list) -> str:
    """Tentukan status jerawat dari hasil deteksi."""
    jerawat = [p for p in permasalahan_list if p.get('label') == 'Jerawat']
    if not jerawat:
        return 'Bersih'
    return 'Jerawat'


def _derive_oil_level(jenis_kulit: str) -> str:
    """Tentukan oil level dari jenis kulit."""
    mapping = {
        'Berminyak': 'Tinggi — Seluruh Wajah',
        'Kombinasi': 'Sedang — T-Zone',
        'Normal': 'Normal — Seimbang',
        'Kering': 'Rendah — Kering',
    }
    return mapping.get(jenis_kulit, 'Sedang — T-Zone')


def _derive_pore_condition(permasalahan_list: list, jenis_kulit: str) -> str:
    """Tentukan kondisi pori dari permasalahan dan jenis kulit."""
    has_bopeng = any(p.get('label') == 'Bopeng' for p in permasalahan_list)
    if has_bopeng:
        return 'Besar — Terlihat Jelas'
    if jenis_kulit in ('Berminyak', 'Kombinasi'):
        return 'Sedang — Cukup Terlihat'
    return 'Baik — Minimal'

#heh
@app.route("/api/skin-scan", methods=["POST"])
def skin_scan():
    """Analisis kulit menggunakan Gemini AI Vision (via REST API)."""
    try:
        is_mock = False
        if not _GEMINI_API_KEY:
            is_mock = True
            print("[WARN] GEMINI_API_KEY tidak diset. Menggunakan mock analysis.")

        data = request.get_json()
        if not data or 'image' not in data:
            return jsonify({"detail": "Field 'image' (base64) wajib diisi."}), 400

        image_data_url = data['image']
        user_id = data.get('user_id')

        # ── Decode base64 ───────────────────────────────────────────────────
        try:
            mime_type = 'image/jpeg'
            if ',' in image_data_url:
                header, b64_str = image_data_url.split(',', 1)
                if 'png' in header:
                    mime_type = 'image/png'
                elif 'webp' in header:
                    mime_type = 'image/webp'
            else:
                b64_str = image_data_url

            # Validasi base64
            base64.b64decode(b64_str, validate=True)
        except Exception as e:
            return jsonify({"detail": f"Format gambar tidak valid: {str(e)}"}), 400

        # ── Helper for mock analysis ──────────────────────────────────────
        def get_mock_result():
            return {
                "jenis_kulit": "Kombinasi",
                "permasalahan": [
                    {
                        "label": "Jerawat",
                        "deskripsi": "Jerawat kemerahan ringan di area pipi.",
                        "box_2d": [300, 400, 350, 460],
                        "confidence": 0.89
                    },
                    {
                        "label": "Pori-pori Besar",
                        "deskripsi": "Pori-pori tampak melebar di area T-zone.",
                        "box_2d": [450, 480, 520, 560],
                        "confidence": 0.85
                    }
                ]
            }

        # ── Panggil Gemini ────────────────────────────────────────────────
        if is_mock:
            hasil = get_mock_result()
        else:
            try:
                hasil = _call_gemini_vision(b64_str, mime_type)
            except Exception as e:
                print(f"[WARN] Gagal memanggil Gemini API ({str(e)}). Menggunakan fallback mock analysis.")
                hasil = get_mock_result()

        # ── Hitung metrik turunan ───────────────────────────────────────────
        jenis_kulit = hasil.get('jenis_kulit', 'Normal')
        permasalahan_list = hasil.get('permasalahan', [])

        skin_score      = _calculate_skin_score(permasalahan_list)
        acne_level      = _derive_acne_level(permasalahan_list)
        oil_level       = _derive_oil_level(jenis_kulit)
        pore_condition  = _derive_pore_condition(permasalahan_list, jenis_kulit)

        # ── Optional: Update DB user ─────────────────────────────────────────
        if user_id:
            try:
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE users
                        SET skin_type=%s, acne_level=%s, oil_level=%s,
                            pore_condition=%s, skin_score=%s
                        WHERE id=%s
                    """, (jenis_kulit, acne_level, oil_level, pore_condition, skin_score, user_id))
                    conn.commit()
                    cursor.close()
                    conn.close()
            except Exception as e:
                print(f"[WARN] Gagal update skin data user {user_id}: {e}")

        return jsonify({
            "jenis_kulit":      jenis_kulit,
            "permasalahan":     permasalahan_list,
            "skin_score":       skin_score,
            "acne_level":       acne_level,
            "oil_level":        oil_level,
            "pore_condition":   pore_condition,
        }), 200
    except Exception as general_err:
        print(f"[ERROR] Exception in skin_scan route: {general_err}")
        return jsonify({"detail": f"Terjadi kesalahan internal server: {str(general_err)}"}), 500


# ─── Rekomendasi Produk (Berbasis Ingredien) ──────────────────────────────────

@app.route("/api/recommendations", methods=["GET", "POST"])
def get_recommendations():
    """
    Endpoint rekomendasi produk berbasis scoring ingredien.

    Terima parameter (GET query string ATAU POST JSON body):
      jenis_kulit  : str   — "Normal" | "Berminyak" | "Kombinasi" | "Kering"
      permasalahan : str   — JSON array of labels, e.g. '["Jerawat","PIH"]'
                            atau comma-separated, e.g. "Jerawat,PIH"
      kategori     : str   — "cleanser"|"moisturizer"|"serum"|"sunscreen"|"toner"
                            (opsional; kalau kosong kembalikan semua kategori)
      limit        : int   — jumlah produk maksimum (default 50)

    Response JSON:
      { "products": [ { name, kategori, price, image_url, link, texture,
                         score, match, recommended, cocok[], tidak_cocok[] } ] }
    """
    if request.method == "POST":
        data = request.get_json() or {}
    else:
        data = request.args

    jenis_kulit = str(data.get("jenis_kulit", "Normal")).strip()
    kategori    = str(data.get("kategori", "")).strip().lower() or None

    # Terima permasalahan sebagai JSON string atau comma-separated
    raw_masalah = data.get("permasalahan", "[]")
    if isinstance(raw_masalah, list):
        permasalahan = raw_masalah
    else:
        raw_masalah = str(raw_masalah).strip()
        if raw_masalah.startswith("["):
            try:
                permasalahan = json.loads(raw_masalah)
            except (json.JSONDecodeError, ValueError):
                permasalahan = []
        else:
            permasalahan = [p.strip() for p in raw_masalah.split(",") if p.strip()]

    try:
        limit = int(data.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50

    try:
        products = score_products(
            jenis_kulit=jenis_kulit,
            permasalahan_labels=permasalahan,
            kategori_frontend=kategori,
            limit=limit,
        )
        return jsonify({"products": products}), 200
    except Exception as e:
        return jsonify({"detail": f"Gagal menghitung rekomendasi: {str(e)}"}), 500



# ─── Midtrans Payment Endpoints ─────────────────────────────────────────────

@app.route("/api/payment/config", methods=["GET"])
def get_payment_config():
    """Endpoint publik untuk mengambil Client Key Midtrans (aman untuk frontend)."""
    return jsonify({
        "client_key": MIDTRANS_CLIENT_KEY,
        "is_production": MIDTRANS_IS_PRODUCTION,
        "snap_url": 'https://app.midtrans.com/snap/snap.js' if MIDTRANS_IS_PRODUCTION else 'https://app.sandbox.midtrans.com/snap/snap.js'
    }), 200


@app.route("/api/payment/create-transaction", methods=["POST"])
@require_auth
def create_midtrans_transaction():
    """
    Buat transaksi Midtrans Snap untuk langganan Glow Plus.
    Mengembalikan snap_token yang dipakai frontend untuk membuka popup pembayaran.
    """
    user_id = g.current_user_id
    data = request.get_json() or {}

    if not MIDTRANS_SERVER_KEY:
        return jsonify({"detail": "Payment gateway belum dikonfigurasi"}), 503

    # Ambil data user dari database
    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name, email FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({"detail": "User tidak ditemukan"}), 404
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

    # Buat order_id unik
    import time
    order_id = f"BGLOW-{user_id}-{int(time.time())}"
    gross_amount = 30000  # Rp 30.000

    payload = {
        "transaction_details": {
            "order_id": order_id,
            "gross_amount": gross_amount
        },
        "item_details": [{
            "id": "glow_plus_monthly",
            "price": gross_amount,
            "quantity": 1,
            "name": "B-Glow Glow Plus - 1 Bulan",
            "category": "Subscription"
        }],
        "customer_details": {
            "first_name": user['name'],
            "email": user['email']
        },
        "callbacks": {
            "finish": "https://bglow.store/#/subscription"
        }
    }

    try:
        # Encode Server Key ke Base64 untuk Basic Auth
        auth_string = base64.b64encode(f"{MIDTRANS_SERVER_KEY}:".encode()).decode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Basic {auth_string}"
        }

        response = req.post(
            MIDTRANS_SNAP_URL,
            json=payload,
            headers=headers,
            timeout=15
        )
        result = response.json()

        if response.status_code in (200, 201) and 'token' in result:
            print(f"[Midtrans] Snap token dibuat untuk user {user_id}, order: {order_id}")
            return jsonify({
                "snap_token": result['token'],
                "redirect_url": result.get('redirect_url', ''),
                "order_id": order_id
            }), 200
        else:
            print(f"[Midtrans] Error: {result}")
            return jsonify({"detail": result.get('error_messages', ['Gagal membuat transaksi'])[0]}), 400

    except req.exceptions.Timeout:
        return jsonify({"detail": "Timeout koneksi ke Midtrans. Coba lagi."}), 504
    except Exception as e:
        print(f"[Midtrans] Exception: {e}")
        return jsonify({"detail": str(e)}), 500


@app.route("/api/payment/webhook", methods=["POST"])
def midtrans_webhook():
    """
    Webhook Midtrans — dipanggil otomatis oleh server Midtrans setelah pembayaran.
    Verifikasi signature, lalu update status subscription user di database.
    """
    import hashlib

    data = request.get_json()
    if not data:
        return jsonify({"detail": "No data"}), 400

    # ── Verifikasi Signature Key ────────────────────────────────────────────
    order_id       = data.get('order_id', '')
    status_code    = data.get('status_code', '')
    gross_amount   = data.get('gross_amount', '')
    signature_key  = data.get('signature_key', '')

    raw_string = f"{order_id}{status_code}{gross_amount}{MIDTRANS_SERVER_KEY}"
    expected_sig = hashlib.sha512(raw_string.encode()).hexdigest()

    if signature_key != expected_sig:
        print(f"[Midtrans Webhook] ❌ Signature mismatch untuk order {order_id}")
        return jsonify({"detail": "Invalid signature"}), 403

    # ── Parse status transaksi ─────────────────────────────────────────────
    transaction_status = data.get('transaction_status', '')
    fraud_status       = data.get('fraud_status', '')
    payment_type       = data.get('payment_type', '')

    print(f"[Midtrans Webhook] Order: {order_id} | Status: {transaction_status} | Fraud: {fraud_status}")

    # Kondisi pembayaran dianggap berhasil:
    is_success = (
        transaction_status == 'capture' and fraud_status == 'accept'
    ) or (
        transaction_status == 'settlement'
    )

    if not is_success:
        # Bukan pembayaran berhasil (pending, deny, cancel, expire, etc.)
        print(f"[Midtrans Webhook] Transaksi tidak berhasil: {transaction_status}")
        return jsonify({"message": "OK - not a success event"}), 200

    # ── Extract user_id dari order_id ──────────────────────────────────────
    # Format order_id: BGLOW-{user_id}-{timestamp}
    try:
        parts = order_id.split('-')
        user_id = int(parts[1])
    except (IndexError, ValueError):
        print(f"[Midtrans Webhook] Gagal parse user_id dari order_id: {order_id}")
        return jsonify({"detail": "Invalid order_id format"}), 400

    # ── Update subscription di database ───────────────────────────────────
    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        # Simpan status subscription ke kolom users atau tabel terpisah
        # Saat ini kita update field plan di users table
        cursor.execute(
            "UPDATE users SET subscription_plan = %s, subscription_updated_at = NOW() WHERE id = %s",
            ('glow-plus', user_id)
        )
        conn.commit()
        print(f"[Midtrans Webhook] ✅ User {user_id} berhasil diupgrade ke Glow Plus!")
        return jsonify({"message": "Subscription updated successfully"}), 200
    except Exception as e:
        print(f"[Midtrans Webhook] DB Error: {e}")
        # Jika kolom belum ada, kembalikan 200 supaya Midtrans tidak retry terus
        return jsonify({"message": "OK - db update skipped", "detail": str(e)}), 200
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/payment/transaction-status/<order_id>", methods=["GET"])
@require_auth
def get_transaction_status(order_id):
    """
    Cek status transaksi Midtrans berdasarkan order_id.
    Di-poll oleh frontend setiap 3 detik untuk auto-detect pembayaran
    tanpa user perlu klik 'Check status' di popup Snap.
    """
    if not MIDTRANS_SERVER_KEY:
        return jsonify({"detail": "Payment gateway not configured"}), 503

    # Pastikan order_id milik user yang sedang login
    # Format: BGLOW-{user_id}-{timestamp}
    try:
        parts = order_id.split('-')
        owner_id = int(parts[1])
        if owner_id != g.current_user_id:
            return jsonify({"detail": "Akses tidak diizinkan"}), 403
    except (IndexError, ValueError):
        return jsonify({"detail": "Format order_id tidak valid"}), 400

    try:
        auth_string = base64.b64encode(f"{MIDTRANS_SERVER_KEY}:".encode()).decode()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {auth_string}"
        }

        # Midtrans Status API endpoint
        api_base = "https://api.midtrans.com" if MIDTRANS_IS_PRODUCTION else "https://api.sandbox.midtrans.com"
        response = req.get(
            f"{api_base}/v2/{order_id}/status",
            headers=headers,
            timeout=10
        )
        result = response.json()
        print(f"[Midtrans Status] order={order_id} status={result.get('transaction_status')} fraud={result.get('fraud_status')}")
        return jsonify(result), 200

    except req.exceptions.Timeout:
        return jsonify({"detail": "Timeout saat cek status"}), 504
    except Exception as e:
        print(f"[Midtrans Status] Error: {e}")
        return jsonify({"detail": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# COMMUNITY FEED ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'uploads')
os.makedirs(UPLOADS_DIR, exist_ok=True)

ALLOWED_IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
MAX_IMAGE_SIZE_MB = 5


@app.route("/api/upload/image", methods=["POST"])
@require_auth
def upload_image():
    """Upload foto untuk post. Simpan ke /uploads/, return public URL."""
    if 'image' not in request.files:
        return jsonify({"detail": "Tidak ada file image yang dikirim"}), 400

    file = request.files['image']
    if not file.filename:
        return jsonify({"detail": "Nama file kosong"}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_IMAGE_EXTS:
        return jsonify({"detail": f"Format tidak didukung. Gunakan: {', '.join(ALLOWED_IMAGE_EXTS)}"}), 400

    # Baca data & cek ukuran
    data = file.read()
    if len(data) > MAX_IMAGE_SIZE_MB * 1024 * 1024:
        return jsonify({"detail": f"Ukuran file maks {MAX_IMAGE_SIZE_MB}MB"}), 413

    # Resize & compress dengan Pillow
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert('RGB')
        max_dim = 1200
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)

        filename = f"post_{g.current_user_id}_{secrets.token_hex(8)}.jpg"
        save_path = os.path.join(UPLOADS_DIR, filename)
        img.save(save_path, 'JPEG', quality=85, optimize=True)
    except Exception as e:
        return jsonify({"detail": f"Gagal memproses gambar: {e}"}), 500

    # Return URL relatif — nginx serve /uploads/
    image_url = f"/uploads/{filename}"
    return jsonify({"image_url": image_url}), 201


@app.route("/api/feed", methods=["GET"])
@require_auth
def get_feed():
    """Ambil semua post (terbaru duluan), dengan jumlah like & status liked user ini."""
    page  = max(1, int(request.args.get('page', 1)))
    limit = min(30, int(request.args.get('limit', 20)))
    offset = (page - 1) * limit

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                p.id,
                p.content,
                p.image_url,
                p.created_at,
                u.id AS user_id,
                u.name AS user_name,
                u.skin_type,
                u.profile_photo,
                COUNT(DISTINCT pl.id)  AS like_count,
                COUNT(DISTINCT pc.id)  AS comment_count,
                MAX(CASE WHEN pl2.user_id = %s THEN 1 ELSE 0 END) AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN post_likes    pl  ON pl.post_id  = p.id
            LEFT JOIN post_comments pc  ON pc.post_id  = p.id
            LEFT JOIN post_likes    pl2 ON pl2.post_id = p.id AND pl2.user_id = %s
            GROUP BY p.id, p.content, p.image_url, p.created_at, u.id, u.name, u.skin_type, u.profile_photo
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, (g.current_user_id, g.current_user_id, limit, offset))
        posts = cursor.fetchall()

        # Serialisasi datetime
        for post in posts:
            if isinstance(post.get('created_at'), datetime):
                post['created_at'] = post['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')
            post['liked_by_me'] = bool(post.get('liked_by_me'))

        # Total count untuk pagination
        cursor.execute("SELECT COUNT(*) AS total FROM posts")
        total = cursor.fetchone()['total']

        return jsonify({
            "posts": posts,
            "page": page,
            "limit": limit,
            "total": total,
            "has_more": offset + limit < total
        })
    except Exception as e:
        print(f"[Feed] Error get_feed: {e}")
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/posts", methods=["POST"])
@require_auth
def create_post():
    """Buat post baru. Body: { content, image_url? }"""
    data = request.get_json(silent=True) or {}
    content   = (data.get('content') or '').strip()
    image_url = (data.get('image_url') or '').strip() or None

    if not content and not image_url:
        return jsonify({"detail": "Konten post tidak boleh kosong"}), 400
    if len(content) > 1000:
        return jsonify({"detail": "Konten maksimal 1000 karakter"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "INSERT INTO posts (user_id, content, image_url) VALUES (%s, %s, %s)",
            (g.current_user_id, content, image_url)
        )
        conn.commit()
        post_id = cursor.lastrowid

        # Return post yang baru dibuat beserta data user
        cursor.execute("""
            SELECT p.id, p.content, p.image_url, p.created_at,
                   u.id AS user_id, u.name AS user_name, u.skin_type
            FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.id = %s
        """, (post_id,))
        post = cursor.fetchone()
        if post and isinstance(post.get('created_at'), datetime):
            post['created_at'] = post['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')
        post['like_count']   = 0
        post['liked_by_me']  = False

        return jsonify(post), 201
    except Exception as e:
        print(f"[Feed] Error create_post: {e}")
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/posts/<int:post_id>", methods=["DELETE"])
@require_auth
def delete_post(post_id):
    """Hapus post milik user yang sedang login."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT user_id, image_url FROM posts WHERE id = %s", (post_id,))
        post = cursor.fetchone()
        if not post:
            return jsonify({"detail": "Post tidak ditemukan"}), 404
        if post['user_id'] != g.current_user_id:
            return jsonify({"detail": "Anda tidak berhak menghapus post ini"}), 403

        # Hapus file gambar jika ada
        if post.get('image_url'):
            fname = post['image_url'].lstrip('/uploads/')
            fpath = os.path.join(UPLOADS_DIR, fname)
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except Exception:
                pass

        cursor.execute("DELETE FROM posts WHERE id = %s", (post_id,))
        conn.commit()
        return jsonify({"message": "Post berhasil dihapus"}), 200
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/posts/<int:post_id>/like", methods=["POST"])
@require_auth
def toggle_like(post_id):
    """Toggle like/unlike sebuah post."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Cek apakah sudah like
        cursor.execute(
            "SELECT id FROM post_likes WHERE post_id = %s AND user_id = %s",
            (post_id, g.current_user_id)
        )
        existing = cursor.fetchone()

        if existing:
            # Unlike
            cursor.execute(
                "DELETE FROM post_likes WHERE post_id = %s AND user_id = %s",
                (post_id, g.current_user_id)
            )
            liked = False
        else:
            # Like
            cursor.execute(
                "INSERT INTO post_likes (post_id, user_id) VALUES (%s, %s)",
                (post_id, g.current_user_id)
            )
            liked = True

        conn.commit()

        # Ambil total like terbaru
        cursor.execute("SELECT COUNT(*) AS cnt FROM post_likes WHERE post_id = %s", (post_id,))
        total = cursor.fetchone()['cnt']

        return jsonify({"liked": liked, "like_count": total})
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


# ─── Product Reviews ───────────────────────────────────────────────────────────

@app.route("/api/products/<path:product_name>/reviews", methods=["GET"])
@require_auth
def get_product_reviews(product_name):
    """Ambil semua review untuk produk tertentu (by name)."""
    product_name = unquote(product_name).strip()
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT r.id, r.rating, r.review_text, r.created_at,
                   u.id AS user_id, u.name AS user_name, u.skin_type
            FROM product_reviews r
            JOIN users u ON u.id = r.user_id
            WHERE r.product_name = %s
            ORDER BY r.created_at DESC
            LIMIT 50
        """, (product_name,))
        reviews = cursor.fetchall()
        for r in reviews:
            if isinstance(r.get('created_at'), datetime):
                r['created_at'] = r['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')

        # Hitung rata-rata rating
        avg_rating = round(sum(r['rating'] for r in reviews) / len(reviews), 1) if reviews else 0

        # Cek apakah user ini sudah pernah review produk ini
        cursor.execute(
            "SELECT id FROM product_reviews WHERE product_name = %s AND user_id = %s",
            (product_name, g.current_user_id)
        )
        already_reviewed = cursor.fetchone() is not None

        return jsonify({
            "reviews": reviews,
            "avg_rating": avg_rating,
            "total": len(reviews),
            "already_reviewed": already_reviewed
        })
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/products/<path:product_name>/reviews", methods=["POST"])
@require_auth
def add_product_review(product_name):
    """Tambah review produk. Satu user hanya bisa review satu produk sekali."""
    product_name = unquote(product_name).strip()
    data = request.get_json(silent=True) or {}
    rating      = data.get('rating')
    review_text = (data.get('review_text') or '').strip()

    if not rating or not isinstance(rating, int) or rating < 1 or rating > 5:
        return jsonify({"detail": "Rating harus antara 1 - 5"}), 400
    if len(review_text) > 500:
        return jsonify({"detail": "Review maksimal 500 karakter"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Cegah double review
        cursor.execute(
            "SELECT id FROM product_reviews WHERE product_name = %s AND user_id = %s",
            (product_name, g.current_user_id)
        )
        if cursor.fetchone():
            return jsonify({"detail": "Anda sudah pernah mereview produk ini"}), 409

        cursor.execute(
            "INSERT INTO product_reviews (user_id, product_name, rating, review_text) VALUES (%s, %s, %s, %s)",
            (g.current_user_id, product_name, rating, review_text or None)
        )
        conn.commit()
        review_id = cursor.lastrowid

        cursor.execute("""
            SELECT r.id, r.rating, r.review_text, r.created_at,
                   u.id AS user_id, u.name AS user_name, u.skin_type
            FROM product_reviews r JOIN users u ON u.id = r.user_id
            WHERE r.id = %s
        """, (review_id,))
        review = cursor.fetchone()
        if review and isinstance(review.get('created_at'), datetime):
            review['created_at'] = review['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')

        return jsonify(review), 201
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


# ─── Post Comments ─────────────────────────────────────────────────────────────

@app.route("/api/posts/<int:post_id>/comments", methods=["GET"])
@require_auth
def get_comments(post_id):
    """Ambil semua komentar dengan replies & like count."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT c.id, c.content, c.created_at, c.parent_id,
                   u.id AS user_id, u.name AS user_name, u.profile_photo,
                   COUNT(DISTINCT cl.id) AS like_count,
                   MAX(CASE WHEN cl2.user_id = %s THEN 1 ELSE 0 END) AS liked_by_me
            FROM post_comments c
            JOIN users u ON u.id = c.user_id
            LEFT JOIN comment_likes cl  ON cl.comment_id  = c.id
            LEFT JOIN comment_likes cl2 ON cl2.comment_id = c.id AND cl2.user_id = %s
            WHERE c.post_id = %s
            GROUP BY c.id, c.content, c.created_at, c.parent_id, u.id, u.name, u.profile_photo
            ORDER BY c.created_at ASC
            LIMIT 200
        """, (g.current_user_id, g.current_user_id, post_id))
        rows = cursor.fetchall()

        # Build nested structure: top-level + replies
        top = []
        by_id = {}
        for c in rows:
            if isinstance(c.get('created_at'), datetime):
                c['created_at'] = c['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')
            c['liked_by_me'] = bool(c.get('liked_by_me'))
            c['replies'] = []
            by_id[c['id']] = c

        for c in rows:
            if c.get('parent_id') and c['parent_id'] in by_id:
                by_id[c['parent_id']]['replies'].append(c)
            else:
                top.append(c)

        cursor.execute("SELECT COUNT(*) AS cnt FROM post_comments WHERE post_id = %s", (post_id,))
        total = cursor.fetchone()['cnt']
        return jsonify({"comments": top, "total": total})
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/posts/<int:post_id>/comments", methods=["POST"])
@require_auth
def add_comment(post_id):
    """Tambah komentar (atau reply) ke sebuah post."""
    data = request.get_json(silent=True) or {}
    content   = (data.get('content') or '').strip()
    parent_id = data.get('parent_id')  # optional: reply to comment
    if not content:
        return jsonify({"detail": "Komentar tidak boleh kosong"}), 400
    if len(content) > 500:
        return jsonify({"detail": "Komentar maksimal 500 karakter"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM posts WHERE id = %s", (post_id,))
        if not cursor.fetchone():
            return jsonify({"detail": "Post tidak ditemukan"}), 404

        # Validate parent_id
        if parent_id:
            cursor.execute("SELECT id FROM post_comments WHERE id = %s AND post_id = %s", (parent_id, post_id))
            if not cursor.fetchone():
                parent_id = None

        cursor.execute(
            "INSERT INTO post_comments (post_id, user_id, content, parent_id) VALUES (%s, %s, %s, %s)",
            (post_id, g.current_user_id, content, parent_id)
        )
        conn.commit()
        comment_id = cursor.lastrowid

        cursor.execute("""
            SELECT c.id, c.content, c.created_at, c.parent_id,
                   u.id AS user_id, u.name AS user_name,
                   0 AS like_count, 0 AS liked_by_me
            FROM post_comments c JOIN users u ON u.id = c.user_id
            WHERE c.id = %s
        """, (comment_id,))
        comment = cursor.fetchone()
        if comment and isinstance(comment.get('created_at'), datetime):
            comment['created_at'] = comment['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')
        comment['replies'] = []
        comment['liked_by_me'] = False

        cursor.execute("SELECT COUNT(*) AS cnt FROM post_comments WHERE post_id = %s", (post_id,))
        comment['post_comment_count'] = cursor.fetchone()['cnt']

        return jsonify(comment), 201
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


# ─── Comment Like Toggle ───────────────────────────────────────────────────────

@app.route("/api/comments/<int:comment_id>/like", methods=["POST"])
@require_auth
def toggle_comment_like(comment_id):
    """Toggle like pada komentar."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM post_comments WHERE id = %s", (comment_id,))
        if not cursor.fetchone():
            return jsonify({"detail": "Komentar tidak ditemukan"}), 404

        cursor.execute(
            "SELECT id FROM comment_likes WHERE comment_id = %s AND user_id = %s",
            (comment_id, g.current_user_id)
        )
        existing = cursor.fetchone()
        if existing:
            cursor.execute("DELETE FROM comment_likes WHERE comment_id = %s AND user_id = %s",
                           (comment_id, g.current_user_id))
            liked = False
        else:
            cursor.execute("INSERT INTO comment_likes (comment_id, user_id) VALUES (%s, %s)",
                           (comment_id, g.current_user_id))
            liked = True
        conn.commit()

        cursor.execute("SELECT COUNT(*) AS cnt FROM comment_likes WHERE comment_id = %s", (comment_id,))
        like_count = cursor.fetchone()['cnt']
        return jsonify({"liked": liked, "like_count": like_count})
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


# ─── User's Own Posts (for Profile page) ─────────────────────────────────────

@app.route("/api/users/me/posts", methods=["GET"])
@require_auth
def get_my_posts():
    """Ambil semua post milik user yang sedang login."""
    page  = max(1, int(request.args.get('page', 1)))
    limit = min(30, int(request.args.get('limit', 20)))
    offset = (page - 1) * limit
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.id, p.content, p.image_url, p.created_at,
                   u.name AS user_name, u.profile_photo,
                   COUNT(DISTINCT pl.id) AS like_count,
                   COUNT(DISTINCT pc.id) AS comment_count
            FROM posts p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN post_likes pl ON pl.post_id = p.id
            LEFT JOIN post_comments pc ON pc.post_id = p.id
            WHERE p.user_id = %s
            GROUP BY p.id, p.content, p.image_url, p.created_at, u.name, u.profile_photo
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, (g.current_user_id, limit, offset))
        posts = cursor.fetchall()
        for p in posts:
            if isinstance(p.get('created_at'), datetime):
                p['created_at'] = p['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')

        cursor.execute("SELECT COUNT(*) AS total FROM posts WHERE user_id = %s", (g.current_user_id,))
        total = cursor.fetchone()['total']

        return jsonify({"posts": posts, "total": total, "has_more": offset + limit < total})
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/users/me/liked", methods=["GET"])
@require_auth
def get_my_liked_posts():
    """Ambil semua post yang di-like oleh user yang sedang login."""
    page  = max(1, int(request.args.get('page', 1)))
    limit = min(30, int(request.args.get('limit', 20)))
    offset = (page - 1) * limit
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.id, p.content, p.image_url, p.created_at,
                   u.name AS user_name, u.skin_type, u.profile_photo,
                   COUNT(DISTINCT pl2.id) AS like_count,
                   COUNT(DISTINCT pc.id) AS comment_count
            FROM post_likes pl
            JOIN posts p ON p.id = pl.post_id
            JOIN users u ON u.id = p.user_id
            LEFT JOIN post_likes pl2 ON pl2.post_id = p.id
            LEFT JOIN post_comments pc ON pc.post_id = p.id
            WHERE pl.user_id = %s
            GROUP BY p.id, p.content, p.image_url, p.created_at, u.name, u.skin_type, u.profile_photo
            ORDER BY pl.created_at DESC
            LIMIT %s OFFSET %s
        """, (g.current_user_id, limit, offset))
        posts = cursor.fetchall()
        for p in posts:
            if isinstance(p.get('created_at'), datetime):
                p['created_at'] = p['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')
        cursor.execute("""
            SELECT COUNT(*) AS total FROM post_likes WHERE user_id = %s
        """, (g.current_user_id,))
        total = cursor.fetchone()['total']
        return jsonify({"posts": posts, "total": total, "has_more": offset + limit < total})
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


# ─── Follow System ─────────────────────────────────────────────────────────────

@app.route("/api/users/<int:user_id>/follow", methods=["POST"])
@require_auth
def toggle_follow(user_id):
    """Toggle follow/unfollow seorang user."""
    if user_id == g.current_user_id:
        return jsonify({"detail": "Tidak bisa follow diri sendiri"}), 400
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({"detail": "User tidak ditemukan"}), 404

        cursor.execute(
            "SELECT id FROM follows WHERE follower_id = %s AND following_id = %s",
            (g.current_user_id, user_id)
        )
        existing = cursor.fetchone()
        if existing:
            cursor.execute("DELETE FROM follows WHERE follower_id = %s AND following_id = %s",
                           (g.current_user_id, user_id))
            following = False
        else:
            cursor.execute("INSERT INTO follows (follower_id, following_id) VALUES (%s, %s)",
                           (g.current_user_id, user_id))
            following = True
        conn.commit()

        cursor.execute("SELECT COUNT(*) AS cnt FROM follows WHERE following_id = %s", (user_id,))
        follower_count = cursor.fetchone()['cnt']
        return jsonify({"following": following, "follower_count": follower_count})
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route("/api/feed/following", methods=["GET"])
@require_auth
def get_following_feed():
    """Feed dari user yang di-follow."""
    page  = max(1, int(request.args.get('page', 1)))
    limit = min(20, int(request.args.get('limit', 10)))
    offset = (page - 1) * limit
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.id, p.content, p.image_url, p.created_at,
                   u.id AS user_id, u.name AS user_name, u.skin_type, u.profile_photo,
                   COUNT(DISTINCT pl.id)  AS like_count,
                   COUNT(DISTINCT pc.id)  AS comment_count,
                   MAX(CASE WHEN pl2.user_id = %s THEN 1 ELSE 0 END) AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            JOIN follows f ON f.following_id = p.user_id AND f.follower_id = %s
            LEFT JOIN post_likes    pl  ON pl.post_id  = p.id
            LEFT JOIN post_comments pc  ON pc.post_id  = p.id
            LEFT JOIN post_likes    pl2 ON pl2.post_id = p.id AND pl2.user_id = %s
            GROUP BY p.id, p.content, p.image_url, p.created_at, u.id, u.name, u.skin_type, u.profile_photo
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, (g.current_user_id, g.current_user_id, g.current_user_id, limit, offset))
        posts = cursor.fetchall()
        for p in posts:
            if isinstance(p.get('created_at'), datetime):
                p['created_at'] = p['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')
            p['liked_by_me'] = bool(p.get('liked_by_me'))

        cursor.execute("""
            SELECT COUNT(*) AS total FROM posts p
            JOIN follows f ON f.following_id = p.user_id AND f.follower_id = %s
        """, (g.current_user_id,))
        total = cursor.fetchone()['total']
        return jsonify({"posts": posts, "has_more": offset + limit < total, "total": total})
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


# ─── Single Post Detail ────────────────────────────────────────────────────────

@app.route("/api/posts/<int:post_id>", methods=["GET"])
@require_auth
def get_single_post(post_id):
    """Ambil satu post lengkap dengan info user dan like status."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.id, p.content, p.image_url, p.created_at,
                   u.id AS user_id, u.name AS user_name,
                   u.skin_type, u.profile_photo,
                   COUNT(DISTINCT pl.id) AS like_count,
                   COUNT(DISTINCT pc.id) AS comment_count,
                   MAX(CASE WHEN pl2.user_id = %s THEN 1 ELSE 0 END) AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN post_likes pl ON pl.post_id = p.id
            LEFT JOIN post_comments pc ON pc.post_id = p.id
            LEFT JOIN post_likes pl2 ON pl2.post_id = p.id AND pl2.user_id = %s
            WHERE p.id = %s
            GROUP BY p.id, p.content, p.image_url, p.created_at, u.id, u.name, u.skin_type, u.profile_photo
        """, (g.current_user_id, g.current_user_id, post_id))
        post = cursor.fetchone()
        if not post:
            return jsonify({"detail": "Post tidak ditemukan"}), 404
        if isinstance(post.get('created_at'), datetime):
            post['created_at'] = post['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')
        post['liked_by_me'] = bool(post.get('liked_by_me'))
        return jsonify(post)
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


# ─── Public User Profile ───────────────────────────────────────────────────────

@app.route("/api/users/<int:user_id>/profile", methods=["GET"])
@require_auth
def get_public_user_profile(user_id):
    """Ambil profil publik user: info + follow stats + posts."""
    page  = max(1, int(request.args.get('page', 1)))
    limit = min(30, int(request.args.get('limit', 20)))
    offset = (page - 1) * limit
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, name, skin_type, acne_level, oil_level, pore_condition, skin_problems,
                   profile_photo, cover_photo
            FROM users WHERE id = %s
        """, (user_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({"detail": "User tidak ditemukan"}), 404

        # Follow counts
        cursor.execute("SELECT COUNT(*) AS cnt FROM follows WHERE following_id = %s", (user_id,))
        follower_count = cursor.fetchone()['cnt']
        cursor.execute("SELECT COUNT(*) AS cnt FROM follows WHERE follower_id = %s", (user_id,))
        following_count = cursor.fetchone()['cnt']
        cursor.execute(
            "SELECT id FROM follows WHERE follower_id = %s AND following_id = %s",
            (g.current_user_id, user_id)
        )
        is_following = bool(cursor.fetchone())

        cursor.execute("SELECT COUNT(*) AS total FROM posts WHERE user_id = %s", (user_id,))
        post_count = cursor.fetchone()['total']

        cursor.execute("""
            SELECT p.id, p.content, p.image_url, p.created_at,
                   COUNT(DISTINCT pl.id) AS like_count,
                   COUNT(DISTINCT pc.id) AS comment_count,
                   MAX(CASE WHEN pl2.user_id = %s THEN 1 ELSE 0 END) AS liked_by_me
            FROM posts p
            LEFT JOIN post_likes pl ON pl.post_id = p.id
            LEFT JOIN post_comments pc ON pc.post_id = p.id
            LEFT JOIN post_likes pl2 ON pl2.post_id = p.id AND pl2.user_id = %s
            WHERE p.user_id = %s
            GROUP BY p.id, p.content, p.image_url, p.created_at
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, (g.current_user_id, g.current_user_id, user_id, limit, offset))
        posts = cursor.fetchall()
        for p in posts:
            if isinstance(p.get('created_at'), datetime):
                p['created_at'] = p['created_at'].strftime('%Y-%m-%dT%H:%M:%S+07:00')
            p['liked_by_me'] = bool(p.get('liked_by_me'))
            p['user_name'] = user['name']
            p['user_id'] = user_id
            p['skin_type'] = user['skin_type']

        return jsonify({
            "user": user,
            "post_count": post_count,
            "follower_count": follower_count,
            "following_count": following_count,
            "is_following": is_following,
            "is_own_profile": (user_id == g.current_user_id),
            "posts": posts,
            "has_more": offset + limit < post_count
        })
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


if __name__ == "__main__":
    # PRODUCTION: debug=False
    # Untuk production, gunakan: gunicorn -w 4 -b 0.0.0.0:5050 main:app
    app.run(host="0.0.0.0", debug=False, port=5050)

