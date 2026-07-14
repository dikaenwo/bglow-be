"""
recommender.py — Sistem rekomendasi produk skincare B-Glow
Menggunakan WSM (Weighted Sum Model) dengan 4 kriteria:
  C1 (0.25) — Kecocokan jenis kulit
  C2 (0.30) — Kecocokan masalah kulit
  C3 (0.20) — Posisi ingredien relevan
  C4 (0.25) — Keamanan / safety

Dataset: Dataset Terbaru.csv (knowledge ingredien ~10k baris)
         Dataset Produk.xlsx (daftar produk ~322 produk)
"""

from __future__ import annotations
import os
import re
import time
import pandas as pd

# ═══════════════════════════════════════════════════════════════════════════════
# KONFIGURASI WSM
# ═══════════════════════════════════════════════════════════════════════════════

WEIGHTS = {
    'C1_skin_type': 0.25,
    'C2_concern':   0.30,
    'C3_position':  0.20,
    'C4_safety':    0.25,
}

POSITIVE_POSITION_WEIGHT = {
    'awal':   1.00,
    'tengah': 0.67,
    'akhir':  0.33,
}

NEGATIVE_POSITION_WEIGHT = {
    'awal':   1.25,
    'tengah': 0.80,
    'akhir':  0.40,
}

# ═══════════════════════════════════════════════════════════════════════════════
# MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

# Mapping label SkinScan AI → nama di dataset
PROBLEM_LABEL_MAP = {
    'Jerawat':         'Berjerawat',
    'PIE':             'PIE',
    'PIH':             'PIH',
    'Bopeng':          'PIE',
    'Hiperpigmentasi': 'PIH',
    'Kemerahan':       'Kemerahan',
}

# Mapping kategori frontend → nama kolom di dataset
CATEGORY_MAP = {
    'cleanser':    'Facial Wash',
    'moisturizer': 'Moisturizer',
    'serum':       'Serum',
    'sunscreen':   'Sunscreen',
    'toner':       'Eksfoliasi',
}

# ═══════════════════════════════════════════════════════════════════════════════
# LOAD DATASET SEKALI SAAT STARTUP → BUILD LOOKUP DICTS (O(1) per ingredien)
# ═══════════════════════════════════════════════════════════════════════════════

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATASET_DIR = os.path.join(_BASE_DIR, 'Dataset')


def _normalize(name: str) -> str:
    """Normalisasi nama ingredien: strip, lowercase, collapse whitespace."""
    return re.sub(r'\s+', ' ', name.strip().strip('"').strip("'")).lower()


def _parse_comma_list(val) -> set:
    """Parse string 'A, B, C' menjadi set {'a', 'b', 'c'}. Return empty set jika '-' atau NaN."""
    if pd.isna(val) or str(val).strip() in ('-', '', 'nan'):
        return set()
    return {x.strip().lower() for x in str(val).split(',') if x.strip() and x.strip() != '-'}


# --- Lookup dicts (di-populate oleh _load_knowledge) ---
# key: ingredient_name_norm (lowercase)
# value: set of matching skin types / concerns (lowercase)
_SKIN_COCOK: dict[str, set[str]] = {}
_SKIN_HINDARI: dict[str, set[str]] = {}
_CONCERN_COCOK: dict[str, set[str]] = {}
_CONCERN_HINDARI: dict[str, set[str]] = {}
_BADGE_MAP: dict[str, str] = {}         # ingredient_norm → badge string
_DESKRIPSI_MAP: dict[str, str] = {}     # ingredient_norm → deskripsi bahasa Indonesia

_df_produk: pd.DataFrame | None = None


def _load_knowledge():
    """Load Dataset Terbaru.csv dan build lookup dicts untuk O(1) matching."""
    global _SKIN_COCOK, _SKIN_HINDARI, _CONCERN_COCOK, _CONCERN_HINDARI
    global _BADGE_MAP, _DESKRIPSI_MAP

    knowledge_path = os.path.join(_DATASET_DIR, 'Dataset Terbaru.csv')
    df = pd.read_csv(knowledge_path, low_memory=False)

    for _, row in df.iterrows():
        name_raw = str(row.get('Ingredient', '')).strip()
        if not name_raw:
            continue
        name_norm = _normalize(name_raw)

        # Skin type cocok / hindari
        skin_cocok = _parse_comma_list(row.get('Jenis Kulit Cocok'))
        skin_hindari = _parse_comma_list(row.get('Jenis Kulit Hindari'))
        concern_cocok = _parse_comma_list(row.get('Masalah Kulit Cocok'))
        concern_hindari = _parse_comma_list(row.get('Masalah Kulit Hindari'))

        # Merge (beberapa ingredient bisa muncul lebih dari sekali dengan badge berbeda)
        if name_norm in _SKIN_COCOK:
            _SKIN_COCOK[name_norm] |= skin_cocok
        else:
            _SKIN_COCOK[name_norm] = skin_cocok

        if name_norm in _SKIN_HINDARI:
            _SKIN_HINDARI[name_norm] |= skin_hindari
        else:
            _SKIN_HINDARI[name_norm] = skin_hindari

        if name_norm in _CONCERN_COCOK:
            _CONCERN_COCOK[name_norm] |= concern_cocok
        else:
            _CONCERN_COCOK[name_norm] = concern_cocok

        if name_norm in _CONCERN_HINDARI:
            _CONCERN_HINDARI[name_norm] |= concern_hindari
        else:
            _CONCERN_HINDARI[name_norm] = concern_hindari

        # Badge — gabungkan jika sudah ada (beda baris bisa punya badge berbeda)
        badge = str(row.get('Badge', '')).strip()
        if badge and badge != 'nan':
            if name_norm in _BADGE_MAP:
                existing = set(_BADGE_MAP[name_norm].split(' | '))
                new_badges = set(badge.split(' | '))
                _BADGE_MAP[name_norm] = ' | '.join(sorted(existing | new_badges))
            else:
                _BADGE_MAP[name_norm] = badge

        # Deskripsi (ambil yang pertama non-kosong)
        desc = str(row.get('Deskripsi_ID', '')).strip()
        if desc and desc != 'nan' and name_norm not in _DESKRIPSI_MAP:
            # Ambil max 150 char untuk deskripsi singkat
            _DESKRIPSI_MAP[name_norm] = desc[:150]

    return len(df)


def _load_products():
    """Load Dataset Produk.xlsx."""
    global _df_produk
    produk_path = os.path.join(_DATASET_DIR, 'Dataset Produk.xlsx')
    _df_produk = pd.read_excel(produk_path)
    return len(_df_produk)


print("[recommender] Loading datasets (WSM mode)...")
_t0 = time.time()
try:
    _n_knowledge = _load_knowledge()
    _n_produk = _load_products()
    _elapsed = time.time() - _t0
    print(f"[recommender] Loaded {_n_knowledge} knowledge rows -> "
          f"{len(_SKIN_COCOK)} unique ingredients, "
          f"{_n_produk} produk. ({_elapsed:.2f}s)")
except Exception as e:
    print(f"[recommender] ERROR loading datasets: {e}")
    import traceback
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════════════════
# INGREDIENT PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_ingredients(ingr_text: str) -> list[dict]:
    """
    Parse string ingredien produk menjadi list dict dengan posisi & bucket.
    Menangani kasus '1,2-Hexanediol' agar koma di tengah angka tidak dipecah.
    """
    raw = str(ingr_text).strip().strip('"').strip("'")
    if not raw:
        return []

    # Proteksi koma di pola seperti '1,2-Hexanediol'
    protected = re.sub(r'(\b\d+),(\d+-[A-Za-z])', r'\1<<COMMA>>\2', raw)
    parts = [x.strip().replace('<<COMMA>>', ',') for x in protected.split(',') if x.strip()]
    total = len(parts)

    result = []
    for idx, name in enumerate(parts):
        pos = idx + 1  # 1-indexed
        if pos <= total / 3:
            bucket = 'awal'
        elif pos <= 2 * total / 3:
            bucket = 'tengah'
        else:
            bucket = 'akhir'

        result.append({
            'name_raw': name,
            'name_norm': _normalize(name),
            'position': pos,
            'total': total,
            'bucket': bucket,
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# WSM SCORING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _classify(score: float) -> str:
    """Klasifikasi skor WSM ke kategori rekomendasi."""
    if score >= 0.75:
        return 'Sangat Direkomendasikan'
    elif score >= 0.50:
        return 'Cukup Direkomendasikan'
    return 'Tidak Direkomendasikan'


def _score_product_wsm(
    ingredients: list[dict],
    skin_type_norm: str,
    concern_labels_norm: set[str],
) -> dict:
    """
    Hitung WSM score untuk satu produk.

    Returns dict:
        wsm_score, kategori_rekomendasi, C1, C2, C3, C4,
        cocok_names, tidak_names, ingredients_analysis
    """
    # --- Per-ingredient analysis ---
    ing_analysis = []
    cocok_names = []
    tidak_names = []

    for ing in ingredients:
        norm = ing['name_norm']
        bucket = ing['bucket']

        # Lookup
        skin_cocok = _SKIN_COCOK.get(norm, set())
        skin_hindari = _SKIN_HINDARI.get(norm, set())
        concern_cocok = _CONCERN_COCOK.get(norm, set())
        concern_hindari = _CONCERN_HINDARI.get(norm, set())
        badge = _BADGE_MAP.get(norm, '')
        desc = _DESKRIPSI_MAP.get(norm, '')

        # Skor per-ingredient (sesuai algoritma Colab: benefit/penalty = 1.0 per match)
        skin_pos = 1.0 if skin_type_norm in skin_cocok else 0.0
        skin_neg = 1.0 if skin_type_norm in skin_hindari else 0.0

        # Concern: cek apakah salah satu concern user cocok/hindari
        concern_pos = 1.0 if concern_labels_norm & concern_cocok else 0.0
        concern_neg = 1.0 if concern_labels_norm & concern_hindari else 0.0

        pos_total = max(skin_pos, concern_pos)
        neg_total = max(skin_neg, concern_neg)

        is_positive = (skin_pos > 0) or (concern_pos > 0)
        is_negative = (skin_neg > 0) or (concern_neg > 0)

        if is_positive and is_negative:
            status = 'campuran'
        elif is_positive:
            status = 'positif'
        elif is_negative:
            status = 'negatif'
        else:
            status = 'netral'

        if is_positive:
            cocok_names.append(ing['name_raw'].title())
        if is_negative:
            tidak_names.append(ing['name_raw'].title())

        ing_analysis.append({
            'name': ing['name_raw'],
            'badge': badge,
            'position': ing['position'],
            'bucket': bucket,
            'status': status,
            'deskripsi': desc,
            # Internal scoring data
            '_skin_pos': skin_pos,
            '_skin_neg': skin_neg,
            '_concern_pos': concern_pos,
            '_concern_neg': concern_neg,
            '_pos_total': pos_total,
            '_neg_total': neg_total,
        })

    # --- C1: Kecocokan Jenis Kulit ---
    B1 = sum(a['_skin_pos'] for a in ing_analysis if a['_skin_pos'] > 0 or a['_skin_neg'] > 0)
    P1 = sum(a['_skin_neg'] for a in ing_analysis if a['_skin_pos'] > 0 or a['_skin_neg'] > 0)
    C1 = B1 / (B1 + P1) if (B1 + P1) > 0 else 0.5

    # --- C2: Kecocokan Masalah Kulit ---
    B2 = sum(a['_concern_pos'] for a in ing_analysis if a['_concern_pos'] > 0 or a['_concern_neg'] > 0)
    P2 = sum(a['_concern_neg'] for a in ing_analysis if a['_concern_pos'] > 0 or a['_concern_neg'] > 0)
    C2 = B2 / (B2 + P2) if (B2 + P2) > 0 else 0.5

    # --- C3: Posisi Ingredien Relevan ---
    Pos3 = 0.0
    Neg3 = 0.0
    for a in ing_analysis:
        is_relevant = (a['_pos_total'] > 0) or (a['_neg_total'] > 0)
        if not is_relevant:
            continue
        bucket = a['bucket']
        if a['_pos_total'] > 0:
            Pos3 += POSITIVE_POSITION_WEIGHT.get(bucket, 0.33)
        if a['_neg_total'] > 0:
            Neg3 += NEGATIVE_POSITION_WEIGHT.get(bucket, 0.40)
    C3 = Pos3 / (Pos3 + Neg3) if (Pos3 + Neg3) > 0 else 0.5

    # --- C4: Keamanan ---
    safe_total = sum(a['_pos_total'] for a in ing_analysis if a['_pos_total'] > 0 or a['_neg_total'] > 0)
    risk_total = sum(a['_neg_total'] for a in ing_analysis if a['_pos_total'] > 0 or a['_neg_total'] > 0)
    C4 = safe_total / (safe_total + risk_total) if (safe_total + risk_total) > 0 else 1.0

    # --- WSM Final ---
    wsm = (
        WEIGHTS['C1_skin_type'] * C1 +
        WEIGHTS['C2_concern'] * C2 +
        WEIGHTS['C3_position'] * C3 +
        WEIGHTS['C4_safety'] * C4
    )

    # Clean up internal fields from analysis before returning
    clean_analysis = []
    for a in ing_analysis:
        clean_analysis.append({
            'name':      a['name'],
            'badge':     a['badge'],
            'position':  a['position'],
            'bucket':    a['bucket'],
            'status':    a['status'],
            'deskripsi': a['deskripsi'],
        })

    return {
        'wsm_score': round(wsm, 6),
        'kategori_rekomendasi': _classify(wsm),
        'C1': round(C1, 4),
        'C2': round(C2, 4),
        'C3': round(C3, 4),
        'C4': round(C4, 4),
        'cocok_names': cocok_names,
        'tidak_names': tidak_names,
        'ingredients_analysis': clean_analysis,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API (signature dipertahankan agar main.py tidak berubah)
# ═══════════════════════════════════════════════════════════════════════════════

def score_products(
    jenis_kulit: str,
    permasalahan_labels: list[str],
    kategori_frontend: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    Hitung skor WSM semua produk berdasarkan jenis kulit + permasalahan.

    Args:
        jenis_kulit: "Normal" | "Berminyak" | "Kombinasi" | "Kering"
        permasalahan_labels: list label dari SkinScan AI, e.g. ["Jerawat", "PIH"]
        kategori_frontend: "cleanser" | "moisturizer" | "serum" | "sunscreen" | "toner" | None
        limit: jumlah maksimum produk yang dikembalikan

    Returns:
        List dict produk yang sudah di-score dan di-sort descending.
    """
    if _df_produk is None or _df_produk.empty:
        return []

    # ── Normalisasi input ──────────────────────────────────────────────────
    skin_type_norm = jenis_kulit.strip().lower()

    # Map label SkinScan → label dataset, lalu lowercase
    concern_labels_norm = set()
    for label in permasalahan_labels:
        mapped = PROBLEM_LABEL_MAP.get(label, label)
        concern_labels_norm.add(mapped.strip().lower())

    # ── Filter kategori ───────────────────────────────────────────────────
    df = _df_produk.copy()
    if kategori_frontend:
        kat_dataset = CATEGORY_MAP.get(kategori_frontend)
        if kat_dataset:
            df = df[df['Kategori'].str.strip() == kat_dataset]

    if df.empty:
        return []

    # ── Score setiap produk ────────────────────────────────────────────────
    results = []
    for _, row in df.iterrows():
        ingr_raw = str(row.get('Ingridients', '') or '')
        if not ingr_raw.strip():
            continue

        ingredients = _parse_ingredients(ingr_raw)
        if not ingredients:
            continue

        wsm_result = _score_product_wsm(ingredients, skin_type_norm, concern_labels_norm)

        harga = row.get('Harga')
        try:
            harga = int(harga)
        except (TypeError, ValueError):
            harga = 0

        # Match percentage: langsung WSM × 100 (sudah 0-1 range)
        match_pct = min(100, max(0, round(wsm_result['wsm_score'] * 100)))

        # Batasi jumlah analysis yang dikirim (hanya yang relevan + max 20)
        analysis_full = wsm_result['ingredients_analysis']
        # Prioritaskan positif dan negatif, netral terakhir
        analysis_relevant = [a for a in analysis_full if a['status'] != 'netral']
        analysis_netral = [a for a in analysis_full if a['status'] == 'netral']
        analysis_to_send = analysis_relevant + analysis_netral[:max(0, 20 - len(analysis_relevant))]

        results.append({
            'name':          str(row.get('Nama Produk', '')).strip(),
            'kategori':      str(row.get('Kategori', '')).strip(),
            'kategori_key':  kategori_frontend or '',
            'price':         harga,
            'image_url':     str(row.get('Gambar', '') or ''),
            'link':          str(row.get('Link_Produk', '') or ''),
            'texture':       str(row.get('Tekstur', '') or ''),
            'score':         wsm_result['wsm_score'],
            'match':         match_pct,
            'recommended':   wsm_result['wsm_score'] >= 0.50,
            'kategori_rekomendasi': wsm_result['kategori_rekomendasi'],
            'cocok':         wsm_result['cocok_names'][:8],
            'tidak_cocok':   wsm_result['tidak_names'][:5],
            'ingredients_analysis': analysis_to_send,
            'wsm_detail': {
                'C1': wsm_result['C1'],
                'C2': wsm_result['C2'],
                'C3': wsm_result['C3'],
                'C4': wsm_result['C4'],
            },
        })

    if not results:
        return []

    # ── Sort descending by WSM score ───────────────────────────────────────
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:limit]
