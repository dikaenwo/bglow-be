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
    """Load Dataset Terbaru.csv dan build lookup dicts untuk O(1) matching.

    Mendukung dua format dataset:
    - Format BARU: kolom ingredient_name, badge_text, domain, target, polarity
    - Format LAMA: kolom Ingredient, Jenis Kulit Cocok/Hindari, Masalah Kulit Cocok/Hindari, Badge
    """
    global _SKIN_COCOK, _SKIN_HINDARI, _CONCERN_COCOK, _CONCERN_HINDARI
    global _BADGE_MAP, _DESKRIPSI_MAP

    knowledge_path = os.path.join(_DATASET_DIR, 'Dataset Terbaru.csv')
    df = pd.read_csv(knowledge_path, low_memory=False)

    cols = set(df.columns.tolist())

    # ── Deteksi format dataset ────────────────────────────────────────────────
    is_new_format = 'ingredient_name' in cols and 'domain' in cols and 'target' in cols

    if is_new_format:
        # Format BARU: setiap baris = 1 aturan (1 ingredien x 1 target x domain x polarity)
        for _, row in df.iterrows():
            name_raw = str(row.get('ingredient_name', '')).strip()
            if not name_raw or name_raw == 'nan':
                continue
            name_norm = _normalize(name_raw)

            domain   = str(row.get('domain',   '')).strip().lower()   # 'skin_type' | 'concern'
            target   = str(row.get('target',   '')).strip()           # e.g. 'Berminyak', 'Berjerawat'
            polarity = str(row.get('polarity', '')).strip().lower()   # 'positif' | 'negatif'

            if not domain or not target or target == 'nan' or not polarity:
                continue

            target_norm = target.strip().lower()

            if domain == 'skin_type':
                if polarity == 'positif':
                    _SKIN_COCOK.setdefault(name_norm, set()).add(target_norm)
                elif polarity == 'negatif':
                    _SKIN_HINDARI.setdefault(name_norm, set()).add(target_norm)

            elif domain == 'concern':
                if polarity == 'positif':
                    _CONCERN_COCOK.setdefault(name_norm, set()).add(target_norm)
                elif polarity == 'negatif':
                    _CONCERN_HINDARI.setdefault(name_norm, set()).add(target_norm)

            # Badge — pakai badge_text dari dataset baru
            badge = str(row.get('badge_text', '')).strip()
            if not badge or badge == 'nan':
                badge = str(row.get('raw_badge_cell', '')).strip()
            if badge and badge != 'nan':
                if name_norm in _BADGE_MAP:
                    existing = set(_BADGE_MAP[name_norm].split(' | '))
                    new_badges = set(badge.split(' | '))
                    _BADGE_MAP[name_norm] = ' | '.join(sorted(existing | new_badges))
                else:
                    _BADGE_MAP[name_norm] = badge

    else:
        # Format LAMA: kompatibilitas mundur
        def _parse_comma_list(val) -> set:
            if pd.isna(val) or str(val).strip() in ('-', '', 'nan'):
                return set()
            return {x.strip().lower() for x in str(val).split(',') if x.strip() and x.strip() != '-'}

        for _, row in df.iterrows():
            name_raw = str(row.get('Ingredient', '')).strip()
            if not name_raw:
                continue
            name_norm = _normalize(name_raw)

            skin_cocok    = _parse_comma_list(row.get('Jenis Kulit Cocok'))
            skin_hindari  = _parse_comma_list(row.get('Jenis Kulit Hindari'))
            concern_cocok  = _parse_comma_list(row.get('Masalah Kulit Cocok'))
            concern_hindari = _parse_comma_list(row.get('Masalah Kulit Hindari'))

            _SKIN_COCOK.setdefault(name_norm, set()).update(skin_cocok)
            _SKIN_HINDARI.setdefault(name_norm, set()).update(skin_hindari)
            _CONCERN_COCOK.setdefault(name_norm, set()).update(concern_cocok)
            _CONCERN_HINDARI.setdefault(name_norm, set()).update(concern_hindari)

            badge = str(row.get('Badge', '')).strip()
            if badge and badge != 'nan':
                if name_norm in _BADGE_MAP:
                    existing = set(_BADGE_MAP[name_norm].split(' | '))
                    new_badges = set(badge.split(' | '))
                    _BADGE_MAP[name_norm] = ' | '.join(sorted(existing | new_badges))
                else:
                    _BADGE_MAP[name_norm] = badge

            desc = str(row.get('Deskripsi_ID', '')).strip()
            if desc and desc != 'nan' and name_norm not in _DESKRIPSI_MAP:
                _DESKRIPSI_MAP[name_norm] = desc[:150]

    fmt = "BARU (domain/target/polarity)" if is_new_format else "LAMA (Jenis Kulit Cocok/Hindari)"
    print(f"[recommender] Format dataset terdeteksi: {fmt}")
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
    """
    Klasifikasi skor WSM ke kategori rekomendasi.
    Menggunakan Laplace Smoothing, skor default/netral adalah 0.5.
    
    Threshold:
      >= 0.65 → Sangat Direkomendasikan
      >= 0.52 → Cukup Direkomendasikan
      <  0.52 → Tidak Direkomendasikan
    """
    if score >= 0.65:
        return 'Sangat Direkomendasikan'
    elif score >= 0.52:
        return 'Cukup Direkomendasikan'
    return 'Tidak Direkomendasikan'


def _score_product_wsm(
    ingredients: list[dict],
    skin_type_norm: str,
    concern_labels_norm: set[str],
    kategori_frontend: str | None = None,
) -> dict:
    """
    Hitung WSM score menggunakan Laplace Smoothing.

    Formula Laplace: (Benefit + Alpha) / (Benefit + Penalty + 2 * Alpha)
    Alpha = 1.0 (bobot smoothing).
    Ini mencegah skor ekstrem (seperti 100%) jika hanya 1 ingredien cocok,
    tanpa memberikan penalti pada ingredien inaktif (seperti air/pengawet)
    yang tidak ada di dataset.
    """
    if len(ingredients) == 0:
        return {
            'wsm_score': 0.5, 'kategori_rekomendasi': _classify(0.5),
            'C1': 0.5, 'C2': 0.5, 'C3': 0.5, 'C4': 0.5,
            'cocok_names': [], 'tidak_names': [], 'ingredients_analysis': [],
        }

    ing_analysis = []
    cocok_names  = []
    tidak_names  = []

    # Position-weighted accumulators
    B1, P1 = 0.0, 0.0   # C1: skin type benefit/penalty
    B2, P2 = 0.0, 0.0   # C2: concern benefit/penalty
    B3, P3 = 0.0, 0.0   # C3: position (combined relevance)
    B4, P4 = 0.0, 0.0   # C4: safety (combined)

    # Whitelist surfaktan (Rinse-off suppression) untuk cleanser
    RINSE_OFF_SURFACTANTS = {
        'sodium cocoyl glycinate', 'disodium cocoyl glutamate', 'cocamidopropyl betaine',
        'potassium cocoyl glycinate', 'potassium cocoate', 'sodium laureth sulfate',
        'sodium lauryl sulfate', 'lauramidopropyl betaine', 'sodium cocoyl isethionate',
        'peg-40 hydrogenated castor oil'
    }

    for ing in ingredients:
        norm   = ing['name_norm']
        bucket = ing['bucket']

        pos_w = POSITIVE_POSITION_WEIGHT.get(bucket, 0.33)
        neg_w = NEGATIVE_POSITION_WEIGHT.get(bucket, 0.40)

        skin_pos   = 1.0 if skin_type_norm in _SKIN_COCOK.get(norm, set())   else 0.0
        skin_neg   = 1.0 if skin_type_norm in _SKIN_HINDARI.get(norm, set()) else 0.0
        concern_pos = 1.0 if concern_labels_norm & _CONCERN_COCOK.get(norm, set())   else 0.0
        concern_neg = 1.0 if concern_labels_norm & _CONCERN_HINDARI.get(norm, set()) else 0.0

        # Rinse-off suppression: jika produk cleanser, abaikan penalti untuk surfaktan pembersih
        if kategori_frontend == 'cleanser' and norm in RINSE_OFF_SURFACTANTS:
            skin_neg = 0.0
            concern_neg = 0.0

        has_skin_rule = (skin_pos > 0 or skin_neg > 0)
        has_conc_rule = (concern_pos > 0 or concern_neg > 0)
        has_any_rule  = has_skin_rule or has_conc_rule

        # Accumulate per-criteria
        if has_skin_rule:
            B1 += skin_pos   * pos_w
            P1 += skin_neg   * neg_w

        if has_conc_rule:
            B2 += concern_pos * pos_w
            P2 += concern_neg * neg_w

        # C3: position score
        if has_any_rule:
            if max(skin_pos, concern_pos) > 0:
                B3 += pos_w
            if max(skin_neg, concern_neg) > 0:
                P3 += neg_w

        # C4: safety
        if has_any_rule:
            B4 += max(skin_pos, concern_pos) * pos_w
            P4 += max(skin_neg, concern_neg) * neg_w

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
            'name':     ing['name_raw'],
            'badge':    _BADGE_MAP.get(norm, ''),
            'position': ing['position'],
            'bucket':   bucket,
            'status':   status,
            'deskripsi': _DESKRIPSI_MAP.get(norm, ''),
        })

    # ── Laplace Smoothing (Alpha = 1.0) ──────────────────────────────────────
    # Mencegah (1 / 1) = 100% dari 1 ingredien,
    # (1 + 1) / (1 + 0 + 2) = 66% (lebih realistis)
    alpha = 1.0

    C1 = (B1 + alpha) / (B1 + P1 + 2 * alpha)
    C2 = (B2 + alpha) / (B2 + P2 + 2 * alpha)
    C3 = (B3 + alpha) / (B3 + P3 + 2 * alpha)
    C4 = (B4 + alpha) / (B4 + P4 + 2 * alpha)

    # ── WSM Final ─────────────────────────────────────────────────────────────
    wsm = (
        WEIGHTS['C1_skin_type'] * C1 +
        WEIGHTS['C2_concern']   * C2 +
        WEIGHTS['C3_position']  * C3 +
        WEIGHTS['C4_safety']    * C4
    )

    return {
        'wsm_score':            round(wsm, 6),
        'kategori_rekomendasi': _classify(wsm),
        'C1': round(C1, 4),
        'C2': round(C2, 4),
        'C3': round(C3, 4),
        'C4': round(C4, 4),
        'cocok_names':          cocok_names,
        'tidak_names':          tidak_names,
        'ingredients_analysis': ing_analysis,
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

        wsm_result = _score_product_wsm(
            ingredients, 
            skin_type_norm, 
            concern_labels_norm, 
            kategori_frontend=kategori_frontend
        )

        harga = row.get('Harga')
        try:
            harga = int(harga)
        except (TypeError, ValueError):
            harga = 0

        # Match percentage: WSM score directly gives 0-1, 0.5 is 50%, 0.8 is 80%
        # So we just multiply by 100
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
