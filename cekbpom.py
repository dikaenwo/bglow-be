from flask import Flask, request, jsonify
from flask_cors import CORS
import requests as req
from bs4 import BeautifulSoup
import logging
import re
from urllib.parse import unquote

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    """Buat session baru dan ambil CSRF token dari BPOM."""
    s = req.Session()
    r = s.get(BPOM_PAGE, headers={"User-Agent": UA}, timeout=12)
    r.raise_for_status()
    csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', r.text)
    csrf_token = csrf_match.group(1) if csrf_match else ""
    xsrf_cookie = unquote(s.cookies.get("XSRF-TOKEN", ""))
    return s, csrf_token, xsrf_cookie


def build_payload(query, csrf_token):
    return {
        "draw": "1",
        "columns[0][data]": "PRODUCT_ID",
        "columns[0][name]": "",
        "columns[0][searchable]": "true",
        "columns[0][orderable]": "false",
        "columns[0][search][value]": "",
        "columns[0][search][regex]": "false",
        "columns[1][data]": "PRODUCT_REGISTER",
        "columns[1][name]": "",
        "columns[1][searchable]": "true",
        "columns[1][orderable]": "true",
        "columns[1][search][value]": "",
        "columns[1][search][regex]": "false",
        "columns[2][data]": "PRODUCT_NAME",
        "columns[2][name]": "",
        "columns[2][searchable]": "true",
        "columns[2][orderable]": "true",
        "columns[2][search][value]": "",
        "columns[2][search][regex]": "false",
        "columns[3][data]": "REGISTRAR_NAME",
        "columns[3][name]": "",
        "columns[3][searchable]": "true",
        "columns[3][orderable]": "true",
        "columns[3][search][value]": "",
        "columns[3][search][regex]": "false",
        "order[0][column]": "1",
        "order[0][dir]": "asc",
        "start": "0",
        "length": "10",
        "search[value]": "",
        "search[regex]": "false",
        "query": query,
        "product_register": "",
        "product_name": "",
        "product_brand": "",
        "product_package": "",
        "product_form": "",
        "ingredients": "",
        "submit_date_start": "",
        "submit_date_end": "",
        "product_date_start": "",
        "product_date_end": "",
        "expire_date_start": "",
        "expire_date_end": "",
        "manufacturer_name": "",
        "status": "",
        "release_date": "",
        "manufacturer": "",
        "registrar": "",
        "_token": csrf_token,
    }


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Backend berjalan!"})


@app.route("/cekbpom", methods=["GET"])
def cekbpom():
    raw_query = request.args.get("na", "").strip()
    if not raw_query:
        return jsonify({"error": "Parameter 'na' wajib diisi"}), 400

    na_number = extract_na_from_query(raw_query)
    logger.info(f"Raw: {raw_query!r} -> Searching: {na_number!r}")

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

        logger.info(f"POST {BPOM_DT} query={na_number!r}")
        resp = session.post(BPOM_DT, data=payload, headers=headers, timeout=20)
        logger.info(f"BPOM status: {resp.status_code}")

        if resp.status_code != 200:
            return jsonify({"error": f"BPOM API error {resp.status_code}"}), 502

        data = resp.json()
        rows = data.get('data', [])
        logger.info(f"recordsTotal: {data.get('recordsTotal')}, rows: {len(rows)}")

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

        return jsonify({
            "query": na_number,
            "raw_query": raw_query,
            "results": results,
            "total_found": len(results),
            "records_total": data.get('recordsTotal', 0),
            "source": "datatables-api"
        })

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return jsonify({"error": str(e), "query": na_number}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000, debug=True)