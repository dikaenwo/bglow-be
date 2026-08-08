#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# B-Glow Backend — Production Startup Script
# Jalankan: bash start.sh
# Stop:     bash stop.sh
# ─────────────────────────────────────────────────────────────────

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/bglow.pid"
LOG_FILE="$APP_DIR/bglow.log"

# Aktifkan virtual environment jika ada
if [ -f "$APP_DIR/venv/bin/activate" ]; then
  source "$APP_DIR/venv/bin/activate"
fi

# Matikan proses lama jika masih berjalan
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[B-Glow] Menghentikan proses lama (PID: $OLD_PID)..."
    kill "$OLD_PID"
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

echo "[B-Glow] Memulai server di background..."

# Jalankan gunicorn di background
# -w 4  → 4 worker processes (sesuaikan dengan jumlah CPU)
# -b    → bind port 5050
# --timeout 120 → timeout 2 menit (penting untuk Gemini AI / long requests)
nohup gunicorn \
  -w 4 \
  -b 0.0.0.0:5050 \
  --timeout 120 \
  --access-logfile "$LOG_FILE" \
  --error-logfile "$LOG_FILE" \
  --log-level info \
  --pid "$PID_FILE" \
  main:app \
  >> "$LOG_FILE" 2>&1 &

# Simpan PID
echo $! > "$PID_FILE"

sleep 2

if kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
  echo "[B-Glow] ✅ Server berjalan! PID: $(cat $PID_FILE)"
  echo "[B-Glow] Log: $LOG_FILE"
  echo "[B-Glow] Port: 5050"
else
  echo "[B-Glow] ❌ Server gagal dimulai. Cek log: $LOG_FILE"
fi
