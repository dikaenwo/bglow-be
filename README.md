# B-Glow Backend

Backend service untuk aplikasi B-Glow, dibangun dengan Python + Flask + Gunicorn.

## Tech Stack
- **Framework:** Flask
- **WSGI Server:** Gunicorn (production)
- **Database:** MySQL (`mysql-connector-python`)
- **Auth:** JWT
- **AI:** Google Gemini Vision API

---

## Setup & Instalasi

### 1. Clone & Virtual Environment
```bash
git clone https://github.com/dikaenwo/bglow-be.git
cd bglow-be
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Konfigurasi Environment
```bash
cp .env.example .env
# Edit .env: isi DB_HOST, DB_USER, DB_PASSWORD, JWT_SECRET, GEMINI_API_KEY
```

### 4. Inisialisasi Database
```bash
python init_db.py
```

---

## Menjalankan Server

### Mode Development (lokal)
```bash
python main.py
# Server berjalan di http://localhost:5050
```

### Mode Production — Background dengan nohup (Simple)
```bash
bash start.sh      # Jalankan di background
bash stop.sh       # Hentikan server
```
> Log tersimpan di `bglow.log`

### Mode Production — Systemd Service (Recommended, auto-start setelah reboot)
```bash
# 1. Sesuaikan path di bglow.service (WorkingDirectory, User)
# 2. Salin ke systemd
sudo cp bglow.service /etc/systemd/system/bglow.service
sudo mkdir -p /var/log/bglow

# 3. Aktifkan & jalankan
sudo systemctl daemon-reload
sudo systemctl enable bglow
sudo systemctl start bglow

# 4. Cek status
sudo systemctl status bglow

# 5. Lihat log
sudo journalctl -u bglow -f
```

### Perintah Berguna
```bash
sudo systemctl restart bglow     # Restart server
sudo systemctl stop bglow        # Hentikan server
sudo systemctl status bglow      # Cek status
```

---

## ⚠️ Penting: Mode Flask

| Mode | Command | Kapan Digunakan |
|------|---------|----------------|
| **Development** | `python main.py` | Lokal/testing saja |
| **Production** | `gunicorn -w 4 -b 0.0.0.0:5050 main:app` | Server live / VPS |

> `debug=True` **JANGAN** digunakan di production — ekspos traceback error ke publik.

---

## API Endpoints

| Method | Endpoint | Keterangan |
|--------|----------|-----------|
| POST | `/api/register` | Registrasi user |
| POST | `/api/login` | Login + JWT |
| POST | `/api/forgot-password` | Kirim OTP |
| POST | `/api/reset-password` | Reset password |
| POST | `/api/social-login` | Login Google/sosial |
| GET | `/api/user/<id>` | Ambil profil user |
| PUT | `/api/user/<id>` | Update profil user |
| POST | `/api/skin-scan` | Analisis kulit (Gemini AI) |
| GET/POST | `/api/recommendations` | Rekomendasi produk (WSM) |
| GET | `/api/bpom-history/<id>` | Riwayat cek BPOM |
| POST | `/api/bpom-history` | Tambah riwayat BPOM |
| GET/POST | `/api/scan-bpom` | Cek BPOM registry |
