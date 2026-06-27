"""
recommender.py — Modul sistem rekomendasi produk skincare B-Glow
Menggunakan scoring berbasis ingredien dengan pembobotan posisi.

Algoritma scoring per produk:
  - Posisi ingredien di 20% pertama  → weight = 1.0
  - Posisi ingredien di 20–50%       → weight = 0.5
  - Posisi ingredien di 50% terakhir → weight = 0.2

  score += 1.0 × weight   jika match ingredien cocok jenis kulit
  score += 0.8 × weight   jika match ingredien cocok masalah kulit (bonus)
  score -= 2.0 × weight   jika match ingredien TIDAK cocok jenis kulit
  score -= 1.5 × weight   jika match ingredien TIDAK cocok masalah kulit
"""

from __future__ import annotations
import os
import re
import pandas as pd

# ─── Mapping nama masalah kulit dari SkinScan AI → nama di dataset ──────────
PROBLEM_LABEL_MAP = {
    'Jerawat':         'Jerawat',
    'PIE':             'Bekas Jerawat',
    'PIH':             'Hiperpigmentasi',
    'Bopeng':          'Bekas Jerawat',
    'Hiperpigmentasi': 'Hiperpigmentasi',
    'Kemerahan':       'Sensitif',
}

# ─── Mapping kategori frontend → nama kolom di dataset ───────────────────────
CATEGORY_MAP = {
    'cleanser':    'Facial Wash',
    'moisturizer': 'Moisturizer',
    'serum':       'Serum',
    'sunscreen':   'Sunscreen',
    'toner':       'Eksfoliasi',
}

# ─── Cache dataset (load sekali saat modul diimport) ─────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATASET_DIR = os.path.join(_BASE_DIR, 'Dataset')

def _load_set(filename, key_col):
    """Load satu file Excel dan return set string ingredient (lowercase, stripped)."""
    path = os.path.join(_DATASET_DIR, filename)
    df = pd.read_excel(path)
    df[key_col] = df[key_col].astype(str).str.strip()
    df['Ingredient_norm'] = df['Ingredient'].astype(str).str.strip().str.lower()
    return df

print("[recommender] Loading datasets...")
try:
    _df_produk             = pd.read_excel(os.path.join(_DATASET_DIR, 'Dataset Produk.xlsx'))
    _df_jenis_cocok        = _load_set('Jenis Kulit Cocok.xlsx',        'Jenis_Kulit')
    _df_jenis_tidak_cocok  = _load_set('Jenis Kulit Tidak Cocok.xlsx',  'Jenis_Kulit')
    _df_masalah_cocok      = _load_set('Masalah Kulit Cocok.xlsx',      'Masalah_Kulit')
    _df_masalah_tidak_cocok= _load_set('Masalah Kulit Tidak Cocok.xlsx','Masalah_Kulit')
    print(f"[recommender] Dataset loaded — {len(_df_produk)} produk.")
except Exception as e:
    print(f"[recommender] ERROR loading datasets: {e}")
    _df_produk = _df_jenis_cocok = _df_jenis_tidak_cocok = None
    _df_masalah_cocok = _df_masalah_tidak_cocok = None


def _normalize_ingredient(name: str) -> str:
    """Normalisasi nama ingredien: strip, lowercase, hapus tanda kutip."""
    return re.sub(r'\s+', ' ', name.strip().strip('"').strip("'")).lower()


def _weight(index: int, total: int) -> float:
    """Hitung bobot ingredien berdasarkan posisinya di dalam ingredient list."""
    if total == 0:
        return 0.2
    pct = index / total
    if pct <= 0.20:
        return 1.0
    elif pct <= 0.50:
        return 0.5
    else:
        return 0.2


def _build_lookup(df, filter_col, filter_val) -> set:
    """Build set ingredien ternormalisasi untuk jenis/masalah kulit tertentu."""
    if df is None:
        return set()
    mask = df[filter_col].str.strip().str.lower() == filter_val.strip().lower()
    return set(df.loc[mask, 'Ingredient_norm'].tolist())


def _score_one(ingr_raw: str,
               set_jenis_cocok: set,
               set_jenis_tidak: set,
               set_masalah_cocok: set,
               set_masalah_tidak: set) -> tuple[float, list, list]:
    """
    Scoring satu produk berdasarkan ingrediennya.
    Return: (score, cocok_found, tidak_cocok_found)
    """
    # Hilangkan tanda kutip di awal/akhir string ingredien keseluruhan
    ingr_raw = ingr_raw.strip().strip('"').strip("'")
    ingredients_list = [_normalize_ingredient(i) for i in ingr_raw.split(',') if i.strip()]
    total = len(ingredients_list)

    score = 0.0
    cocok_found = []
    tidak_found = []

    for idx, ingr in enumerate(ingredients_list):
        w = _weight(idx, total)

        # Positif: cocok jenis kulit
        if ingr in set_jenis_cocok:
            score += 1.0 * w
            cocok_found.append(ingr.title())

        # Bonus: cocok masalah kulit
        if ingr in set_masalah_cocok:
            score += 0.8 * w
            if ingr.title() not in cocok_found:
                cocok_found.append(ingr.title())

        # Negatif: tidak cocok jenis kulit
        if ingr in set_jenis_tidak:
            score -= 2.0 * w
            tidak_found.append(ingr.title())

        # Negatif: tidak cocok masalah kulit
        if ingr in set_masalah_tidak:
            score -= 1.5 * w
            if ingr.title() not in tidak_found:
                tidak_found.append(ingr.title())

    return round(score, 2), cocok_found, tidak_found


def _score_to_match_pct(score: float, max_score: float, min_score: float) -> int:
    """Normalisasi skor ke rentang 0–100% untuk tampilan "Match %"."""
    if max_score == min_score:
        return 75  # default jika semua produk sama skornya
    # Skor di atas 0 → range 50–100, skor di bawah 0 → range 0–49
    if score >= 0:
        pct = 50 + (score / max(max_score, 1)) * 50
    else:
        pct = max(0, 50 + (score / max(abs(min_score), 1)) * 50)
    return min(100, max(0, round(pct)))


def score_products(
    jenis_kulit: str,
    permasalahan_labels: list[str],
    kategori_frontend: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    Hitung skor semua produk berdasarkan jenis kulit + permasalahan.

    Args:
        jenis_kulit: "Normal" | "Berminyak" | "Kombinasi" | "Kering"
        permasalahan_labels: list label dari SkinScan AI, e.g. ["Jerawat", "PIH"]
        kategori_frontend: "cleanser" | "moisturizer" | "serum" | "sunscreen" | "toner" | None
        limit: jumlah maksimum produk yang dikembalikan

    Returns:
        List dict produk yang sudah di-score dan di-sort descending.
    """
    if _df_produk is None:
        return []

    # ── Tentukan kategori dataset ──────────────────────────────────────────
    df = _df_produk.copy()
    if kategori_frontend:
        kat_dataset = CATEGORY_MAP.get(kategori_frontend)
        if kat_dataset:
            df = df[df['Kategori'].str.strip() == kat_dataset]

    if df.empty:
        return []

    # ── Build lookup sets ──────────────────────────────────────────────────
    set_jenis_cocok = _build_lookup(_df_jenis_cocok,        'Jenis_Kulit',  jenis_kulit)
    set_jenis_tidak = _build_lookup(_df_jenis_tidak_cocok,  'Jenis_Kulit',  jenis_kulit)

    # Gabungkan semua masalah kulit yang relevan
    set_masalah_cocok: set = set()
    set_masalah_tidak: set = set()
    for label in permasalahan_labels:
        dataset_label = PROBLEM_LABEL_MAP.get(label, label)
        set_masalah_cocok |= _build_lookup(_df_masalah_cocok,        'Masalah_Kulit', dataset_label)
        set_masalah_tidak |= _build_lookup(_df_masalah_tidak_cocok,  'Masalah_Kulit', dataset_label)

    # ── Score setiap produk ────────────────────────────────────────────────
    results = []
    for _, row in df.iterrows():
        ingr_raw = str(row.get('Ingridients', '') or '')
        if not ingr_raw.strip():
            continue

        score, cocok, tidak = _score_one(
            ingr_raw,
            set_jenis_cocok,
            set_jenis_tidak,
            set_masalah_cocok,
            set_masalah_tidak,
        )

        harga = row.get('Harga')
        try:
            harga = int(harga)
        except (TypeError, ValueError):
            harga = 0

        results.append({
            'name':          str(row.get('Nama Produk', '')).strip(),
            'kategori':      str(row.get('Kategori', '')).strip(),
            'kategori_key':  kategori_frontend or CATEGORY_MAP.get(str(row.get('Kategori', '')).strip(), 'other'),
            'price':         harga,
            'image_url':     str(row.get('Gambar', '') or ''),
            'link':          str(row.get('Link_Produk', '') or ''),
            'texture':       str(row.get('Tekstur', '') or ''),
            'score':         score,
            'cocok':         cocok[:8],      # max 8 ingredien ditampilkan
            'tidak_cocok':   tidak[:5],
        })

    if not results:
        return []

    # ── Normalisasi ke match % ─────────────────────────────────────────────
    all_scores = [r['score'] for r in results]
    max_s = max(all_scores)
    min_s = min(all_scores)

    for r in results:
        r['match'] = _score_to_match_pct(r['score'], max_s, min_s)
        r['recommended'] = r['score'] > 0

    # ── Sort descending by score, return top `limit` ───────────────────────
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:limit]
