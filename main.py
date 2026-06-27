import os
import jwt
import secrets
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

# Muat .env jika ada (untuk development lokal)
load_dotenv()

app = Flask(__name__)

# ─── CORS ───────────────────────────────────────────────────────────────────
# Di production, ganti dengan origin spesifik via env var CORS_ORIGINS
_cors_origins = os.environ.get('CORS_ORIGINS', '*')
CORS(app, resources={r"/api/*": {"origins": _cors_origins}})

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
        'sub': user_id,                                          # Subject (user id)
        'iat': datetime.now(timezone.utc),                       # Issued at
        'exp': datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS)  # Expiry
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode dan verifikasi JWT. Return payload dict, atau None jika invalid."""
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
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

        g.current_user_id = payload['sub']
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


@app.route("/api/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json()
    if not data or 'email' not in data:
        return jsonify({"detail": "Email wajib diisi"}), 400

    email = data['email']
    conn = get_db_connection()
    if not conn:
        return jsonify({"detail": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        db_user = cursor.fetchone()

        if not db_user:
            return jsonify({"detail": "Email tidak terdaftar"}), 400

        # Return mock OTP 1234
        return jsonify({
            "message": "Kode OTP telah dikirim (Mock)",
            "otp": "1234",
            "email": email
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
            SELECT id, name, email, profile_photo, skin_type, acne_level, oil_level, pore_condition, skin_score, sunscreen_interval 
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
            'name', 'email', 'profile_photo', 'skin_type',
            'acne_level', 'oil_level', 'pore_condition', 'skin_score',
            'sunscreen_interval'
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
            SELECT id, name, email, profile_photo, skin_type, acne_level, oil_level, pore_condition, skin_score, sunscreen_interval 
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
                row['created_at'] = row['created_at'].isoformat()

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


# ─── Gemini AI Skin Scan (via REST API) ───────────────────────────────────────

_GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
_GEMINI_MODEL = 'gemini-3.1-flash-lite'
_GEMINI_REST_URL = (
    'https://generativelanguage.googleapis.com/v1beta/models/'
    f'{_GEMINI_MODEL}:generateContent'
)

if not _GEMINI_API_KEY:
    print("[WARN] GEMINI_API_KEY tidak diset. Endpoint /api/skin-scan tidak akan berfungsi.")


SKIN_ANALYSIS_PROMPT = """\
Anda adalah dermatologis profesional yang menganalisis kondisi kulit wajah.
Analisis gambar ini dengan cermat.
Kembalikan respons HANYA dalam format JSON valid tanpa teks tambahan, tanpa markdown, tanpa penjelasan.

Struktur JSON yang harus dikembalikan:
{
  "jenis_kulit": "Normal" | "Berminyak" | "Kombinasi" | "Kering",
  "permasalahan": [
    {
      "label": "Jerawat" | "PIE" | "PIH" | "Bopeng" | "Hiperpigmentasi" | "Kemerahan",
      "box_2d": [ymin, xmin, ymax, xmax],
      "confidence": 0.0
    }
  ]
}

Catatan:
- box_2d menggunakan skala 0-1000 relatif terhadap ukuran gambar (bukan piksel)
- confidence adalah nilai 0.0 hingga 1.0
- Jika kulit bersih tanpa masalah, kembalikan array permasalahan kosong []
- Deteksi SEMUA area permasalahan kulit yang terlihat
"""


def _call_gemini_vision(b64_image: str, mime_type: str) -> dict:
    """
    Panggil Gemini REST API generateContent dengan gambar inline (base64).
    Kembalikan dict hasil parse JSON dari Gemini.
    """
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": SKIN_ANALYSIS_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_image
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
        }
    }

    resp = req.post(
        _GEMINI_REST_URL,
        params={'key': _GEMINI_API_KEY},
        json=payload,
        timeout=60,
        headers={'Content-Type': 'application/json'}
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")

    resp_data = resp.json()
    try:
        raw_text = resp_data['candidates'][0]['content']['parts'][0]['text'].strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Respons Gemini tidak terduga: {str(resp_data)[:200]}")

    # Bersihkan markdown fence jika ada
    clean = raw_text
    if clean.startswith('```'):
        lines = clean.split('\n')
        # Buang baris pertama (```json atau ```) dan terakhir (```)
        inner = lines[1:] if len(lines) > 1 else lines
        if inner and inner[-1].strip() == '```':
            inner = inner[:-1]
        clean = '\n'.join(inner)
    clean = clean.replace('```json', '').replace('```', '').strip()

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
    """Tentukan acne level dari hasil deteksi."""
    jerawat = [p for p in permasalahan_list if p.get('label') == 'Jerawat']
    if not jerawat:
        return 'Tidak Ada'
    avg_conf = sum(float(p.get('confidence', 0.5)) for p in jerawat) / len(jerawat)
    if avg_conf >= 0.8:
        return 'Parah — Grade 3'
    elif avg_conf >= 0.6:
        return 'Sedang — Grade 2'
    else:
        return 'Ringan — Grade 1'


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


@app.route("/api/skin-scan", methods=["POST"])
def skin_scan():
    """Analisis kulit menggunakan Gemini AI Vision (via REST API).

    Body JSON:
      image    : str  -- Data URL base64 (data:image/jpeg;base64,...) atau base64 murni
      user_id  : int  -- (opsional) ID user untuk auto-update DB

    Response JSON:
      jenis_kulit, permasalahan[], skin_score, acne_level, oil_level, pore_condition
    """
    if not _GEMINI_API_KEY:
        return jsonify({"detail": "Gemini API Key tidak dikonfigurasi di server."}), 503

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

    # ── Panggil Gemini ────────────────────────────────────────────────
    try:
        hasil = _call_gemini_vision(b64_str, mime_type)
    except json.JSONDecodeError as e:
        return jsonify({"detail": f"Gemini mengembalikan format tidak valid: {str(e)}"}), 502
    except RuntimeError as e:
        return jsonify({"detail": str(e)}), 502
    except Exception as e:
        return jsonify({"detail": f"Gagal menganalisis dengan Gemini: {str(e)}"}), 500

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5050)