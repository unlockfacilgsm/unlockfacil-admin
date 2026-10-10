from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from pathlib import Path

from werkzeug.middleware.proxy_fix import ProxyFix
from flask import (
    Flask, abort, g, redirect, render_template, request, url_for, current_app
)

APP_DIR = Path(__file__).resolve().parent
VALID_TOOLS = {
    "Unlock Tool": {"12 horas": 2000, "3 meses": 6000, "6 meses": 9000},
    "TSM Tool": {"12 horas": 2500},
    "AMT Tool": {"12 horas": 2500},
    "TFM Tool": {"12 horas": 2500},
    "Borneo": {"3 dias": 4000},
}
VALID_STATUSES = {"Pago", "Cancelado"}
VALID_PAYMENTS = {"Pix", "Cartão", "Dinheiro", "Outro"}
VALID_EXPENSE_CATEGORIES = {
    "Software e serviços", "Infraestrutura", "Taxas", "Marketing", "Outros"
}
VALID_AD_PLATFORMS = {"Meta Ads", "Google Ads", "Outro"}
SESSION_COOKIE = "uf_admin_session"
SESSION_TTL_SECONDS = 30 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 8


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_now() -> str:
    return utcnow().isoformat()


def parse_money_to_cents(raw: str) -> int:
    """Parse Brazilian-style money input into integer cents; reject ambiguous/negative values."""
    value = (raw or "").strip().replace("R$", "").replace(" ", "")
    if not value or len(value) > 24:
        raise ValueError("Informe um valor válido.")
    if "," in value:
        if value.count(",") > 1:
            raise ValueError("Informe um valor válido.")
        value = value.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Informe um valor válido.") from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal("99999999.99"):
        raise ValueError("O valor deve estar entre R$ 0,00 e R$ 99.999.999,99.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(cents: int | None) -> str:
    cents = int(cents or 0)
    return f"R$ {cents // 100:,.0f},{cents % 100:02d}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_date(raw: str, fallback: date | None = None) -> str:
    if not raw:
        return (fallback or date.today()).isoformat()
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Informe uma data válida.") from exc
    if parsed < date(2000, 1, 1) or parsed > date.today() + timedelta(days=1):
        raise ValueError("A data está fora do intervalo permitido.")
    return parsed.isoformat()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db_path = Path(current_app.config["DATABASE_PATH"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        g.db = conn
    return g.db


def init_db(app: Flask) -> None:
    db_path = Path(app.config["DATABASE_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
            contact TEXT NOT NULL DEFAULT '' CHECK(length(contact) <= 80),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
            tool TEXT NOT NULL CHECK(length(tool) <= 60),
            plan TEXT NOT NULL CHECK(length(plan) <= 40),
            amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
            payment_method TEXT NOT NULL CHECK(length(payment_method) <= 30),
            status TEXT NOT NULL CHECK(status IN ('Pago','Pendente','Cancelado')),
            sold_on TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sales_sold_on ON sales(sold_on);
        CREATE INDEX IF NOT EXISTS idx_sales_status_sold_on ON sales(status, sold_on);
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL CHECK(length(category) <= 80),
            description TEXT NOT NULL CHECK(length(description) BETWEEN 1 AND 180),
            amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
            spent_on TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_expenses_spent_on ON expenses(spent_on);
        CREATE TABLE IF NOT EXISTS ad_spend (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL CHECK(length(platform) <= 40),
            campaign TEXT NOT NULL DEFAULT '' CHECK(length(campaign) <= 120),
            amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
            spent_on TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ad_spend_spent_on ON ad_spend(spent_on);
        CREATE TABLE IF NOT EXISTS admin_sessions (
            token_hash TEXT PRIMARY KEY,
            csrf_token TEXT NOT NULL,
            username TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_sessions_expiry ON admin_sessions(expires_at);
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            attempted_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time ON login_attempts(ip_address, attempted_at);
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at);
        INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS product_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT NOT NULL, plan TEXT NOT NULL,
            amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(amount_cents >= 0),
            duration_hours INTEGER NOT NULL DEFAULT 12 CHECK(duration_hours > 0),
            active INTEGER NOT NULL DEFAULT 1, UNIQUE(tool, plan)
        );
        """)
        for table, column, definition in [
            ("customers", "phone", "TEXT NOT NULL DEFAULT ''"),
            ("sales", "account_login", "TEXT NOT NULL DEFAULT ''"),
            ("sales", "account_password", "TEXT NOT NULL DEFAULT ''"),
            ("sales", "expires_at", "TEXT NOT NULL DEFAULT ''"),
            ("sales", "duration_hours", "INTEGER NOT NULL DEFAULT 12"),
        ]:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_phone_unique ON customers(phone) WHERE phone != ''")
        defaults = [("Unlock Tool","12 horas",1200,12),("Unlock Tool","3 meses",6000,2160),("Unlock Tool","6 meses",9000,4320),("TSM Tool","12 horas",2500,12),("AMT Tool","12 horas",2500,12),("TFM Tool","12 horas",2500,12),("Borneo","3 dias",4000,72)]
        for tool, plan, amount, hours in defaults:
            conn.execute("INSERT OR IGNORE INTO product_plans(tool,plan,amount_cents,duration_hours,active) VALUES(?,?,?,?,1)", (tool, plan, amount, hours))
        conn.commit()
    finally:
        conn.close()



def get_product_plans(db: sqlite3.Connection, active_only: bool = True) -> dict[str, dict[str, dict[str, int]]]:
    where = "WHERE active=1" if active_only else ""
    rows = db.execute(f"SELECT tool,plan,amount_cents,duration_hours,active FROM product_plans {where} ORDER BY tool,id").fetchall()
    result: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        result.setdefault(row["tool"], {})[row["plan"]] = {"amount_cents": row["amount_cents"], "duration_hours": row["duration_hours"], "active": row["active"]}
    return result


def normalize_phone(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def expiry_for(created_at: str, duration_hours: int) -> str:
    try: start = datetime.fromisoformat(created_at)
    except (TypeError, ValueError): start = datetime.now(timezone.utc)
    if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
    return (start + timedelta(hours=int(duration_hours))).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_client_ip() -> str:
    # Do not trust X-Forwarded-For here; the reverse proxy must be configured deliberately.
    return (request.remote_addr or "unknown")[:64]


def create_session(db: sqlite3.Connection, username: str | None = None) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = utcnow()
    expires = now + timedelta(seconds=SESSION_TTL_SECONDS)
    db.execute(
        "INSERT INTO admin_sessions(token_hash, csrf_token, username, created_at, expires_at) VALUES(?,?,?,?,?)",
        (hash_token(token), csrf, username, now.isoformat(), expires.isoformat()),
    )
    return token, csrf


def current_session() -> sqlite3.Row | None:
    if getattr(g, "admin_session", None) is not None:
        return g.admin_session
    g.admin_session = None
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 200:
        return None
    db = get_db()
    row = db.execute(
        "SELECT token_hash, csrf_token, username, expires_at FROM admin_sessions WHERE token_hash = ?",
        (hash_token(token),),
    ).fetchone()
    if not row:
        return None
    try:
        expiry = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (row["token_hash"],))
        db.commit()
        return None
    if expiry <= utcnow():
        db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (row["token_hash"],))
        db.commit()
        return None
    g.cookie_token = token
    new_expiry = (utcnow() + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat()
    db.execute("UPDATE admin_sessions SET expires_at = ? WHERE token_hash = ?", (new_expiry, row["token_hash"]))
    db.commit()
    g.admin_session = row
    return row


def require_csrf() -> None:
    session = current_session()
    supplied = request.form.get("csrf_token", "")
    if not session or not supplied or not hmac.compare_digest(supplied, session["csrf_token"]):
        abort(400, description="Token de segurança inválido. Atualize a página e tente novamente.")


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        session = current_session()
        if not session or not session["username"]:
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def audit(event_type: str, entity_type: str, entity_id: int | None = None) -> None:
    get_db().execute(
        "INSERT INTO audit_events(event_type, entity_type, entity_id, created_at) VALUES(?,?,?,?)",
        (event_type[:60], entity_type[:60], entity_id, iso_now()),
    )
    get_db().commit()


def render_page(template: str, **context):
    session = current_session()
    context.setdefault("csrf_token", session["csrf_token"] if session else "")
    context.setdefault("username", session["username"] if session else "")
    context.setdefault("money", money)
    return render_template(template, **context)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/admin/static", template_folder="templates")
    app.config.update(
        DATABASE_PATH=os.environ.get("DATABASE_PATH", str(APP_DIR / "instance" / "unlockfacil.sqlite3")),
        ADMIN_USERNAME=os.environ.get("ADMIN_USERNAME", ""),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", ""),
        COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "true").lower() == "true",
        TRUST_PROXY=os.environ.get("TRUST_PROXY", "false").lower() == "true",
        SESSION_TTL_SECONDS=SESSION_TTL_SECONDS,
        MAX_CONTENT_LENGTH=32 * 1024,
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["TESTING"] and (not app.config["ADMIN_USERNAME"] or not app.config["ADMIN_PASSWORD"]):
        raise RuntimeError("Configure ADMIN_USERNAME e ADMIN_PASSWORD antes de iniciar o painel.")

    # Trust exactly one reverse proxy only when explicitly enabled and the app is loopback-only.
    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    app.config["g_app_config"] = {
        "DATABASE_PATH": app.config["DATABASE_PATH"]
    }

    @app.before_request
    def set_request_config():
        g.app_config = app.config["g_app_config"]
        g.admin_session = None
        g.new_session_token = None
        g.cookie_token = None
        g.clear_session_cookie = False
        # Keep non-admin public routes out of the admin application.
        if not request.path.startswith("/admin"):
            abort(404)
        if request.path.startswith("/admin/static/"):
            return None
        # Prune expired sessions and short-lived login attempts opportunistically.
        db = get_db()
        db.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (utcnow().isoformat(),))
        db.execute("DELETE FROM login_attempts WHERE attempted_at <= ?", ((utcnow() - timedelta(seconds=LOGIN_WINDOW_SECONDS)).isoformat(),))
        db.commit()
        token = request.cookies.get(SESSION_COOKIE, "")
        session = current_session()
        if not session:
            new_token, csrf = create_session(db)
            db.commit()
            g.new_session_token = new_token
            g.admin_session = {
                "token_hash": hash_token(new_token),
                "csrf_token": csrf,
                "username": None,
                "expires_at": (utcnow() + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat(),
            }
        return None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        cookie_token = getattr(g, "new_session_token", None) or getattr(g, "cookie_token", None)
        if cookie_token and not getattr(g, "clear_session_cookie", False):
            response.set_cookie(
                SESSION_COOKIE,
                cookie_token,
                max_age=SESSION_TTL_SECONDS,
                httponly=True,
                secure=app.config["COOKIE_SECURE"],
                samesite="Strict",
                path="/admin",
            )
        return response

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message=getattr(error, "description", "Requisição inválida.")), 400

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="Página não encontrada."), 404

    @app.errorhandler(413)
    def too_large(_error):
        return render_template("error.html", code=413, message="Requisição muito grande."), 413

    @app.errorhandler(500)
    def server_error(_error):
        app.logger.error("Erro interno ao processar requisição administrativa.")
        return render_template("error.html", code=500, message="Ocorreu um erro interno. Tente novamente."), 500

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        session = current_session()
        if session and session["username"]:
            return redirect(url_for("dashboard"))
        if request.method == "GET":
            return render_page("login.html")
        require_csrf()
        username = request.form.get("username", "")[:120]
        password = request.form.get("password", "")[:1024]
        db = get_db()
        ip = get_client_ip()
        recent = db.execute(
            "SELECT COUNT(*) AS n FROM login_attempts WHERE ip_address = ? AND attempted_at > ?",
            (ip, (utcnow() - timedelta(seconds=LOGIN_WINDOW_SECONDS)).isoformat()),
        ).fetchone()["n"]
        if recent >= LOGIN_MAX_ATTEMPTS:
            return render_page("login.html", error="Não foi possível entrar. Aguarde alguns minutos e tente novamente."), 429
        db.execute("INSERT INTO login_attempts(ip_address, attempted_at) VALUES(?,?)", (ip, iso_now()))
        db.commit()
        credentials_ok = (
            hmac.compare_digest(username, app.config["ADMIN_USERNAME"])
            and hmac.compare_digest(password, app.config["ADMIN_PASSWORD"])
        )
        if not credentials_ok:
            audit("login_failed", "authentication", None)
            return render_page("login.html", error="Login ou senha incorretos. Tente novamente."), 401
        # Rotate the session identifier on authentication to prevent fixation.
        db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (session["token_hash"],))
        new_token, _csrf = create_session(db, "admin")
        db.commit()
        g.new_session_token = new_token
        g.admin_session = db.execute(
            "SELECT token_hash, csrf_token, username, expires_at FROM admin_sessions WHERE token_hash = ?",
            (hash_token(new_token),),
        ).fetchone()
        audit("login_success", "session", None)
        return redirect(url_for("dashboard"))

    @app.route("/admin/logout", methods=["POST"])
    @admin_required
    def admin_logout():
        require_csrf()
        session = current_session()
        get_db().execute("DELETE FROM admin_sessions WHERE token_hash = ?", (session["token_hash"],))
        get_db().commit()
        response = redirect(url_for("admin_login"))
        g.cookie_token = None
        g.clear_session_cookie = True
        response.delete_cookie(SESSION_COOKIE, path="/admin")
        audit("logout", "session", None)
        return response

    @app.route("/admin")
    @admin_required
    def dashboard():
        db=get_db(); today=date.today().isoformat(); month_start=date.today().replace(day=1).isoformat()
        prev_month_last=date.today().replace(day=1)-timedelta(days=1); prev_end=prev_month_last.replace(day=min(date.today().day,prev_month_last.day)); prev_start=prev_month_last.replace(day=1).isoformat()
        def total(table,col,start=None,end=None,condition=""):
            clauses=[]; args=[]
            if start: clauses.append(f"{col}>=?"); args.append(start)
            if end: clauses.append(f"{col}<=?"); args.append(end)
            if condition: clauses.append(condition)
            where=" WHERE "+" AND ".join(clauses) if clauses else ""
            return db.execute(f"SELECT COALESCE(SUM(amount_cents),0) n FROM {table}{where}",args).fetchone()["n"]
        def count_sales(start=None,end=None):
            clauses=["status='Pago'"]; args=[]
            if start: clauses.append("sold_on>=?"); args.append(start)
            if end: clauses.append("sold_on<=?"); args.append(end)
            return db.execute("SELECT COUNT(*) n FROM sales WHERE "+" AND ".join(clauses),args).fetchone()["n"]
        revenue=total("sales","sold_on",month_start,today,"status='Pago'"); expenses=total("expenses","spent_on",month_start,today); ads=total("ad_spend","spent_on",month_start,today)
        prev_revenue=total("sales","sold_on",prev_start,prev_end.isoformat(),"status='Pago'")
        all_revenue=total("sales","sold_on",condition="status='Pago'"); all_expenses=total("expenses","spent_on"); all_ads=total("ad_spend","spent_on")
        recent=db.execute("SELECT s.*,COALESCE(NULLIF(c.phone,''),NULLIF(c.contact,''),c.name,'—') customer_phone FROM sales s LEFT JOIN customers c ON c.id=s.customer_id ORDER BY s.id DESC LIMIT 12").fetchall()
        daily=db.execute("SELECT day,COALESCE(sales_count,0) count,COALESCE(revenue,0) revenue,COALESCE(expenses,0) expenses,COALESCE(ads,0) ads FROM (SELECT day FROM (SELECT sold_on day FROM sales WHERE status='Pago' UNION SELECT spent_on day FROM expenses UNION SELECT spent_on day FROM ad_spend) WHERE day>=date(?, '-29 days') AND day<=?) d LEFT JOIN (SELECT sold_on day,COUNT(*) sales_count,SUM(amount_cents) revenue FROM sales WHERE status='Pago' GROUP BY sold_on) s USING(day) LEFT JOIN (SELECT spent_on day,SUM(amount_cents) expenses FROM expenses GROUP BY spent_on) e USING(day) LEFT JOIN (SELECT spent_on day,SUM(amount_cents) ads FROM ad_spend GROUP BY spent_on) a USING(day) ORDER BY day DESC",(today,today)).fetchall()
        by_tool=db.execute("SELECT tool,COUNT(*) count,SUM(amount_cents) revenue FROM sales WHERE status='Pago' GROUP BY tool ORDER BY revenue DESC").fetchall()
        month_by_tool=db.execute("SELECT tool,COUNT(*) count,SUM(amount_cents) revenue FROM sales WHERE status='Pago' AND sold_on BETWEEN ? AND ? GROUP BY tool ORDER BY revenue DESC",(month_start,today)).fetchall()
        return render_page("dashboard.html", revenue=revenue,count=count_sales(month_start,today),expenses=expenses,ads=ads,net=revenue-expenses-ads,
            all_revenue=all_revenue,all_net=all_revenue-all_expenses-all_ads,prev_revenue=prev_revenue,previous_count=count_sales(prev_start,prev_end.isoformat()),
            recent=recent,today=today,month_start=month_start,daily=daily,by_tool=by_tool,month_by_tool=month_by_tool,plans=get_product_plans(db),all_expenses=all_expenses,all_ads=all_ads)

    @app.route("/admin/vendas", methods=["GET", "POST"])
    @admin_required
    def sales_page():
        db=get_db(); plans=get_product_plans(db)
        if request.method=="POST":
            require_csrf(); tool=request.form.get("tool",""); plan=request.form.get("plan","")
            if tool not in plans or plan not in plans[tool]: abort(400,description="Ferramenta ou plano inválido.")
            phone=normalize_phone(request.form.get("phone",""))
            if len(phone)<8 or len(phone)>20: abort(400,description="Informe um telefone válido com DDD.")
            try: amount=parse_money_to_cents(request.form.get("amount","")); sold_on=parse_date(request.form.get("sold_on",""))
            except ValueError as exc: abort(400,description=str(exc))
            payment=request.form.get("payment_method","Pix")
            if payment not in VALID_PAYMENTS: abort(400,description="Forma de pagamento inválida.")
            login_name=request.form.get("account_login","").strip()[:180]; account_password=request.form.get("account_password","").strip()[:180]
            if not login_name or not account_password: abort(400,description="Informe o login e a senha da conta fornecida.")
            customer=db.execute("SELECT id FROM customers WHERE phone=?",(phone,)).fetchone()
            if customer: customer_id=customer["id"]
            else: customer_id=db.execute("INSERT INTO customers(name,contact,phone,created_at) VALUES(?,?,?,?)",(phone,phone,phone,iso_now())).lastrowid
            created=iso_now(); hours=plans[tool][plan]["duration_hours"]; expires=expiry_for(created,hours)
            cur=db.execute("INSERT INTO sales(customer_id,tool,plan,amount_cents,payment_method,status,sold_on,created_at,account_login,account_password,expires_at,duration_hours) VALUES(?,?,?,?,?,'Pago',?,?,?,?,?,?)",
                (customer_id,tool,plan,amount,payment,sold_on,created,login_name,account_password,expires,hours))
            db.commit(); audit("sale_created","sale",cur.lastrowid); return redirect(url_for("sales_page"))
        query_phone=normalize_phone(request.args.get("telefone",""))
        if query_phone:
            sales=db.execute("SELECT s.*,COALESCE(NULLIF(c.phone,''),NULLIF(c.contact,''),c.name,'—') customer_phone FROM sales s LEFT JOIN customers c ON c.id=s.customer_id WHERE c.phone LIKE ? OR c.contact LIKE ? ORDER BY s.sold_on DESC,s.id DESC LIMIT 500",(f"%{query_phone}%",f"%{query_phone}%")).fetchall()
        else:
            sales=db.execute("SELECT s.*,COALESCE(NULLIF(c.phone,''),NULLIF(c.contact,''),c.name,'—') customer_phone FROM sales s LEFT JOIN customers c ON c.id=s.customer_id ORDER BY s.sold_on DESC,s.id DESC LIMIT 300").fetchall()
        return render_page("sales.html",sales=sales,tools=plans,payments=VALID_PAYMENTS,today=date.today().isoformat(),search_phone=request.args.get("telefone",""))

    @app.post("/admin/vendas/<int:sale_id>/cancelar")
    @admin_required
    def cancel_sale(sale_id: int):
        require_csrf()
        db = get_db()
        cur = db.execute("UPDATE sales SET status='Cancelado' WHERE id=? AND status!='Cancelado'", (sale_id,))
        if cur.rowcount != 1:
            abort(404)
        db.commit()
        audit("sale_cancelled", "sale", sale_id)
        return redirect(url_for("sales_page"))

    @app.route("/admin/clientes", methods=["GET"])
    @admin_required
    def customers_page():
        db=get_db(); phone=normalize_phone(request.args.get("telefone",""))
        customers=db.execute("SELECT c.id,c.phone,COUNT(s.id) sales_count,COALESCE(SUM(CASE WHEN s.status='Pago' THEN s.amount_cents ELSE 0 END),0) paid_total,MAX(s.sold_on) last_sale FROM customers c LEFT JOIN sales s ON s.customer_id=c.id WHERE (?='' OR c.phone LIKE ?) GROUP BY c.id ORDER BY last_sale DESC LIMIT 500",(phone,f"%{phone}%")).fetchall()
        history=[]
        if phone: history=db.execute("SELECT s.*,c.phone customer_phone FROM sales s JOIN customers c ON c.id=s.customer_id WHERE c.phone LIKE ? ORDER BY s.sold_on DESC,s.id DESC LIMIT 500",(f"%{phone}%",)).fetchall()
        return render_page("customers.html",customers=customers,history=history,search_phone=request.args.get("telefone",""))

    @app.route("/admin/vencimentos")
    @admin_required
    def expirations_page():
        db=get_db(); now=utcnow().isoformat()
        expired=db.execute("SELECT s.*,c.phone customer_phone FROM sales s LEFT JOIN customers c ON c.id=s.customer_id WHERE s.status='Pago' AND s.expires_at!='' AND s.expires_at<=? ORDER BY s.expires_at DESC",(now,)).fetchall()
        grouped={}
        for row in expired: grouped.setdefault(row['tool'],[]).append(row)
        return render_page('expirations.html',expired=expired,grouped=grouped,now=now)

    @app.route("/admin/catalogo", methods=["GET","POST"])
    @admin_required
    def catalog_page():
        db=get_db()
        if request.method=='POST':
            require_csrf(); action=request.form.get('action','save'); tool=request.form.get('tool','').strip()[:60]; plan=request.form.get('plan','').strip()[:40]
            if not tool or not plan: abort(400,description='Informe ferramenta e plano.')
            if action=='toggle':
                row=db.execute('SELECT active FROM product_plans WHERE tool=? AND plan=?',(tool,plan)).fetchone()
                if row: db.execute('UPDATE product_plans SET active=? WHERE tool=? AND plan=?',(0 if row['active'] else 1,tool,plan))
            else:
                try: amount=parse_money_to_cents(request.form.get('amount','')); hours=int(request.form.get('duration_hours',''))
                except (ValueError,TypeError): abort(400,description='Informe preço e duração válidos.')
                if hours<1 or hours>87600: abort(400,description='A duração deve ficar entre 1 hora e 10 anos.')
                db.execute("INSERT INTO product_plans(tool,plan,amount_cents,duration_hours,active) VALUES(?,?,?,?,1) ON CONFLICT(tool,plan) DO UPDATE SET amount_cents=excluded.amount_cents,duration_hours=excluded.duration_hours,active=1",(tool,plan,amount,hours))
            db.commit(); audit('catalog_updated','product_plan',None); return redirect(url_for('catalog_page'))
        products=db.execute('SELECT * FROM product_plans ORDER BY tool,id').fetchall()
        return render_page('catalog.html',products=products)

    @app.route("/admin/despesas", methods=["GET", "POST"])
    @admin_required
    def expenses_page():
        db = get_db()
        if request.method == "POST":
            require_csrf()
            category = request.form.get("category", "")
            description = request.form.get("description", "").strip()
            try:
                amount = parse_money_to_cents(request.form.get("amount", ""))
                spent_on = parse_date(request.form.get("spent_on", ""))
            except ValueError as exc:
                return render_page("expenses.html", expenses=[], categories=VALID_EXPENSE_CATEGORIES, today=date.today().isoformat(), error=str(exc)), 400
            if category not in VALID_EXPENSE_CATEGORIES or not description or len(description) > 180:
                abort(400, description="Categoria ou descrição inválida.")
            cur = db.execute("INSERT INTO expenses(category,description,amount_cents,spent_on,created_at) VALUES(?,?,?,?,?)",
                             (category, description, amount, spent_on, iso_now()))
            db.commit()
            audit("expense_created", "expense", cur.lastrowid)
            return redirect(url_for("expenses_page"))
        expenses = db.execute("SELECT * FROM expenses ORDER BY spent_on DESC,id DESC LIMIT 200").fetchall()
        return render_page("expenses.html", expenses=expenses, categories=VALID_EXPENSE_CATEGORIES, today=date.today().isoformat())

    @app.route("/admin/anuncios", methods=["GET", "POST"])
    @admin_required
    def ads_page():
        db = get_db()
        if request.method == "POST":
            require_csrf()
            platform = request.form.get("platform", "")
            campaign = request.form.get("campaign", "").strip()
            try:
                amount = parse_money_to_cents(request.form.get("amount", ""))
                spent_on = parse_date(request.form.get("spent_on", ""))
            except ValueError as exc:
                return render_page("ads.html", ads=[], platforms=VALID_AD_PLATFORMS, today=date.today().isoformat(), error=str(exc)), 400
            if platform not in VALID_AD_PLATFORMS or len(campaign) > 120:
                abort(400, description="Plataforma ou campanha inválida.")
            cur = db.execute("INSERT INTO ad_spend(platform,campaign,amount_cents,spent_on,created_at) VALUES(?,?,?,?,?)",
                             (platform, campaign, amount, spent_on, iso_now()))
            db.commit()
            audit("ad_spend_created", "ad_spend", cur.lastrowid)
            return redirect(url_for("ads_page"))
        ads = db.execute("SELECT * FROM ad_spend ORDER BY spent_on DESC,id DESC LIMIT 200").fetchall()
        return render_page("ads.html", ads=ads, platforms=VALID_AD_PLATFORMS, today=date.today().isoformat())

    @app.get("/admin/health")
    def health():
        # Minimal liveness response; intentionally no DB details, version, or configuration.
        return {"status": "ok"}, 200

    with app.app_context():
        init_db(app)
    return app


app = create_app() if os.environ.get("ADMIN_USERNAME") and os.environ.get("ADMIN_PASSWORD") else None

if __name__ == "__main__":
    if app is None:
        raise SystemExit("Configure ADMIN_USERNAME e ADMIN_PASSWORD. Consulte README.md.")
    app.run(host="127.0.0.1", port=8000, debug=False)
