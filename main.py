import os
import jwt
import secrets
from typing import Optional
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection
from dotenv import load_dotenv
import json

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
        nomor_bpom = data.get('query')
    else:
        nomor_bpom = request.args.get('na') or request.args.get('query')

    if not nomor_bpom:
        return jsonify({"detail": "Nomor registrasi/query tidak boleh kosong"}), 400

    try:
        # TODO: Jalankan fungsi/modul scraping BPOM di sini
        hasil_scraping = {
            "results": [
                {
                    "nomor_registrasi": nomor_bpom,
                    "nama_produk": "B-Glow Skincare Serum (Mock)",
                    "pendaftar": "PT. Glow Kosmetik Indonesia",
                    "merek": "B-Glow",
                    "status_bpom": "TERDAFTAR / AKTIF"
                }
            ]
        }
        return jsonify(hasil_scraping), 200

    except Exception as e:
        return jsonify({"detail": f"Gagal memindai data: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5050)