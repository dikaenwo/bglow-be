#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# B-Glow Backend — Stop Script
# Jalankan: bash stop.sh
# ─────────────────────────────────────────────────────────────────

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/bglow.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "[B-Glow] Server tidak sedang berjalan (PID file tidak ditemukan)."
  exit 0
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
  echo "[B-Glow] Menghentikan server (PID: $PID)..."
  kill "$PID"
  sleep 1
  rm -f "$PID_FILE"
  echo "[B-Glow] ✅ Server berhasil dihentikan."
else
  echo "[B-Glow] Proses PID $PID tidak ditemukan. Membersihkan PID file..."
  rm -f "$PID_FILE"
fi
