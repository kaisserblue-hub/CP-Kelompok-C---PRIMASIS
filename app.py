import getpass
import os
import re
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, g, jsonify, request, send_from_directory, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PRIMASIS_DB", BASE_DIR / "primasis.db"))
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)
SECRET_PATH = INSTANCE_DIR / ".secret_key"
if os.environ.get("PRIMASIS_SECRET_KEY"):
    SECRET_KEY = os.environ["PRIMASIS_SECRET_KEY"]
else:
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_hex(32), encoding="ascii")
        SECRET_PATH.chmod(0o600)
    SECRET_KEY = SECRET_PATH.read_text(encoding="ascii").strip()

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("PRIMASIS_COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=10),
    MAX_CONTENT_LENGTH=64 * 1024,
)

PARTNERS = {"smkmuh4": "SMK Muhammadiyah 4", "smanmojo": "SMAN Mojo"}
SEED_PRODUCTS = [
    ("p-001", "Risoles", "Mayo, Ayam, Pizzarella", "PCS", 1800, 2600, 3000),
    ("p-002", "Wonton Rebus Chili Oil", "Level 1, Level 2", "Porsi", 3000, 4500, 5000),
    ("p-003", "Pangsit Goreng Chili Oil", "", "Porsi", 3000, 4500, 5000),
    ("p-004", "Basreng Chili Oil", "", "Porsi", 3000, 4500, 5000),
    ("p-005", "Mie Chili Oil", "", "Porsi", 3000, 4500, 5000),
    ("p-006", "Mix Pangsit-Basreng Chili Oil", "", "Porsi", 3000, 4500, 5000),
    ("p-007", "Kebab Sosis-Sayur", "", "PCS", 3000, 4500, 5000),
    ("p-008", "Brownies Potong", "Original", "PCS", 1200, 1750, 2000),
]


@contextmanager
def database():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    with database() as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS partners (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                variant TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL,
                cost INTEGER NOT NULL CHECK(cost >= 0),
                supplier INTEGER NOT NULL CHECK(supplier >= cost),
                retail INTEGER NOT NULL CHECK(retail >= supplier)
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                partner TEXT NOT NULL,
                product_id TEXT NOT NULL REFERENCES products(id),
                qty INTEGER NOT NULL CHECK(qty > 0),
                unit_cost INTEGER NOT NULL,
                unit_supplier INTEGER NOT NULL,
                unit_retail INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id INTEGER NOT NULL REFERENCES deliveries(id),
                date TEXT NOT NULL,
                qty INTEGER NOT NULL CHECK(qty > 0)
            );
            CREATE TABLE IF NOT EXISTS returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id INTEGER NOT NULL REFERENCES deliveries(id),
                date TEXT NOT NULL,
                qty INTEGER NOT NULL CHECK(qty > 0)
            );
            """
        )
        users_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()["sql"]
        if "check(role in" in users_schema.lower():
            connection.execute("ALTER TABLE users RENAME TO users_legacy")
            connection.execute(
                """CREATE TABLE users (
                       username TEXT PRIMARY KEY,
                       display_name TEXT NOT NULL,
                       role TEXT NOT NULL,
                       password_hash TEXT NOT NULL
                   )"""
            )
            connection.execute(
                "INSERT INTO users SELECT username, display_name, role, password_hash FROM users_legacy"
            )
            connection.execute("DROP TABLE users_legacy")
        delivery_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='deliveries'"
        ).fetchone()["sql"]
        if "check(partner in" in delivery_schema.lower():
            connection.execute("ALTER TABLE deliveries RENAME TO deliveries_legacy")
            connection.execute(
                """CREATE TABLE deliveries (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       date TEXT NOT NULL,
                       partner TEXT NOT NULL,
                       product_id TEXT NOT NULL REFERENCES products(id),
                       qty INTEGER NOT NULL CHECK(qty > 0),
                       unit_cost INTEGER NOT NULL,
                       unit_supplier INTEGER NOT NULL,
                       unit_retail INTEGER NOT NULL
                   )"""
            )
            connection.execute(
                """INSERT INTO deliveries
                   SELECT id, date, partner, product_id, qty, unit_cost, unit_supplier, unit_retail
                   FROM deliveries_legacy"""
            )
            connection.execute("DROP TABLE deliveries_legacy")
        connection.execute("PRAGMA legacy_alter_table = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executemany(
            "INSERT OR IGNORE INTO partners(id, name) VALUES (?, ?)",
            PARTNERS.items(),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO products VALUES (?, ?, ?, ?, ?, ?, ?)",
            SEED_PRODUCTS,
        )


def clean_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        abort(400, description="Tanggal harus berformat YYYY-MM-DD.")


def positive_integer(value, label):
    if isinstance(value, bool):
        abort(400, description=f"{label} harus berupa bilangan bulat positif.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        abort(400, description=f"{label} harus berupa bilangan bulat positif.")
    if number < 1 or str(number) != str(value):
        abort(400, description=f"{label} harus berupa bilangan bulat positif.")
    return number


def nonnegative_integer(value, label):
    if isinstance(value, bool):
        abort(400, description=f"{label} harus berupa bilangan bulat nol atau lebih.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        abort(400, description=f"{label} harus berupa bilangan bulat nol atau lebih.")
    if number < 0 or str(number) != str(value):
        abort(400, description=f"{label} harus berupa bilangan bulat nol atau lebih.")
    return number


def payload():
    if not request.is_json:
        abort(415, description="Konten harus berupa JSON.")
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        abort(400, description="Data permintaan tidak valid.")
    return value


def current_user():
    username = session.get("username")
    if not username:
        abort(401, description="Silakan masuk terlebih dahulu.")
    with database() as connection:
        user = connection.execute(
            "SELECT username, display_name, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not user:
        session.clear()
        abort(401, description="Sesi tidak valid. Silakan masuk kembali.")
    return dict(user)


def require_user(*roles):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            user = current_user()
            if roles and user["role"] not in roles:
                abort(403, description="Akun ini tidak memiliki izin untuk tindakan tersebut.")
            g.user = user
            return function(*args, **kwargs)
        return wrapped
    return decorator


def totals_for(connection, delivery_id):
    row = connection.execute(
        """SELECT d.qty,
                  COALESCE((SELECT SUM(qty) FROM sales WHERE delivery_id = d.id), 0) sold,
                  COALESCE((SELECT SUM(qty) FROM returns WHERE delivery_id = d.id), 0) returned
           FROM deliveries d WHERE d.id = ?""",
        (delivery_id,),
    ).fetchone()
    return row


def visible_clause(user, alias="d"):
    if user["role"] == "admin":
        return "", ()
    return f" WHERE {alias}.partner = ?", (user["role"],)


@app.before_request
def protect_api():
    if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        token = session.get("csrf_token")
        if not token or request.headers.get("X-CSRF-Token") != token:
            abort(403, description="Token keamanan tidak valid. Muat ulang halaman.")


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "no-cache"
    return response


@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return jsonify(error=str(error.description)), 404
    return error


@app.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        return jsonify(error=str(error.description)), error.code
    app.logger.exception("Unhandled request error")
    if request.path.startswith("/api/"):
        return jsonify(error="Terjadi kesalahan pada server."), 500
    return "Terjadi kesalahan pada server.", 500


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/session")
def get_session():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    user = None
    if session.get("username"):
        try:
            user = current_user()
        except Exception:
            session.clear()
            session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(user=user, csrfToken=session["csrf_token"])


@app.get("/api/setup-status")
def setup_status():
    with database() as connection:
        configured = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    return jsonify(configured=configured)


@app.post("/api/setup")
def setup_first_accounts():
    data = payload()
    account_data = [
        ("chusnul", "Chusnul", "admin", data.get("adminPassword")),
        ("smkmuh4", PARTNERS["smkmuh4"], "smkmuh4", data.get("smkmuh4Password")),
        ("smanmojo", PARTNERS["smanmojo"], "smanmojo", data.get("smanmojoPassword")),
    ]
    for username, _, _, password in account_data:
        if not isinstance(password, str) or len(password) < 10 or len(password) > 1024:
            abort(400, description=f"Kata sandi untuk {username} harus 10 sampai 1024 karakter.")
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            abort(409, description="Akun sudah disiapkan. Silakan masuk.")
        connection.executemany(
            "INSERT INTO users(username, display_name, role, password_hash) VALUES (?, ?, ?, ?)",
            [(username, display_name, role, generate_password_hash(password)) for username, display_name, role, password in account_data],
        )
    session.clear()
    session.permanent = True
    session["username"] = "chusnul"
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(
        configured=True,
        user={"username": "chusnul", "display_name": "Chusnul", "role": "admin"},
        csrfToken=session["csrf_token"],
    ), 201


@app.post("/api/session/login")
def login():
    data = payload()
    username = str(data.get("username", "")).strip().lower()
    password = data.get("password", "")
    if not isinstance(password, str) or len(username) > 80 or len(password) > 1024:
        abort(400, description="Nama pengguna atau kata sandi tidak valid.")
    with database() as connection:
        user = connection.execute(
            "SELECT username, display_name, role, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        abort(401, description="Nama pengguna atau kata sandi salah.")
    session.clear()
    session.permanent = True
    session["username"] = user["username"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(
        user={"username": user["username"], "display_name": user["display_name"], "role": user["role"]},
        csrfToken=session["csrf_token"],
    )


@app.post("/api/session/logout")
def logout():
    session.clear()
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(ok=True, csrfToken=session["csrf_token"])


@app.get("/api/state")
@require_user()
def get_state():
    user = g.user
    where, params = visible_clause(user)
    with database() as connection:
        partners = {
            row["id"]: row["name"]
            for row in connection.execute("SELECT id, name FROM partners ORDER BY name")
        }
        products = [dict(row) for row in connection.execute("SELECT * FROM products ORDER BY id")]
        deliveries = [dict(row) for row in connection.execute(f"SELECT d.* FROM deliveries d{where} ORDER BY d.date DESC, d.id DESC", params)]
        ids = [row["id"] for row in deliveries]
        sales = []
        returns = []
        if ids:
            marks = ",".join("?" for _ in ids)
            sales = [dict(row) for row in connection.execute(f"SELECT s.id, s.delivery_id, s.date, s.qty FROM sales s JOIN deliveries d ON d.id=s.delivery_id WHERE s.delivery_id IN ({marks}) ORDER BY s.date DESC, s.id DESC", ids)]
            returns = [dict(row) for row in connection.execute(f"SELECT r.id, r.delivery_id, r.date, r.qty FROM returns r JOIN deliveries d ON d.id=r.delivery_id WHERE r.delivery_id IN ({marks}) ORDER BY r.date DESC, r.id DESC", ids)]
    for delivery in deliveries:
        delivery["productId"] = delivery.pop("product_id")
        delivery["unitCost"] = delivery.pop("unit_cost")
        delivery["unitSupplier"] = delivery.pop("unit_supplier")
        delivery["unitRetail"] = delivery.pop("unit_retail")
        if user["role"] != "admin":
            delivery.pop("unitCost")
    if user["role"] != "admin":
        for product in products:
            product.pop("cost")
            product.pop("supplier")
    for item in sales + returns:
        item["deliveryId"] = item.pop("delivery_id")
    return jsonify(products=products, deliveries=deliveries, sales=sales, returns=returns, partners=partners)


@app.post("/api/partners")
@require_user("admin")
def add_partner():
    data = payload()
    name = str(data.get("name", "")).strip()
    username = str(data.get("username", "")).strip().lower()
    password = data.get("password", "")
    if not name or len(name) > 80:
        abort(400, description="Nama mitra harus berisi 1 sampai 80 karakter.")
    if not re.fullmatch(r"[a-z0-9_-]{3,30}", username) or username == "admin":
        abort(400, description="Username harus 3 sampai 30 karakter: huruf kecil, angka, _ atau -.")
    if not isinstance(password, str) or len(password) < 10 or len(password) > 1024:
        abort(400, description="Kata sandi harus 10 sampai 1024 karakter.")
    try:
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO partners(id, name) VALUES (?, ?)", (username, name))
            connection.execute(
                "INSERT INTO users(username, display_name, role, password_hash) VALUES (?, ?, ?, ?)",
                (username, name, username, generate_password_hash(password)),
            )
    except sqlite3.IntegrityError:
        abort(409, description="Username atau nama mitra sudah digunakan.")
    return jsonify(ok=True, partner={"id": username, "name": name}), 201


def product_input(data):
    product_id = str(data.get("id", "")).strip().lower()
    name = str(data.get("name", "")).strip()
    variant = str(data.get("variant", "")).strip()
    unit = str(data.get("unit", "")).strip()
    if not product_id or len(product_id) > 30 or not name or len(name) > 120 or len(variant) > 120 or not unit or len(unit) > 30:
        abort(400, description="ID, nama, varian, atau satuan produk tidak valid.")
    try:
        cost, supplier, retail = (int(data[key]) for key in ("cost", "supplier", "retail"))
    except (KeyError, TypeError, ValueError):
        abort(400, description="Harga produk harus berupa bilangan bulat.")
    if min(cost, supplier, retail) < 0 or supplier < cost or retail < supplier:
        abort(400, description="Harga mitra harus ≥ harga suplier ≥ modal.")
    return product_id, name, variant, unit, cost, supplier, retail


@app.post("/api/products")
@require_user("admin")
def add_product():
    values = product_input(payload())
    try:
        with database() as connection:
            connection.execute("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", values)
    except sqlite3.IntegrityError:
        abort(409, description="ID produk sudah digunakan.")
    return jsonify(ok=True), 201


@app.put("/api/products/<product_id>")
@require_user("admin")
def update_product(product_id):
    values = product_input({**payload(), "id": product_id})
    with database() as connection:
        cursor = connection.execute(
            "UPDATE products SET name=?, variant=?, unit=?, cost=?, supplier=?, retail=? WHERE id=?",
            (*values[1:], product_id),
        )
        if cursor.rowcount == 0:
            abort(404, description="Produk tidak ditemukan.")
    return jsonify(ok=True)


@app.post("/api/deliveries")
@require_user("admin")
def add_delivery():
    data = payload()
    partner = data.get("partner")
    with database() as connection:
        exists = connection.execute("SELECT 1 FROM partners WHERE id=?", (partner,)).fetchone()
    if not exists:
        abort(400, description="Mitra tidak valid.")
    product_id = str(data.get("productId", ""))
    qty = positive_integer(data.get("qty"), "Jumlah titipan")
    delivery_date = clean_date(data.get("date"))
    with database() as connection:
        item = connection.execute("SELECT cost, supplier, retail FROM products WHERE id=?", (product_id,)).fetchone()
        if not item:
            abort(404, description="Produk tidak ditemukan.")
        cursor = connection.execute(
            "INSERT INTO deliveries(date, partner, product_id, qty, unit_cost, unit_supplier, unit_retail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (delivery_date, partner, product_id, qty, item["cost"], item["supplier"], item["retail"]),
        )
    return jsonify(id=cursor.lastrowid), 201


@app.put("/api/deliveries/<int:delivery_id>")
@require_user("admin")
def update_delivery(delivery_id):
    data = payload()
    qty = positive_integer(data.get("qty"), "Jumlah titipan")
    delivery_date = clean_date(data.get("date"))
    with database() as connection:
        delivery = connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if not delivery:
            abort(404, description="Titipan tidak ditemukan.")
        totals = totals_for(connection, delivery_id)
        if qty < totals["sold"] + totals["returned"]:
            abort(409, description="Jumlah titipan tidak boleh kurang dari penjualan dan retur.")
        connection.execute("UPDATE deliveries SET date=?, qty=? WHERE id=?", (delivery_date, qty, delivery_id))
    return jsonify(ok=True)


@app.post("/api/sales")
@require_user()
def add_sale():
    with database() as connection:
        is_partner = connection.execute("SELECT 1 FROM partners WHERE id=?", (g.user["role"],)).fetchone()
    if not is_partner:
        abort(403, description="Hanya akun mitra yang dapat mencatat penjualan.")
    data = payload()
    delivery_id = positive_integer(data.get("deliveryId"), "Titipan")
    qty = positive_integer(data.get("qty"), "Jumlah terjual")
    sale_date = clean_date(data.get("date"))
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        delivery = connection.execute("SELECT partner FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if not delivery or delivery["partner"] != g.user["role"]:
            abort(404, description="Stok tidak ditemukan.")
        totals = totals_for(connection, delivery_id)
        if qty > totals["qty"] - totals["sold"] - totals["returned"]:
            abort(409, description="Jumlah penjualan melebihi stok yang tersedia.")
        connection.execute("INSERT INTO sales(delivery_id, date, qty) VALUES (?, ?, ?)", (delivery_id, sale_date, qty))
    return jsonify(ok=True), 201


@app.post("/api/returns")
@require_user()
def add_return():
    with database() as connection:
        is_partner = connection.execute("SELECT 1 FROM partners WHERE id=?", (g.user["role"],)).fetchone()
    if not is_partner:
        abort(403, description="Hanya akun mitra yang dapat mencatat retur.")
    data = payload()
    delivery_id = positive_integer(data.get("deliveryId"), "Titipan")
    qty = positive_integer(data.get("qty"), "Jumlah retur")
    return_date = clean_date(data.get("date"))
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        delivery = connection.execute("SELECT partner FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if not delivery or delivery["partner"] != g.user["role"]:
            abort(404, description="Stok tidak ditemukan.")
        totals = totals_for(connection, delivery_id)
        if qty > totals["qty"] - totals["sold"] - totals["returned"]:
            abort(409, description="Jumlah retur melebihi stok yang tersedia.")
        connection.execute("INSERT INTO returns(delivery_id, date, qty) VALUES (?, ?, ?)", (delivery_id, return_date, qty))
    return jsonify(ok=True), 201


@app.post("/api/reconciliations")
@require_user()
def reconcile_delivery():
    with database() as connection:
        if not connection.execute(
            "SELECT 1 FROM partners WHERE id=?", (g.user["role"],)
        ).fetchone():
            abort(403, description="Hanya akun mitra yang dapat mencatat sisa sore.")
    data = payload()
    delivery_id = positive_integer(data.get("deliveryId"), "Titipan")
    returned_qty = nonnegative_integer(data.get("qty"), "Jumlah retur atau sisa")
    reconciliation_date = clean_date(data.get("date"))
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        delivery = connection.execute(
            "SELECT partner, unit_supplier FROM deliveries WHERE id=?", (delivery_id,)
        ).fetchone()
        if not delivery or delivery["partner"] != g.user["role"]:
            abort(404, description="Stok tidak ditemukan.")
        totals = totals_for(connection, delivery_id)
        available = totals["qty"] - totals["sold"] - totals["returned"]
        if available < 1:
            abort(409, description="Stok ini sudah direkap.")
        if returned_qty > available:
            abort(409, description="Jumlah retur atau sisa melebihi stok yang tersedia.")
        sold_qty = available - returned_qty
        if sold_qty:
            connection.execute(
                "INSERT INTO sales(delivery_id, date, qty) VALUES (?, ?, ?)",
                (delivery_id, reconciliation_date, sold_qty),
            )
        if returned_qty:
            connection.execute(
                "INSERT INTO returns(delivery_id, date, qty) VALUES (?, ?, ?)",
                (delivery_id, reconciliation_date, returned_qty),
            )
    return jsonify(
        ok=True,
        sold=sold_qty,
        returned=returned_qty,
        amountDue=sold_qty * delivery["unit_supplier"],
    ), 201


@app.get("/api/reports")
@require_user()
def reports():
    user = g.user
    where, params = visible_clause(user)
    with database() as connection:
        rows = connection.execute(
            f"""SELECT p.id, p.name, p.variant,
                       COALESCE(SUM(d.qty), 0) delivered,
                       COALESCE(SUM((SELECT SUM(s.qty) FROM sales s WHERE s.delivery_id=d.id)), 0) sold,
                       COALESCE(SUM((SELECT SUM(r.qty) FROM returns r WHERE r.delivery_id=d.id)), 0) returned,
                       COALESCE(SUM(d.qty - (SELECT COALESCE(SUM(s.qty),0) FROM sales s WHERE s.delivery_id=d.id) - (SELECT COALESCE(SUM(r.qty),0) FROM returns r WHERE r.delivery_id=d.id)), 0) remaining,
                       COALESCE(SUM((SELECT SUM(s.qty) FROM sales s WHERE s.delivery_id=d.id) * d.unit_cost), 0) supplierCost,
                       COALESCE(SUM((SELECT SUM(s.qty) FROM sales s WHERE s.delivery_id=d.id) * d.unit_supplier), 0) supplierRevenue,
                       COALESCE(SUM((SELECT SUM(s.qty) FROM sales s WHERE s.delivery_id=d.id) * (d.unit_supplier-d.unit_cost)), 0) supplierProfit,
                       COALESCE(SUM((SELECT SUM(s.qty) FROM sales s WHERE s.delivery_id=d.id) * (d.unit_retail-d.unit_supplier)), 0) partnerProfit,
                       COALESCE(SUM((SELECT SUM(s.qty) FROM sales s WHERE s.delivery_id=d.id) * d.unit_retail), 0) retailRevenue
                FROM products p LEFT JOIN deliveries d ON d.product_id=p.id{where}
                GROUP BY p.id ORDER BY p.id""",
            params,
        ).fetchall()
        product_rows = [dict(row) for row in rows if row["delivered"] or row["sold"] or row["returned"]]
        partner_rows = []
        if user["role"] == "admin":
            for partner_row in connection.execute("SELECT id, name FROM partners ORDER BY name"):
                partner, name = partner_row["id"], partner_row["name"]
                row = connection.execute(
                    """SELECT ? name,
                              COALESCE(SUM(s.qty),0) sold,
                              COALESCE(SUM(s.qty*d.unit_retail),0) revenue,
                              COALESCE(SUM(s.qty*(d.unit_supplier-d.unit_cost)),0) supplierProfit,
                              COALESCE(SUM(s.qty*(d.unit_retail-d.unit_supplier)),0) partnerProfit
                       FROM deliveries d LEFT JOIN sales s ON s.delivery_id=d.id WHERE d.partner=?""",
                    (name, partner),
                ).fetchone()
                partner_rows.append(dict(row))
    totals = {key: sum(row[key] for row in product_rows) for key in ("delivered", "sold", "returned", "remaining", "supplierCost", "supplierRevenue", "supplierProfit", "partnerProfit", "retailRevenue")}
    if user["role"] != "admin":
        for row in product_rows:
            for key in ("supplierCost", "supplierRevenue", "supplierProfit", "partnerProfit", "retailRevenue"):
                row.pop(key)
        for key in ("supplierCost", "supplierRevenue", "supplierProfit", "partnerProfit", "retailRevenue"):
            totals.pop(key)
    return jsonify(products=product_rows, totals=totals, partners=partner_rows)


def setup_users():
    initialize_database()
    accounts = [
        ("chusnul", "Chusnul", "admin"),
        ("smkmuh4", PARTNERS["smkmuh4"], "smkmuh4"),
        ("smanmojo", PARTNERS["smanmojo"], "smanmojo"),
    ]
    credentials = []
    for username, display_name, role in accounts:
        while True:
            password = getpass.getpass(f"Kata sandi baru untuk {username}: ")
            if len(password) < 10:
                print("Gunakan setidaknya 10 karakter.")
                continue
            confirmation = getpass.getpass("Ulangi kata sandi: ")
            if password != confirmation:
                print("Kata sandi tidak sama.")
                continue
            credentials.append((username, display_name, role, generate_password_hash(password)))
            break
    with database() as connection:
        connection.executemany(
            "INSERT INTO users(username, display_name, role, password_hash) VALUES (?, ?, ?, ?) ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name, role=excluded.role, password_hash=excluded.password_hash",
            credentials,
        )
    print("Akun PRIMASIS berhasil dibuat atau diperbarui.")


initialize_database()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup-users":
        setup_users()
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
