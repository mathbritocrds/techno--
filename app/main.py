"""SIGI Gestão - API (FastAPI + SQLite).
Rodar (na raiz do repositório):  uvicorn app.main:app --reload
Docs:   http://localhost:8000/docs
"""
import base64, binascii, csv, hashlib, hmac, io, logging, math, os, secrets, sqlite3, time
import asyncio, json
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

import psycopg2
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.database import connect_postgres
from app.security import generate_totp_secret, provisioning_uri, verify_totp

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.abspath(os.getenv("DB_PATH") or os.path.join(APP_DIR, "..", "data", "flux.db"))
SUPABASE_DATABASE_URL = os.getenv("SUPABASE_DATABASE_URL", "").strip()
os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")                    # opcional: acesso direto via API (vazio = desligado)
SESSION_HOURS = 12
TZ_OFFSET = float(os.getenv("TZ_OFFSET_HOURS", "-3"))         # fuso da empresa (Brasília = -3)
MAX_GPS_ERROR_M = 50      # GPS impreciso demais é recusado
MAX_SPREAD_M = 100        # amostras muito dispersas = sinal instável
MAX_SPEED_MS = 55         # ~200 km/h: acima disso, deslocamento impossível
MAX_PIN_TRIES = 5
DAILY_HOURS = 8
OVERTIME_RATE = 1.5       # hora extra a 50% (estimativa)

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins(
  user TEXT PRIMARY KEY, salt TEXT, hash TEXT, role TEXT NOT NULL DEFAULT 'admin',
  department_id INTEGER, totp_secret TEXT, totp_enabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user TEXT, expires REAL,
    role TEXT NOT NULL DEFAULT 'admin', subject_id INTEGER);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS employees(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, cpf TEXT DEFAULT '', role TEXT DEFAULT '',
  salary REAL NOT NULL DEFAULT 0, benefits REAL NOT NULL DEFAULT 0,
  pin_salt TEXT, pin_hash TEXT, failed_pins INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
  department_id INTEGER);
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, material REAL DEFAULT 0,
  labor REAL DEFAULT 0, overhead REAL DEFAULT 0, price REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, assignee TEXT DEFAULT '',
  due TEXT DEFAULT '', status INTEGER DEFAULT 0,
  priority TEXT DEFAULT 'media', progress INTEGER DEFAULT 0,
  approval TEXT DEFAULT 'pendente', severity TEXT DEFAULT 'normal',
  department_id INTEGER, labor_budget_hours REAL DEFAULT 0,
  cost_ceiling REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS task_checklist(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS task_tags(
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  tag TEXT NOT NULL, PRIMARY KEY(task_id,tag));
CREATE TABLE IF NOT EXISTS task_attachments(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, content TEXT NOT NULL,
  uploaded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_history(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  actor TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_materials(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  material_id INTEGER NOT NULL REFERENCES materials(id), quantity REAL NOT NULL,
  consumed_quantity REAL NOT NULL DEFAULT 0, unit_cost REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS time_entries(
  id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
  kind TEXT NOT NULL, at TEXT NOT NULL, lat REAL, lng REAL,
  distance_m REAL, accepted INTEGER NOT NULL, reason TEXT DEFAULT '',
  cpf TEXT DEFAULT '', nsr INTEGER, prev_hash TEXT, hash TEXT, samples INTEGER DEFAULT 1,
  spread_m REAL DEFAULT 0, task_id INTEGER REFERENCES tasks(id), labor_rate REAL);
CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, description TEXT NOT NULL,
    amount REAL NOT NULL, due TEXT NOT NULL, paid INTEGER DEFAULT 0, paid_at TEXT,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS materials(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, unit TEXT DEFAULT 'un',
  stock REAL NOT NULL DEFAULT 0, unit_cost REAL NOT NULL DEFAULT 0,
  minimum_stock REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS departments(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, lead TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS spaces(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT DEFAULT 'gestao',
  admin_email TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS space_bookings(
  id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
  department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
  title TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  booked_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS automation_rules(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, trigger TEXT NOT NULL,
  threshold REAL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS automation_runs(
  rule_id INTEGER NOT NULL REFERENCES automation_rules(id) ON DELETE CASCADE,
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, PRIMARY KEY(rule_id,task_id));
CREATE TABLE IF NOT EXISTS cost_analyses(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, revenue REAL NOT NULL DEFAULT 0,
  material REAL NOT NULL DEFAULT 0, opex REAL NOT NULL DEFAULT 0, tax REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS integrations(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
  endpoint TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT '', action TEXT NOT NULL, resource TEXT NOT NULL,
  status INTEGER NOT NULL, ip TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS notification_channels(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, recipient TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('team','private')),
    employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
    sender_role TEXT NOT NULL CHECK(sender_role IN ('admin','employee')),
    sender_employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    sender_name TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
"""
MIGRATIONS = ["ALTER TABLE employees ADD COLUMN cpf TEXT DEFAULT ''",
                            "ALTER TABLE sessions ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'",
                            "ALTER TABLE sessions ADD COLUMN subject_id INTEGER",
                            "ALTER TABLE employees ADD COLUMN account_email TEXT DEFAULT ''",
                            "ALTER TABLE employees ADD COLUMN account_salt TEXT",
                            "ALTER TABLE employees ADD COLUMN account_hash TEXT",
              "ALTER TABLE transactions ADD COLUMN department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL",
              "ALTER TABLE time_entries ADD COLUMN cpf TEXT DEFAULT ''",
              "ALTER TABLE time_entries ADD COLUMN nsr INTEGER",
              "ALTER TABLE time_entries ADD COLUMN prev_hash TEXT",
              "ALTER TABLE time_entries ADD COLUMN hash TEXT",
              "ALTER TABLE time_entries ADD COLUMN samples INTEGER DEFAULT 1",
              "ALTER TABLE time_entries ADD COLUMN spread_m REAL DEFAULT 0",
              "ALTER TABLE employees ADD COLUMN department_id INTEGER",
              "ALTER TABLE materials ADD COLUMN minimum_stock REAL NOT NULL DEFAULT 0",
              "ALTER TABLE admins ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'",
              "ALTER TABLE admins ADD COLUMN department_id INTEGER",
              "ALTER TABLE admins ADD COLUMN totp_secret TEXT",
              "ALTER TABLE admins ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'media'",
              "ALTER TABLE tasks ADD COLUMN progress INTEGER DEFAULT 0",
              "ALTER TABLE tasks ADD COLUMN approval TEXT DEFAULT 'pendente'",
              "ALTER TABLE tasks ADD COLUMN severity TEXT DEFAULT 'normal'",
              "ALTER TABLE tasks ADD COLUMN department_id INTEGER",
              "ALTER TABLE tasks ADD COLUMN labor_budget_hours REAL DEFAULT 0",
              "ALTER TABLE tasks ADD COLUMN cost_ceiling REAL DEFAULT 0",
              "ALTER TABLE time_entries ADD COLUMN task_id INTEGER",
              "ALTER TABLE time_entries ADD COLUMN labor_rate REAL"]

@contextmanager
def db():
    if SUPABASE_DATABASE_URL:
        con = connect_postgres(SUPABASE_DATABASE_URL)
    else:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()

with db() as c:
    if SUPABASE_DATABASE_URL:
        schema = Path(APP_DIR, "..", "supabase", "schema.sql").read_text()
        for statement in schema.split(";"):
            if statement.strip():
                c.execute(statement)
    else:
        c.executescript(SCHEMA)
        for m in MIGRATIONS:                 # bancos criados em versões anteriores
            try: c.execute(m)
            except sqlite3.OperationalError: pass
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_nsr ON time_entries(nsr) WHERE nsr IS NOT NULL")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_employee_account_email ON employees(lower(account_email)) WHERE account_email IS NOT NULL AND account_email != ''")

app = FastAPI(title="SIGI Gestão API")

# ---------- Utilidades ----------
def rows(cur): return [dict(r) for r in cur.fetchall()]
def utcnow(): return datetime.now(timezone.utc)
def iso(dt): return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
def local(dt): return dt.astimezone(timezone(timedelta(hours=TZ_OFFSET)))

def day_range(d: date):
    s = datetime(d.year, d.month, d.day, tzinfo=timezone(timedelta(hours=TZ_OFFSET)))
    return iso(s), iso(s + timedelta(days=1))

def month_bounds(ym: str):
    try:
        y, m = map(int, ym.split("-")); a = date(y, m, 1)
    except Exception:
        raise HTTPException(400, "Mês inválido. Use AAAA-MM.")
    return day_range(a)[0], day_range(date(y + (m == 12), m % 12 + 1, 1))[0]

def hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 120_000).hex()

def haversine(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12_742_000 * math.asin(math.sqrt(x))

def settings(c): return {r["k"]: r["v"] for r in c.execute("SELECT k,v FROM settings")}
def workplace_of(s): return {k: float(s[k]) for k in ("lat", "lng", "radius_m")} if "lat" in s else None

class LiveClients:
    def __init__(self):
        self.clients = set()

    async def broadcast(self, event):
        stale = set()
        for client in tuple(self.clients):
            try:
                await client.send_json(event)
            except WebSocketDisconnect:
                stale.add(client)
        self.clients.difference_update(stale)

live_clients = LiveClients()

@app.middleware("http")
async def audit_mutations(request, call_next):
    token = request.headers.get("authorization", "")
    actor, role = "", ""
    if token.startswith("Bearer "):
        token_hash = hashlib.sha256(token[7:].encode()).hexdigest()
        with db() as c:
            session = c.execute('SELECT "user",role FROM sessions WHERE token=? AND expires>?',
                                (token_hash, time.time())).fetchone()
            if session:
                actor, role = session["user"], session["role"]
    response = await call_next(request)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path not in {
        "/auth/login", "/auth/employee/login", "/auth/register", "/auth/logout",
    }:
        with db() as c:
            c.execute(
                "INSERT INTO audit_log(at,actor,role,action,resource,status,ip) VALUES(?,?,?,?,?,?,?)",
                (iso(utcnow()), actor, role, request.method, request.url.path, response.status_code,
                 request.client.host if request.client else ""),
            )
        if response.status_code < 400:
            await live_clients.broadcast({"type": "change", "resource": request.url.path})
    return response

def admin(authorization: str = Header(default=""), x_admin_token: str = Header(default="")):
    if authorization.startswith("Bearer "):
        h = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with db() as c:
            r = c.execute('SELECT "user",expires,role FROM sessions WHERE token=?', (h,)).fetchone()
            account = c.execute('SELECT role FROM admins WHERE "user"=?', (r["user"],)).fetchone() if r else None
        if r and r["expires"] > time.time() and r["role"] == "admin" and account and account["role"] == "admin":
            return
    if x_admin_token and hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
        return
    raise HTTPException(401, "Sessão inválida ou expirada. Entre novamente.")

def admin_context(authorization: str = Header(default=""), x_admin_token: str = Header(default="")):
    admin(authorization, x_admin_token)
    if authorization.startswith("Bearer "):
        return principal_for_token(authorization[7:])
    return {"role": "admin", "user": "admin-token", "department_id": None}

# ---------- Conta da empresa e login ----------
_fails: dict = {}

class Login(BaseModel):
    password: str
    user: str = ""
    email: str = ""
    totp_code: str = ""

class Registration(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    company_name: str = Field(min_length=2, max_length=160)
    cnpj: str = Field(default="", pattern=r"^(\d{14})?$")
    role: Literal["manager", "operator"] = "manager"
    department_id: Optional[int] = None

def session_user(c, authorization, now):
    if not authorization.startswith("Bearer "):
        return None
    token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    session = c.execute('SELECT "user",expires,role FROM sessions WHERE token=?', (token_hash,)).fetchone()
    if not session or session["expires"] <= now or session["role"] != "admin":
        return None
    account = c.execute('SELECT role FROM admins WHERE "user"=?', (session["user"],)).fetchone()
    return session["user"] if account and account["role"] == "admin" else None

def principal_for_token(token: str):
    authorization = "Bearer " + token
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sessão inválida ou expirada. Entre novamente.")
    token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with db() as c:
        session = c.execute('SELECT "user",expires,role,subject_id FROM sessions WHERE token=?', (token_hash,)).fetchone()
        if not session or session["expires"] <= time.time():
            raise HTTPException(401, "Sessão inválida ou expirada. Entre novamente.")
        if session["role"] in ("admin", "manager", "operator"):
            account = c.execute('SELECT role,department_id FROM admins WHERE "user"=?',
                                (session["user"],)).fetchone()
            if not account or account["role"] != session["role"]:
                raise HTTPException(401, "Sessão inválida ou expirada. Entre novamente.")
            return {"role": account["role"], "user": session["user"], "employee_id": None,
                    "department_id": account["department_id"]}
        employee = c.execute("SELECT id,name FROM employees WHERE id=? AND lower(account_email)=lower(?) AND active=1",
                             (session["subject_id"], session["user"])).fetchone()
        if not employee:
            raise HTTPException(401, "Sessão inválida ou expirada. Entre novamente.")
        return {"role": "employee", "user": session["user"], "employee_id": employee["id"], "name": employee["name"]}

def principal(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sessão inválida ou expirada. Entre novamente.")
    return principal_for_token(authorization[7:])

def manager_or_admin(who: dict = Depends(principal)):
    if who["role"] not in ("admin", "manager", "operator"):
        raise HTTPException(403, "Este recurso exige permissão de gestor.")
    return who

@app.get("/auth/status")
def auth_status():
    with db() as c:
        setup_required = not c.execute("SELECT 1 FROM admins LIMIT 1").fetchone()
    return {"setup_required": setup_required}

@app.post("/auth/register", status_code=201)
def register_account(b: Registration, authorization: str = Header(default="")):
    email, now = b.email.strip().lower(), time.time()
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        account_exists = bool(c.execute("SELECT 1 FROM admins LIMIT 1").fetchone())
        creator = session_user(c, authorization, now)
        if account_exists and not creator:
            raise HTTPException(401, "Entre como administrador para criar outro acesso.")
        if c.execute('SELECT 1 FROM admins WHERE "user"=?', (email,)).fetchone():
            raise HTTPException(409, "Este e-mail já possui acesso.")
        current = settings(c)
        company_name = current.get("co_name", "").strip()
        company_cnpj = current.get("co_cnpj", "").strip()
        if company_name and (company_name.casefold() != b.company_name.strip().casefold()
                             or company_cnpj != b.cnpj):
            raise HTTPException(409, "Esta instalação já pertence a outra empresa.")
        role = "admin" if not account_exists else b.role
        department_id = b.department_id if role == "manager" else None
        if department_id is not None and not c.execute(
            "SELECT 1 FROM departments WHERE id=?", (department_id,)
        ).fetchone():
            raise HTTPException(404, "Departamento não encontrado.")
        salt = secrets.token_hex(16)
        c.execute('INSERT INTO admins("user",salt,hash,role,department_id) VALUES(?,?,?,?,?)',
                  (email, salt, hash_pin(b.password, salt), role, department_id))
        if not company_name:
            c.execute("INSERT INTO settings(k,v) VALUES('co_name',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (b.company_name.strip(),))
            c.execute("INSERT INTO settings(k,v) VALUES('co_cnpj',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (b.cnpj,))
        token = None
        if not account_exists:
            token = secrets.token_urlsafe(32)
            c.execute('INSERT INTO sessions(token,"user",expires,role) VALUES(?,?,?,\'admin\')',
                      (hashlib.sha256(token.encode()).hexdigest(), email, now + SESSION_HOURS * 3600))
    return {"email": email, "token": token, "role": role}

@app.post("/auth/login")
def login(b: Login):
    k, now = (b.email or b.user).strip().lower(), time.time()
    n, t = _fails.get(k, (0, 0))
    if n >= 5 and now - t < 300:
        raise HTTPException(429, "Muitas tentativas. Aguarde 5 minutos.")
    with db() as c:
        a = c.execute('SELECT * FROM admins WHERE "user"=?', (k,)).fetchone()
        if not a or not hmac.compare_digest(hash_pin(b.password, a["salt"]), a["hash"]):
            _fails[k] = ((n if now - t < 300 else 0) + 1, now)
            raise HTTPException(401, "Usuário ou senha incorretos.")
        if a["totp_enabled"]:
            if not b.totp_code:
                return {"requires_otp": True}
            if not verify_totp(a["totp_secret"], b.totp_code):
                _fails[k] = ((n if now - t < 300 else 0) + 1, now)
                raise HTTPException(401, "Código autenticador inválido.")
        _fails.pop(k, None)
        tok = secrets.token_urlsafe(32)
        c.execute("DELETE FROM sessions WHERE expires<?", (now,))
        c.execute('INSERT INTO sessions(token,"user",expires,role) VALUES(?,?,?,?)',
                  (hashlib.sha256(tok.encode()).hexdigest(), k, now + SESSION_HOURS * 3600, a["role"]))
    return {"token": tok, "user": k, "role": a["role"]}

@app.post("/auth/employee/login")
def employee_login(b: Login):
    email, now = (b.email or b.user).strip().lower(), time.time()
    n, t = _fails.get(email, (0, 0))
    if n >= 5 and now - t < 300:
        raise HTTPException(429, "Muitas tentativas. Aguarde 5 minutos.")
    with db() as c:
        e = c.execute("SELECT id,name,account_salt,account_hash FROM employees WHERE lower(account_email)=lower(?) AND active=1",
                       (email,)).fetchone()
        if not e or not e["account_hash"] or not hmac.compare_digest(hash_pin(b.password, e["account_salt"]), e["account_hash"]):
            _fails[email] = ((n if now - t < 300 else 0) + 1, now)
            raise HTTPException(401, "E-mail ou senha incorretos.")
        _fails.pop(email, None)
        token = secrets.token_urlsafe(32)
        c.execute("DELETE FROM sessions WHERE expires<?", (now,))
        c.execute('INSERT INTO sessions(token,"user",expires,role,subject_id) VALUES(?,?,?,\'employee\',?)',
                  (hashlib.sha256(token.encode()).hexdigest(), email, now + SESSION_HOURS * 3600, e["id"]))
    return {"token": token, "user": email, "role": "employee", "employee_id": e["id"], "name": e["name"]}

@app.post("/auth/logout")
def logout(authorization: str = Header(default="")):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(authorization[7:].encode()).hexdigest(),))
    return {"ok": True}

class TotpCode(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")

@app.get("/auth/2fa")
def get_totp_status(who=Depends(principal)):
    if who["role"] not in ("admin", "manager", "operator"):
        raise HTTPException(403, "2FA disponível para contas de gestão.")
    with db() as c:
        row = c.execute('SELECT totp_enabled FROM admins WHERE "user"=?', (who["user"],)).fetchone()
    return {"enabled": bool(row["totp_enabled"])}

@app.post("/auth/2fa/setup")
def setup_totp(who=Depends(principal)):
    if who["role"] not in ("admin", "manager", "operator"):
        raise HTTPException(403, "2FA disponível para contas de gestão.")
    secret = generate_totp_secret()
    with db() as c:
        c.execute('UPDATE admins SET totp_secret=?,totp_enabled=0 WHERE "user"=?', (secret, who["user"]))
    return {"secret": secret, "otpauth_url": provisioning_uri(secret, who["user"])}

@app.post("/auth/2fa/enable")
def enable_totp(b: TotpCode, who=Depends(principal)):
    with db() as c:
        row = c.execute('SELECT totp_secret FROM admins WHERE "user"=?', (who["user"],)).fetchone()
        if not row or not verify_totp(row["totp_secret"], b.code):
            raise HTTPException(400, "Código autenticador inválido.")
        c.execute('UPDATE admins SET totp_enabled=1 WHERE "user"=?', (who["user"],))
    return {"enabled": True}

@app.post("/auth/2fa/disable")
def disable_totp(b: TotpCode, who=Depends(principal)):
    with db() as c:
        row = c.execute('SELECT totp_secret FROM admins WHERE "user"=?', (who["user"],)).fetchone()
        if not row or not verify_totp(row["totp_secret"], b.code):
            raise HTTPException(400, "Código autenticador inválido.")
        c.execute('UPDATE admins SET totp_secret=NULL,totp_enabled=0 WHERE "user"=?', (who["user"],))
    return {"enabled": False}

class RoleChange(BaseModel):
    role: Literal["manager", "operator"]
    department_id: Optional[int] = None

@app.get("/team/accounts", dependencies=[Depends(admin)])
def list_team_accounts():
    with db() as c:
        return rows(c.execute('SELECT a."user",a.role,a.department_id,d.name AS department '
                              'FROM admins a LEFT JOIN departments d ON d.id=a.department_id ORDER BY a."user"'))

@app.patch("/team/accounts/{email}", dependencies=[Depends(admin)])
def change_team_account(email: str, change: RoleChange):
    department_id = change.department_id if change.role == "manager" else None
    with db() as c:
        if not c.execute('SELECT 1 FROM admins WHERE "user"=?', (email.lower(),)).fetchone():
            raise HTTPException(404, "Conta não encontrada.")
        if email.lower() == c.execute('SELECT "user" FROM admins WHERE role=\'admin\' ORDER BY "user" LIMIT 1').fetchone()["user"]:
            raise HTTPException(400, "Não é possível alterar a conta principal.")
        if department_id is not None and not c.execute("SELECT 1 FROM departments WHERE id=?", (department_id,)).fetchone():
            raise HTTPException(404, "Departamento não encontrado.")
        c.execute('UPDATE admins SET role=?,department_id=? WHERE "user"=?',
                  (change.role, department_id, email.lower()))
        c.execute('DELETE FROM sessions WHERE "user"=?', (email.lower(),))
    return {"ok": True}

@app.get("/audit", dependencies=[Depends(admin)])
def list_audit(limit: int = 100):
    if not 1 <= limit <= 500:
        raise HTTPException(400, "O limite deve estar entre 1 e 500.")
    with db() as c:
        return rows(c.execute("SELECT at,actor,role,action,resource,status,ip FROM audit_log "
                              "ORDER BY id DESC LIMIT ?", (limit,)))

@app.get("/search", dependencies=[Depends(manager_or_admin)])
def global_search(q: str = "", who=Depends(manager_or_admin)):
    query = q.strip()[:80]
    if len(query) < 2:
        return []
    pattern = "%" + query.replace("%", "\\%").replace("_", "\\_") + "%"
    with db() as c:
        task_scope = "" if who["role"] != "manager" else " AND (department_id IS NULL OR department_id=?)"
        task_args = (pattern, pattern) if who["role"] != "manager" else (
            pattern, pattern, who["department_id"])
        tasks = rows(c.execute("SELECT id,title,assignee,status FROM tasks WHERE "
                               "(title LIKE ? ESCAPE '\\' OR assignee LIKE ? ESCAPE '\\')" + task_scope +
                               " ORDER BY id DESC LIMIT 8", task_args))
        products = rows(c.execute("SELECT id,name FROM products WHERE name LIKE ? ESCAPE '\\' ORDER BY name LIMIT 8",
                                  (pattern,))) if who["role"] == "admin" else []
        employees = []
        if who["role"] == "admin":
            employees = rows(c.execute("SELECT id,name,role FROM employees WHERE active=1 AND "
                                       "(name LIKE ? ESCAPE '\\' OR role LIKE ? ESCAPE '\\') ORDER BY name LIMIT 8",
                                       (pattern, pattern)))
    return ([{"kind": "task", **item} for item in tasks]
            + [{"kind": "product", **item} for item in products]
            + [{"kind": "employee", **item} for item in employees])[:20]

@app.websocket("/events")
async def event_stream(socket: WebSocket):
    await socket.accept()
    try:
        credentials = await asyncio.wait_for(socket.receive_json(), timeout=10)
        if not isinstance(credentials, dict):
            await socket.close(code=1008)
            return
        token = credentials.get("token", "")
        if not isinstance(token, str):
            await socket.close(code=1008)
            return
        principal_for_token(token)
        live_clients.clients.add(socket)
        await socket.send_json({"type": "connected"})
        while True:
            await socket.receive_text()
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except HTTPException:
        await socket.close(code=1008)
    finally:
        live_clients.clients.discard(socket)

# ---------- Configurações: local de trabalho e empresa ----------
class Workplace(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: float = Field(default=100, gt=0, le=5000)

class Company(BaseModel):
    name: str = ""
    cnpj: str = Field(default="", pattern=r"^(\d{14})?$")

def put_settings(d: dict):
    with db() as c:
        for k, v in d.items():
            c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (k, str(v)))

@app.put("/settings/workplace", dependencies=[Depends(admin)])
def set_workplace(w: Workplace):
    put_settings(w.model_dump()); return w

@app.get("/settings/workplace")
def get_workplace():
    with db() as c: w = workplace_of(settings(c))
    if not w: raise HTTPException(404, "Local de trabalho ainda não definido.")
    return w

@app.put("/settings/company", dependencies=[Depends(admin)])
def set_company(co: Company):
    put_settings({"co_name": co.name, "co_cnpj": co.cnpj}); return co

@app.get("/settings/company", dependencies=[Depends(admin)])
def get_company():
    with db() as c: s = settings(c)
    return {"name": s.get("co_name", ""), "cnpj": s.get("co_cnpj", "")}

# ---------- Funcionários ----------
CPF = r"^(\d{11})?$"

class EmployeeIn(BaseModel):
    name: str; role: str = ""; salary: float = Field(ge=0); benefits: float = Field(default=0, ge=0)
    cpf: str = Field(default="", pattern=CPF)
    pin: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")
    department_id: Optional[int] = None

class EmployeePatch(BaseModel):
    name: Optional[str] = None; role: Optional[str] = None; cpf: Optional[str] = Field(default=None, pattern=CPF)
    salary: Optional[float] = None; benefits: Optional[float] = None; active: Optional[bool] = None
    department_id: Optional[int] = None

@app.post("/employees", dependencies=[Depends(admin)], status_code=201)
def add_employee(e: EmployeeIn):
    salt = secrets.token_hex(16)
    with db() as c:
        cur = c.execute("INSERT INTO employees(name,cpf,role,salary,benefits,pin_salt,pin_hash,department_id) VALUES(?,?,?,?,?,?,?,?) RETURNING id",
                        (e.name, e.cpf, e.role, e.salary, e.benefits, salt, hash_pin(e.pin, salt), e.department_id))
        employee_id = cur.fetchall()[0]["id"]
    return {"id": employee_id}

@app.get("/employees", dependencies=[Depends(admin)])
def list_employees():
    with db() as c:
        return rows(c.execute("""SELECT e.id,e.name,e.cpf,e.role,e.salary,e.benefits,e.active,e.department_id,e.account_email,
            d.name AS department FROM employees e LEFT JOIN departments d ON d.id=e.department_id"""))

@app.get("/employees/export.csv", dependencies=[Depends(admin)])
def export_employees():
    with db() as c:
        employees = c.execute("""SELECT e.id,e.name,e.cpf,e.role,e.salary,e.benefits,
            COALESCE(d.name,'') AS department,CASE WHEN e.active=1 THEN 'Ativo' ELSE 'Inativo' END AS status
            FROM employees e LEFT JOIN departments d ON d.id=e.department_id
            ORDER BY lower(e.name),e.id""").fetchall()
    out = io.StringIO()
    out.write("\ufeff")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["ID", "Nome", "CPF", "Cargo", "Salário", "Benefícios", "Departamento", "Status"])
    for employee in employees:
        writer.writerow([csv_safe(value) for value in employee])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="funcionarios.csv"'})

@app.patch("/employees/{eid}", dependencies=[Depends(admin)])
def edit_employee(eid: int, p: EmployeePatch):
    data = {k: (int(v) if k in ("active", "department_id") else v) for k, v in p.model_dump().items() if v is not None}
    if not data: raise HTTPException(400, "Nada para atualizar.")
    with db() as c:
        cur = c.execute(f"UPDATE employees SET {','.join(k+'=?' for k in data)} WHERE id=?", (*data.values(), eid))
        if not cur.rowcount: raise HTTPException(404, "Funcionário não encontrado.")
    return {"ok": True}

@app.delete("/employees/{eid}", dependencies=[Depends(admin)])
def delete_employee(eid: int):
    with db() as c:
        employee = c.execute("SELECT id FROM employees WHERE id=?", (eid,)).fetchone()
        if not employee:
            raise HTTPException(404, "Funcionário não encontrado.")
        c.execute("""UPDATE employees SET active=0,pin_salt=NULL,pin_hash=NULL,failed_pins=0,
                     account_email='',account_salt=NULL,account_hash=NULL WHERE id=?""", (eid,))
        c.execute("DELETE FROM sessions WHERE role='employee' AND subject_id=?", (eid,))
    return {"ok": True, "archived": True}

class EmployeeAccount(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)

@app.post("/employees/{eid}/account", dependencies=[Depends(admin)], status_code=201)
def create_employee_account(eid: int, b: EmployeeAccount):
    email = b.email.strip().lower()
    salt = secrets.token_hex(16)
    with db() as c:
        employee = c.execute("SELECT id FROM employees WHERE id=? AND active=1", (eid,)).fetchone()
        if not employee:
            raise HTTPException(404, "Funcionário não encontrado ou inativo.")
        if c.execute('SELECT 1 FROM admins WHERE lower("user")=lower(?)', (email,)).fetchone():
            raise HTTPException(409, "Este e-mail já pertence a uma conta administrativa.")
        try:
            c.execute("UPDATE employees SET account_email=?,account_salt=?,account_hash=? WHERE id=?",
                      (email, salt, hash_pin(b.password, salt), eid))
        except (sqlite3.IntegrityError, psycopg2.IntegrityError):
            raise HTTPException(409, "Este e-mail já possui uma conta de funcionário.")
    return {"email": email}

class MessageIn(BaseModel):
    scope: Literal["team", "private"]
    body: str = Field(min_length=1, max_length=2000)
    employee_id: Optional[int] = None

@app.get("/messages")
def list_messages(employee_id: Optional[int] = None, who: dict = Depends(principal)):
    with db() as c:
        if who["role"] == "employee":
            employee_id = who["employee_id"]
        elif employee_id is not None and not c.execute("SELECT 1 FROM employees WHERE id=?", (employee_id,)).fetchone():
            raise HTTPException(404, "Funcionário não encontrado.")
        if employee_id is None:
            query = "SELECT * FROM messages WHERE scope='team' ORDER BY id"
            params = ()
        else:
            query = "SELECT * FROM messages WHERE scope='team' OR (scope='private' AND employee_id=?) ORDER BY id"
            params = (employee_id,)
        return rows(c.execute(query, params))

@app.post("/messages", status_code=201)
def send_message(b: MessageIn, who: dict = Depends(principal)):
    body = b.body.strip()
    if not body:
        raise HTTPException(400, "A mensagem não pode ficar vazia.")
    employee_id = b.employee_id
    if who["role"] == "employee":
        employee_id = who["employee_id"] if b.scope == "private" else None
    elif b.scope == "private":
        if employee_id is None:
            raise HTTPException(400, "Escolha o funcionário desta conversa privada.")
        with db() as c:
            if not c.execute("SELECT 1 FROM employees WHERE id=?", (employee_id,)).fetchone():
                raise HTTPException(404, "Funcionário não encontrado.")
    else:
        employee_id = None
    sender_employee_id = who["employee_id"] if who["role"] == "employee" else None
    sender_name = who.get("name") or who["user"]
    with db() as c:
        cur = c.execute("INSERT INTO messages(scope,employee_id,sender_role,sender_employee_id,sender_name,body,created_at) "
                        "VALUES(?,?,?,?,?,?,?) RETURNING id",
                        (b.scope, employee_id, who["role"], sender_employee_id, sender_name, body, iso(utcnow())))
        message_id = cur.fetchall()[0]["id"]
        message = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    return dict(message)

# ---------- Ponto: espelho de horas ----------
def timesheet_data(c, ym):
    a, b = month_bounds(ym)
    ents = rows(c.execute("SELECT employee_id,kind,at FROM time_entries WHERE accepted=1 AND at>=? AND at<? "
                          "ORDER BY employee_id, at", (a, b)))
    out = {}
    for e in ents:
        t = datetime.fromisoformat(e["at"])
        rec = out.setdefault(e["employee_id"], {"days": defaultdict(float), "open": None})
        if e["kind"] == "entrada":
            rec["open"] = t
        elif rec["open"]:
            rec["days"][local(rec["open"]).date().isoformat()] += (t - rec["open"]).total_seconds() / 3600
            rec["open"] = None
    for rec in out.values():
        rec["total"] = sum(rec["days"].values())
        rec["overtime"] = sum(max(0, h - DAILY_HOURS) for h in rec["days"].values())
    return out

@app.get("/timesheet", dependencies=[Depends(admin)])
def timesheet(month: Optional[str] = None):
    ym = month or local(utcnow()).strftime("%Y-%m")
    with db() as c:
        emps = rows(c.execute("SELECT id,name FROM employees WHERE active=1 ORDER BY name"))
        ts = timesheet_data(c, ym)
    out = []
    for e in emps:
        t = ts.get(e["id"], {"days": {}, "total": 0, "overtime": 0, "open": None})
        out.append({"id": e["id"], "name": e["name"],
                    "days": [{"date": d, "hours": round(h, 2)} for d, h in sorted(t["days"].items())],
                    "total_hours": round(t["total"], 2), "overtime_hours": round(t["overtime"], 2),
                    "open": t["open"] is not None})
    return {"month": ym, "employees": out}

# ---------- Folha (integrada ao ponto) ----------
def inss(s): return min(s, 7800) * 0.11          # estimativa; ajuste à tabela vigente
EMPLOYER_CHARGES = 0.28                           # estimativa de encargos patronais

@app.get("/payroll", dependencies=[Depends(admin)])
def payroll(month: Optional[str] = None):
    ym = month or local(utcnow()).strftime("%Y-%m")
    with db() as c:
        emps = rows(c.execute("SELECT * FROM employees WHERE active=1"))
        ts = timesheet_data(c, ym)
    out = []
    for e in emps:
        t = ts.get(e["id"])
        oh = round(t["overtime"], 2) if t else 0.0
        ot = round(oh * (e["salary"] / 220) * OVERTIME_RATE, 2)
        gross = e["salary"] + ot
        d = inss(gross)
        out.append({"id": e["id"], "name": e["name"], "role": e["role"], "department_id": e["department_id"], "base_salary": e["salary"],
                    "overtime_hours": oh, "overtime_pay": ot, "gross": round(gross, 2), "inss": round(d, 2),
                    "benefits": e["benefits"], "net": round(gross - d + e["benefits"], 2),
                    "company_cost": round(gross * (1 + EMPLOYER_CHARGES) + e["benefits"], 2)})
    return {"month": ym, "employees": out, "total_net": round(sum(o["net"] for o in out), 2),
            "total_company_cost": round(sum(o["company_cost"] for o in out), 2)}

# ---------- Produtos / custeio ----------
class ProductIn(BaseModel):
    name: str; material: float = Field(default=0, ge=0); labor: float = Field(default=0, ge=0)
    overhead: float = Field(default=0, ge=0); price: float = Field(default=0, ge=0)

def with_costs(p):
    cost = p["material"] + p["labor"] + p["overhead"]
    profit = p["price"] - cost
    return {**p, "cost": round(cost, 2), "profit": round(profit, 2),
            "margin_pct": round(profit / p["price"] * 100, 1) if p["price"] else 0}

@app.post("/products", dependencies=[Depends(admin)], status_code=201)
def add_product(p: ProductIn):
    with db() as c:
        cur = c.execute("INSERT INTO products(name,material,labor,overhead,price) VALUES(?,?,?,?,?) RETURNING id",
                        (p.name, p.material, p.labor, p.overhead, p.price))
        product_id = cur.fetchall()[0]["id"]
    return {"id": product_id}

@app.get("/products", dependencies=[Depends(admin)])
def list_products():
    with db() as c:
        return [with_costs(p) for p in rows(c.execute("SELECT * FROM products"))]

@app.put("/products/{pid}", dependencies=[Depends(admin)])
def edit_product(pid: int, p: ProductIn):
    with db() as c:
        cur = c.execute("UPDATE products SET name=?,material=?,labor=?,overhead=?,price=? WHERE id=?",
                        (p.name, p.material, p.labor, p.overhead, p.price, pid))
        if not cur.rowcount: raise HTTPException(404, "Produto não encontrado.")
    return {"ok": True}

# ---------- Quadros (tarefas) ----------
PRI = ("baixa", "media", "alta")
SEV = ("normal", "atencao", "critico")
APPR = ("pendente", "aprovado", "recusado")

class TaskIn(BaseModel):
    title: str; assignee: str = ""; due: str = ""; status: int = Field(default=0, ge=0, le=2)
    priority: str = Field(default="media"); progress: int = Field(default=0, ge=0, le=100)
    approval: str = Field(default="pendente"); severity: str = Field(default="normal")
    department_id: Optional[int] = None
    labor_budget_hours: float = Field(default=0, ge=0)
    cost_ceiling: float = Field(default=0, ge=0)

class TaskPatch(BaseModel):
    title: Optional[str] = None; assignee: Optional[str] = None; due: Optional[str] = None
    status: Optional[int] = Field(default=None, ge=0, le=2)
    priority: Optional[str] = None; progress: Optional[int] = Field(default=None, ge=0, le=100)
    approval: Optional[str] = None; severity: Optional[str] = None
    department_id: Optional[int] = None
    labor_budget_hours: Optional[float] = Field(default=None, ge=0)
    cost_ceiling: Optional[float] = Field(default=None, ge=0)

def norm_task(t: TaskIn):
    if t.priority not in PRI: raise HTTPException(400, "Prioridade inválida.")
    if t.severity not in SEV: raise HTTPException(400, "Status inválido.")
    if t.approval not in APPR: raise HTTPException(400, "Aprovação inválida.")
    return t

TASK_STATUS = ("A fazer", "Em andamento", "Concluído")
def task_history(c, task_id, actor, action, details):
    c.execute("INSERT INTO task_history(task_id,actor,action,details,created_at) VALUES(?,?,?,?,?)",
              (task_id, actor, action, details, iso(utcnow())))

def sync_checklist_progress(c, task_id, actor, background):
    counts = c.execute("SELECT COUNT(*) total,COALESCE(SUM(done),0) finished "
                       "FROM task_checklist WHERE task_id=?", (task_id,)).fetchone()
    if counts["total"] == 0:
        return
    progress = round(counts["finished"] * 100 / counts["total"])
    old = c.execute("SELECT progress FROM tasks WHERE id=?", (task_id,)).fetchone()["progress"]
    if old != progress:
        c.execute("UPDATE tasks SET progress=? WHERE id=?", (progress, task_id))
        task_history(c, task_id, actor, "Progresso", f"Checklist atualizou o progresso de {old}% para {progress}%.")
        if progress == 100:
            run_task_automations(c, task_id, background)

def task_cost(c, task_id):
    entries = rows(c.execute(
        "SELECT employee_id,task_id,kind,at,COALESCE(labor_rate,salary/220.0) AS hourly_rate FROM time_entries "
        "JOIN employees ON employees.id=time_entries.employee_id "
        "WHERE accepted=1 ORDER BY time_entries.employee_id,time_entries.at,time_entries.id"))
    openings, labor_hours, labor_cost = {}, 0.0, 0.0
    for entry in entries:
        key = entry["employee_id"]
        if entry["kind"] == "entrada":
            openings[key] = entry
        elif entry["kind"] == "saida" and key in openings:
            start = openings.pop(key)
            if start["task_id"] != task_id and entry["task_id"] != task_id:
                continue
            hours = max(0, (datetime.fromisoformat(entry["at"]) -
                            datetime.fromisoformat(start["at"])).total_seconds() / 3600)
            labor_hours += hours
            labor_cost += hours * (start["hourly_rate"] or 0)
    materials = c.execute(
        "SELECT COALESCE(SUM(consumed_quantity*unit_cost),0) FROM task_materials WHERE task_id=?",
        (task_id,)).fetchone()[0]
    return {"labor_hours": round(labor_hours, 2), "labor_cost": round(labor_cost, 2),
            "material_cost": round(materials, 2), "actual_cost": round(labor_cost + materials, 2)}

def consume_task_materials(c, task_id, background=None):
    allocations = rows(c.execute(
        "SELECT tm.id,tm.quantity,tm.consumed_quantity,tm.material_id,m.name,m.unit,m.stock,m.unit_cost,m.minimum_stock "
        "FROM task_materials tm JOIN materials m ON m.id=tm.material_id WHERE tm.task_id=?",
        (task_id,)))
    pending = [item for item in allocations if item["consumed_quantity"] < item["quantity"]]
    for item in pending:
        amount = item["quantity"] - item["consumed_quantity"]
        if item["stock"] < amount:
            raise HTTPException(409, f"Estoque insuficiente de {item['name']}: disponível "
                                     f"{item['stock']:g} {item['unit']}, necessário {amount:g}.")
    for item in pending:
        amount = item["quantity"] - item["consumed_quantity"]
        cur = c.execute("UPDATE materials SET stock=stock-? WHERE id=? AND stock>=?",
                        (amount, item["material_id"], amount))
        if not cur.rowcount:
            raise HTTPException(409, f"Estoque insuficiente de {item['name']}. Atualize o quadro e tente novamente.")
        c.execute("UPDATE task_materials SET consumed_quantity=quantity,unit_cost=? WHERE id=?",
                  (item["unit_cost"], item["id"]))
        task_history(c, task_id, "Sistema", "Baixa de insumo",
                     f"{amount:g} {item['unit']} de {item['name']} consumidos ao iniciar o card.")
        if background and (item["stock"] - amount <= 0 or
                           (item["minimum_stock"] > 0 and item["stock"] - amount <= item["minimum_stock"])):
            background.add_task(dispatch_notifications,
                                f"Alerta SIGI: baixa de matéria-prima para tarefa; estoque de {item['name']} "
                                f"ficou em {item['stock'] - amount:g} {item['unit']}.")

def run_task_automations(c, task_id, background):
    task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return
    rules = rows(c.execute("SELECT * FROM automation_rules WHERE active=1"))
    for rule in rules:
        should_run = (rule["trigger"] == "progress_complete_pending" and task["progress"] == 100)
        if rule["trigger"] == "cost_over_ceiling_notify" and task["cost_ceiling"] > 0:
            should_run = task_cost(c, task_id)["actual_cost"] > task["cost_ceiling"]
        already = c.execute("SELECT 1 FROM automation_runs WHERE rule_id=? AND task_id=?",
                            (rule["id"], task_id)).fetchone()
        if not should_run or already:
            continue
        c.execute("INSERT INTO automation_runs(rule_id,task_id,created_at) VALUES(?,?,?)",
                  (rule["id"], task_id, iso(utcnow())))
        if rule["trigger"] == "progress_complete_pending":
            c.execute("UPDATE tasks SET approval='pendente' WHERE id=?", (task_id,))
            task_history(c, task_id, "Automação", "Aprovação", "Progresso em 100%: aprovação movida para pendente.")
            background.add_task(dispatch_notifications,
                                f"Automação: a tarefa {task['title']} chegou a 100% e aguarda aprovação.")
        else:
            cost = task_cost(c, task_id)["actual_cost"]
            task_history(c, task_id, "Automação", "Custo", f"Custo real de R$ {cost:.2f} ultrapassou o teto.")
            background.add_task(dispatch_notifications,
                                f"Automação: custo da tarefa {task['title']} ultrapassou o teto "
                                f"(R$ {cost:.2f} de R$ {task['cost_ceiling']:.2f}).")

@app.post("/tasks", dependencies=[Depends(admin)], status_code=201)
def add_task(t: TaskIn, background: BackgroundTasks, who=Depends(admin_context)):
    t = norm_task(t)
    with db() as c:
        if t.department_id is not None and not c.execute("SELECT 1 FROM departments WHERE id=?",
                                                         (t.department_id,)).fetchone():
            raise HTTPException(404, "Departamento não encontrado.")
        cur = c.execute("INSERT INTO tasks(title,assignee,due,status,priority,progress,approval,severity,"
                        "department_id,labor_budget_hours,cost_ceiling) VALUES(?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
                        (t.title, t.assignee, t.due, t.status, t.priority, t.progress, t.approval,
                         t.severity, t.department_id, t.labor_budget_hours, t.cost_ceiling))
        task_id = cur.fetchall()[0]["id"]
        task_history(c, task_id, who["user"], "Criação", f"Tarefa criada: {t.title}.")
        if t.status in (1, 2):
            consume_task_materials(c, task_id, background)
        run_task_automations(c, task_id, background)
    if t.status != 2 and (t.severity == "critico" or (t.due and t.due < local(utcnow()).date().isoformat())):
        background.add_task(dispatch_notifications, f"Alerta SIGI: tarefa em risco — {t.title}.")
    return {"id": task_id}

def task_for_user(c, task_id, who):
    task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise HTTPException(404, "Tarefa não encontrada.")
    if who["role"] == "manager" and task["department_id"] is not None and \
            task["department_id"] != who["department_id"]:
        raise HTTPException(404, "Tarefa não encontrada.")
    return task

@app.get("/tasks")
def list_tasks(who=Depends(manager_or_admin)):
    with db() as c:
        if who["role"] == "manager":
            tasks = rows(c.execute("SELECT * FROM tasks WHERE department_id IS NULL OR department_id=? "
                                   "ORDER BY status,id", (who["department_id"],)))
        else:
            tasks = rows(c.execute("SELECT * FROM tasks ORDER BY status, id"))
        for task in tasks:
            task["tags"] = [r["tag"] for r in c.execute(
                "SELECT tag FROM task_tags WHERE task_id=? ORDER BY tag", (task["id"],))]
            task["checklist_total"] = c.execute(
                "SELECT COUNT(*) FROM task_checklist WHERE task_id=?", (task["id"],)).fetchone()[0]
            task["checklist_done"] = c.execute(
                "SELECT COUNT(*) FROM task_checklist WHERE task_id=? AND done=1", (task["id"],)).fetchone()[0]
        return tasks

@app.get("/tasks/export.csv")
def export_tasks(q: str = "", status: Optional[int] = None, priority: Optional[str] = None,
                 severity: Optional[str] = None, approval: Optional[str] = None,
                 department_id: Optional[int] = None, who=Depends(manager_or_admin)):
    if status is not None and status not in (0, 1, 2):
        raise HTTPException(400, "Etapa inválida.")
    if priority is not None and priority not in PRI:
        raise HTTPException(400, "Prioridade inválida.")
    if severity is not None and severity not in SEV:
        raise HTTPException(400, "Severidade inválida.")
    if approval is not None and approval not in APPR:
        raise HTTPException(400, "Aprovação inválida.")
    conditions, params = [], []
    if q.strip():
        pattern = "%" + q.strip()[:80].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append("(title LIKE ? ESCAPE '\\' OR assignee LIKE ? ESCAPE '\\')")
        params.extend((pattern, pattern))
    for column, value in (("status", status), ("priority", priority), ("severity", severity),
                          ("approval", approval), ("department_id", department_id)):
        if value is not None:
            conditions.append(f"{column}=?")
            params.append(value)
    with db() as c:
        if who["role"] == "manager":
            conditions.append("(department_id IS NULL OR department_id=?)")
            params.append(who["department_id"])
        sql = ("SELECT title,status,priority,severity,due,progress,approval,assignee,"
               "labor_budget_hours,cost_ceiling FROM tasks")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        tasks = rows(c.execute(sql + " ORDER BY status,id", params))
    out = io.StringIO()
    out.write("\ufeff")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Tarefa", "Etapa", "Prioridade", "Severidade", "Prazo", "Progresso",
                     "Aprovação", "Responsável", "Horas orçadas", "Teto de custo"])
    for task in tasks:
        task["status"] = TASK_STATUS[task["status"]]
        writer.writerow([csv_safe(value) for value in task.values()])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="quadros.csv"'})

@app.get("/tasks/{tid}")
def task_detail(tid: int, who=Depends(manager_or_admin)):
    with db() as c:
        task = task_for_user(c, tid, who)
        result = dict(task)
        result["checklist"] = rows(c.execute("SELECT id,title,done FROM task_checklist WHERE task_id=? ORDER BY id",
                                              (tid,)))
        result["tags"] = [item["tag"] for item in c.execute(
            "SELECT tag FROM task_tags WHERE task_id=? ORDER BY tag", (tid,))]
        result["attachments"] = rows(c.execute(
            "SELECT id,name,mime,size,uploaded_at FROM task_attachments WHERE task_id=? ORDER BY id DESC",
            (tid,)))
        result["history"] = rows(c.execute(
            "SELECT actor,action,details,created_at FROM task_history WHERE task_id=? ORDER BY id DESC LIMIT 100",
            (tid,)))
        result["materials"] = rows(c.execute(
            "SELECT tm.id,tm.material_id,m.name,m.unit,tm.quantity,tm.consumed_quantity,tm.unit_cost "
            "FROM task_materials tm JOIN materials m ON m.id=tm.material_id WHERE tm.task_id=? ORDER BY tm.id",
            (tid,)))
        result["cost"] = task_cost(c, tid)
        return result

@app.get("/tasks/{tid}/cost")
def task_cost_report(tid: int, who=Depends(manager_or_admin)):
    with db() as c:
        task = task_for_user(c, tid, who)
        return {**task_cost(c, tid), "labor_budget_hours": task["labor_budget_hours"],
                "cost_ceiling": task["cost_ceiling"]}

class ChecklistIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)

class ChecklistPatch(BaseModel):
    done: bool

class TagsIn(BaseModel):
    tags: list[str] = Field(max_length=12)

class AttachmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    mime: Literal["application/pdf", "image/jpeg", "image/png", "image/webp"]
    content_base64: str = Field(min_length=1, max_length=8_400_000)

@app.post("/tasks/{tid}/checklist", dependencies=[Depends(admin)])
def add_checklist_item(tid: int, item: ChecklistIn, who=Depends(admin_context)):
    title = item.title.strip()
    if not title:
        raise HTTPException(400, "A descrição da etapa não pode ficar vazia.")
    with db() as c:
        if not c.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Tarefa não encontrada.")
        cur = c.execute("INSERT INTO task_checklist(task_id,title,done) VALUES(?,?,0) RETURNING id",
                        (tid, title))
        item_id = cur.fetchall()[0]["id"]
        task_history(c, tid, who["user"], "Checklist", f"Etapa adicionada: {title}.")
    return {"id": item_id}

@app.patch("/tasks/{tid}/checklist/{item_id}", dependencies=[Depends(admin)])
def update_checklist_item(tid: int, item_id: int, item: ChecklistPatch,
                          background: BackgroundTasks, who=Depends(admin_context)):
    with db() as c:
        cur = c.execute("UPDATE task_checklist SET done=? WHERE task_id=? AND id=?",
                        (int(item.done), tid, item_id))
        if not cur.rowcount:
            raise HTTPException(404, "Etapa não encontrada.")
        title = c.execute("SELECT title FROM task_checklist WHERE id=?", (item_id,)).fetchone()["title"]
        task_history(c, tid, who["user"], "Checklist",
                     f"Etapa {'concluída' if item.done else 'reaberta'}: {title}.")
        sync_checklist_progress(c, tid, who["user"], background)
    return {"ok": True}

@app.delete("/tasks/{tid}/checklist/{item_id}", dependencies=[Depends(admin)])
def delete_checklist_item(tid: int, item_id: int, background: BackgroundTasks, who=Depends(admin_context)):
    with db() as c:
        item = c.execute("SELECT title FROM task_checklist WHERE task_id=? AND id=?", (tid, item_id)).fetchone()
        if not item:
            raise HTTPException(404, "Etapa não encontrada.")
        c.execute("DELETE FROM task_checklist WHERE task_id=? AND id=?", (tid, item_id))
        task_history(c, tid, who["user"], "Checklist", f"Etapa removida: {item['title']}.")
        sync_checklist_progress(c, tid, who["user"], background)
    return {"ok": True}

@app.put("/tasks/{tid}/tags", dependencies=[Depends(admin)])
def update_task_tags(tid: int, payload: TagsIn, who=Depends(admin_context)):
    tags = list(dict.fromkeys(tag.strip()[:32] for tag in payload.tags if tag.strip()))
    with db() as c:
        if not c.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Tarefa não encontrada.")
        c.execute("DELETE FROM task_tags WHERE task_id=?", (tid,))
        for tag in tags:
            c.execute("INSERT INTO task_tags(task_id,tag) VALUES(?,?)", (tid, tag))
        task_history(c, tid, who["user"], "Tags", "Tags definidas: " + (", ".join(tags) or "nenhuma") + ".")
    return {"tags": tags}

@app.post("/tasks/{tid}/attachments", dependencies=[Depends(admin)], status_code=201)
def add_task_attachment(tid: int, attachment: AttachmentIn, who=Depends(admin_context)):
    try:
        content = base64.b64decode(attachment.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Arquivo codificado inválido.")
    if not content or len(content) > 6 * 1024 * 1024:
        raise HTTPException(413, "O anexo deve ter até 6 MB.")
    signatures = {"application/pdf": content.startswith(b"%PDF-"),
                  "image/jpeg": content.startswith(b"\xff\xd8\xff"),
                  "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
                  "image/webp": len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"}
    if not signatures[attachment.mime]:
        raise HTTPException(400, "O conteúdo do arquivo não corresponde ao tipo informado.")
    name = attachment.name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        raise HTTPException(400, "Nome de arquivo inválido.")
    with db() as c:
        if not c.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Tarefa não encontrada.")
        cur = c.execute("INSERT INTO task_attachments(task_id,name,mime,size,content,uploaded_at) "
                        "VALUES(?,?,?,?,?,?) RETURNING id",
                        (tid, name, attachment.mime, len(content), base64.b64encode(content).decode(), iso(utcnow())))
        attachment_id = cur.fetchall()[0]["id"]
        task_history(c, tid, who["user"], "Anexo", f"Arquivo anexado: {name}.")
    return {"id": attachment_id, "name": name, "size": len(content)}

@app.get("/tasks/{tid}/attachments/{attachment_id}")
def download_task_attachment(tid: int, attachment_id: int, who=Depends(manager_or_admin)):
    with db() as c:
        task_for_user(c, tid, who)
        file = c.execute("SELECT name,mime,content FROM task_attachments WHERE task_id=? AND id=?",
                         (tid, attachment_id)).fetchone()
        if not file:
            raise HTTPException(404, "Anexo não encontrado.")
    return Response(base64.b64decode(file["content"]), media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file['name'])}",
                             "X-Content-Type-Options": "nosniff"})

@app.delete("/tasks/{tid}/attachments/{attachment_id}", dependencies=[Depends(admin)])
def delete_task_attachment(tid: int, attachment_id: int, who=Depends(admin_context)):
    with db() as c:
        file = c.execute("SELECT name FROM task_attachments WHERE task_id=? AND id=?",
                         (tid, attachment_id)).fetchone()
        if not file:
            raise HTTPException(404, "Anexo não encontrado.")
        c.execute("DELETE FROM task_attachments WHERE task_id=? AND id=?", (tid, attachment_id))
        task_history(c, tid, who["user"], "Anexo", f"Arquivo removido: {file['name']}.")
    return {"ok": True}

class TaskMaterialIn(BaseModel):
    material_id: int
    quantity: float = Field(gt=0)

@app.post("/tasks/{tid}/materials", dependencies=[Depends(admin)], status_code=201)
def add_task_material(tid: int, item: TaskMaterialIn, who=Depends(admin_context)):
    with db() as c:
        task = c.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
        material = c.execute("SELECT name,unit,stock FROM materials WHERE id=?", (item.material_id,)).fetchone()
        if not task:
            raise HTTPException(404, "Tarefa não encontrada.")
        if not material:
            raise HTTPException(404, "Material não encontrado.")
        if material["stock"] < item.quantity:
            raise HTTPException(409, f"Estoque insuficiente de {material['name']}: "
                                     f"disponível {material['stock']:g} {material['unit']}.")
        if task["status"] != 0:
            raise HTTPException(409, "Vincule os materiais antes de iniciar a tarefa.")
        cur = c.execute("INSERT INTO task_materials(task_id,material_id,quantity) VALUES(?,?,?) RETURNING id",
                        (tid, item.material_id, item.quantity))
        link_id = cur.fetchall()[0]["id"]
        task_history(c, tid, who["user"], "Material",
                     f"Insumo vinculado: {item.quantity:g} {material['unit']} de {material['name']}.")
    return {"id": link_id}

@app.delete("/tasks/{tid}/materials/{link_id}", dependencies=[Depends(admin)])
def remove_task_material(tid: int, link_id: int, who=Depends(admin_context)):
    with db() as c:
        item = c.execute(
            "SELECT tm.quantity,tm.consumed_quantity,m.name,m.unit FROM task_materials tm "
            "JOIN materials m ON m.id=tm.material_id WHERE tm.task_id=? AND tm.id=?",
            (tid, link_id)).fetchone()
        if not item:
            raise HTTPException(404, "Insumo vinculado não encontrado.")
        if item["consumed_quantity"] > 0:
            raise HTTPException(409, "A baixa já foi registrada; o consumo não pode ser desfeito por esta tela.")
        c.execute("DELETE FROM task_materials WHERE task_id=? AND id=?", (tid, link_id))
        task_history(c, tid, who["user"], "Material", f"Vínculo de {item['name']} removido.")
    return {"ok": True}

class AutomationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger: Literal["progress_complete_pending", "cost_over_ceiling_notify"]

@app.get("/automations", dependencies=[Depends(admin)])
def list_automations():
    with db() as c:
        return rows(c.execute("SELECT * FROM automation_rules ORDER BY id"))

@app.post("/automations", dependencies=[Depends(admin)], status_code=201)
def add_automation(rule: AutomationIn):
    name = rule.name.strip()
    if not name:
        raise HTTPException(400, "Informe um nome para a automação.")
    with db() as c:
        cur = c.execute("INSERT INTO automation_rules(name,trigger,active) VALUES(?,?,1) RETURNING id",
                        (name, rule.trigger))
        return {"id": cur.fetchall()[0]["id"]}

@app.patch("/automations/{rule_id}", dependencies=[Depends(admin)])
def toggle_automation(rule_id: int, active: bool):
    with db() as c:
        cur = c.execute("UPDATE automation_rules SET active=? WHERE id=?", (int(active), rule_id))
        if not cur.rowcount:
            raise HTTPException(404, "Automação não encontrada.")
    return {"ok": True}

@app.delete("/automations/{rule_id}", dependencies=[Depends(admin)])
def delete_automation(rule_id: int):
    with db() as c:
        cur = c.execute("DELETE FROM automation_rules WHERE id=?", (rule_id,))
        if not cur.rowcount:
            raise HTTPException(404, "Automação não encontrada.")
    return {"ok": True}

class ApprovalIn(BaseModel):
    decision: Literal["aprovado", "recusado"]

@app.post("/tasks/{tid}/approval")
def decide_task_approval(tid: int, decision: ApprovalIn,
                         background: BackgroundTasks, who=Depends(manager_or_admin)):
    if who["role"] not in ("admin", "manager"):
        raise HTTPException(403, "Somente administradores e gestores podem aprovar tarefas.")
    with db() as c:
        task = task_for_user(c, tid, who)
        if who["role"] == "manager" and task["department_id"] is not None and \
                task["department_id"] != who["department_id"]:
            raise HTTPException(404, "Tarefa não encontrada.")
        cur = c.execute("UPDATE tasks SET approval=? WHERE id=? AND approval='pendente'",
                        (decision.decision, tid))
        if not cur.rowcount:
            raise HTTPException(409, "Esta tarefa não está aguardando aprovação.")
        title = task["title"]
        task_history(c, tid, who["user"], "Aprovação", f"Tarefa {decision.decision} por {who['user']}.")
    message = f"Tarefa {decision.decision}: {title} (por {who['user']})."
    background.add_task(dispatch_notifications, message)
    return {"ok": True, "approval": decision.decision}

@app.patch("/tasks/{tid}/status/{status}", dependencies=[Depends(admin)])
def move_task(tid: int, status: int, background: BackgroundTasks, who=Depends(admin_context)):
    if status not in (0, 1, 2): raise HTTPException(400, "Status deve ser 0, 1 ou 2.")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        task = c.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
        if not task: raise HTTPException(404, "Tarefa não encontrada.")
        if task["status"] == 0 and status in (1, 2):
            consume_task_materials(c, tid, background)
        cur = c.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
        task_history(c, tid, who["user"], "Etapa",
                     f"Etapa alterada de {TASK_STATUS[task['status']]} para {TASK_STATUS[status]}.")
        run_task_automations(c, tid, background)
    return {"ok": True}

@app.patch("/tasks/{tid}", dependencies=[Depends(admin)])
def edit_task(tid: int, p: TaskPatch, background: BackgroundTasks, who=Depends(admin_context)):
    data = {k: v for k, v in p.model_dump().items() if v is not None}
    if "priority" in data and data["priority"] not in PRI: raise HTTPException(400, "Prioridade inválida.")
    if "severity" in data and data["severity"] not in SEV: raise HTTPException(400, "Status inválido.")
    if "approval" in data and data["approval"] not in APPR: raise HTTPException(400, "Aprovação inválida.")
    if "title" in data and not data["title"].strip(): raise HTTPException(400, "O título não pode ficar vazio.")
    if not data: raise HTTPException(400, "Nada para atualizar.")
    with db() as c:
        old = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not old: raise HTTPException(404, "Tarefa não encontrada.")
        if data.get("department_id") is not None and not c.execute(
                "SELECT 1 FROM departments WHERE id=?", (data["department_id"],)).fetchone():
            raise HTTPException(404, "Departamento não encontrado.")
        cur = c.execute(f"UPDATE tasks SET {','.join(k+'=?' for k in data)} WHERE id=?", (*data.values(), tid))
        for key, value in data.items():
            if value != old[key]:
                labels = {"due": "prazo", "progress": "progresso", "assignee": "responsável",
                          "priority": "prioridade", "severity": "severidade", "approval": "aprovação",
                          "title": "título", "department_id": "departamento",
                          "labor_budget_hours": "horas orçadas", "cost_ceiling": "teto de custo"}
                task_history(c, tid, who["user"], labels.get(key, key),
                             f"{labels.get(key, key).capitalize()} alterado de {old[key] or 'vazio'} para {value}.")
        if "progress" in data and data["progress"] == 100 and old["progress"] != 100:
            run_task_automations(c, tid, background)
    return {"ok": True}

@app.delete("/tasks/{tid}", dependencies=[Depends(admin)])
def delete_task(tid: int):
    with db() as c: c.execute("DELETE FROM tasks WHERE id=?", (tid,))
    return {"ok": True}

# ---------- Financeiro: contas a pagar/receber e fluxo de caixa ----------
class Tx(BaseModel):
    kind: str = Field(pattern="^(receber|pagar)$")
    description: str = Field(min_length=1)
    amount: float = Field(gt=0)
    due: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    department_id: Optional[int] = None

@app.post("/finance", dependencies=[Depends(admin)], status_code=201)
def add_tx(t: Tx):
    try: date.fromisoformat(t.due)
    except ValueError: raise HTTPException(400, "Data de vencimento inválida.")
    if t.department_id is not None and t.kind != "pagar":
        raise HTTPException(400, "Somente despesas podem ser vinculadas a um setor.")
    with db() as c:
        if t.department_id is not None and not c.execute(
            "SELECT 1 FROM departments WHERE id=?", (t.department_id,)
        ).fetchone():
            raise HTTPException(404, "Setor não encontrado.")
        cur = c.execute("INSERT INTO transactions(kind,description,amount,due,department_id) VALUES(?,?,?,?,?) RETURNING id",
                        (t.kind, t.description, t.amount, t.due, t.department_id))
        transaction_id = cur.fetchall()[0]["id"]
    return {"id": transaction_id}

@app.get("/finance", dependencies=[Depends(admin)])
def list_tx(status: str = "open"):
    q = {"open": "WHERE paid=0", "paid": "WHERE paid=1"}.get(status, "")
    with db() as c:
        return rows(c.execute(f"SELECT t.*,d.name AS department FROM transactions t LEFT JOIN departments d ON d.id=t.department_id {q.replace('paid=', 't.paid=')} ORDER BY t.due,t.id"))

@app.get("/finance/export.csv", dependencies=[Depends(admin)])
def export_finance():
    with db() as c:
        transactions = c.execute("""SELECT t.kind,t.description,COALESCE(d.name,'') AS department,t.due,
            t.amount,t.paid,t.paid_at FROM transactions t
            LEFT JOIN departments d ON d.id=t.department_id ORDER BY t.due,t.id""").fetchall()
    out = io.StringIO()
    out.write("\ufeff")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Tipo", "Descrição", "Departamento", "Vencimento", "Valor", "Status", "Pago em"])
    for transaction in transactions:
        kind = "A receber" if transaction["kind"] == "receber" else "A pagar"
        status = "Pago" if transaction["paid"] else "Em aberto"
        writer.writerow([csv_safe(value) for value in (
            kind, transaction["description"], transaction["department"], transaction["due"],
            transaction["amount"], status, transaction["paid_at"] or "")])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="financeiro.csv"'})

@app.patch("/finance/{tid}/paid", dependencies=[Depends(admin)])
def pay_tx(tid: int):
    with db() as c:
        cur = c.execute("UPDATE transactions SET paid=1, paid_at=? WHERE id=?", (iso(utcnow()), tid))
        if not cur.rowcount: raise HTTPException(404, "Lançamento não encontrado.")
    return {"ok": True}

@app.delete("/finance/{tid}", dependencies=[Depends(admin)])
def delete_tx(tid: int):
    with db() as c: c.execute("DELETE FROM transactions WHERE id=?", (tid,))
    return {"ok": True}

@app.get("/finance/summary", dependencies=[Depends(admin)])
def finance_summary():
    today = local(utcnow()).date()
    with db() as c: tx = rows(c.execute("SELECT * FROM transactions WHERE paid=0"))
    folha = payroll()["total_company_cost"]
    def total(kind, late=False):
        return round(sum(t["amount"] for t in tx if t["kind"] == kind and (not late or t["due"] < today.isoformat())), 2)
    cur_ym, d, flow = today.strftime("%Y-%m"), today.replace(day=1), []
    for _ in range(6):
        ym = d.strftime("%Y-%m")
        pick = lambda k: round(sum(t["amount"] for t in tx if t["kind"] == k and max(t["due"][:7], cur_ym) == ym), 2)
        rec, pag = pick("receber"), pick("pagar")     # vencidos entram no mês atual
        flow.append({"month": ym, "receber": rec, "pagar": pag, "folha": folha, "saldo": round(rec - pag - folha, 2)})
        d = (d + timedelta(days=32)).replace(day=1)
    return {"receber_aberto": total("receber"), "pagar_aberto": total("pagar"),
            "receber_vencido": total("receber", True), "pagar_vencido": total("pagar", True),
            "folha_mensal": folha, "fluxo": flow}

def department_costs(month: Optional[str] = None):
    ym = month or local(utcnow()).strftime("%Y-%m")
    month_bounds(ym)
    first_day = date.fromisoformat(ym + "-01")
    next_month = (first_day + timedelta(days=32)).replace(day=1).isoformat()
    payroll_data = payroll(ym)
    with db() as c:
        departments = rows(c.execute("SELECT id,name FROM departments ORDER BY name"))
        expenses = rows(c.execute("""SELECT department_id,
            SUM(CASE WHEN paid=1 THEN amount ELSE 0 END) AS paid,
            SUM(CASE WHEN paid=0 THEN amount ELSE 0 END) AS open
            FROM transactions WHERE kind='pagar' AND due>=? AND due<? GROUP BY department_id""", (first_day.isoformat(), next_month)))
    sectors = {d["id"]: {"department_id": d["id"], "name": d["name"], "paid": 0.0,
                          "open": 0.0, "payroll": 0.0} for d in departments}
    for expense in expenses:
        did = expense["department_id"]
        if did not in sectors:
            sectors[did] = {"department_id": None, "name": "Sem setor", "paid": 0.0,
                            "open": 0.0, "payroll": 0.0}
        sectors[did]["paid"] = round(expense["paid"] or 0, 2)
        sectors[did]["open"] = round(expense["open"] or 0, 2)
    for employee in payroll_data["employees"]:
        did = employee["department_id"]
        if did not in sectors:
            sectors[did] = {"department_id": None, "name": "Sem setor", "paid": 0.0,
                            "open": 0.0, "payroll": 0.0}
        sectors[did]["payroll"] += employee["company_cost"]
    items = []
    for sector in sectors.values():
        sector["payroll"] = round(sector["payroll"], 2)
        sector["expenses"] = round(sector["paid"] + sector["open"], 2)
        sector["total"] = round(sector["expenses"] + sector["payroll"], 2)
        items.append(sector)
    items.sort(key=lambda sector: (-sector["total"], sector["name"]))
    return {"month": ym, "departments": items,
            "totals": {"paid": round(sum(item["paid"] for item in items), 2),
                       "open": round(sum(item["open"] for item in items), 2),
                       "payroll": round(sum(item["payroll"] for item in items), 2),
                       "total": round(sum(item["total"] for item in items), 2)}}

@app.get("/finance/department-costs", dependencies=[Depends(admin)])
def get_department_costs(month: Optional[str] = None):
    return department_costs(month)

# ---------- Matéria-prima ----------
class MaterialIn(BaseModel):
    name: str; unit: str = "un"; stock: float = Field(default=0, ge=0)
    unit_cost: float = Field(default=0, ge=0); minimum_stock: float = Field(default=0, ge=0)

def with_mat(m):
    return {**m, "value": round(m["stock"] * m["unit_cost"], 2)}

@app.post("/materials", dependencies=[Depends(admin)], status_code=201)
def add_material(m: MaterialIn, background: BackgroundTasks):
    with db() as c:
        cur = c.execute("INSERT INTO materials(name,unit,stock,unit_cost,minimum_stock) VALUES(?,?,?,?,?) RETURNING id",
                        (m.name, m.unit, m.stock, m.unit_cost, m.minimum_stock))
        material_id = cur.fetchall()[0]["id"]
    if m.stock == 0 or (m.minimum_stock > 0 and m.stock <= m.minimum_stock):
        background.add_task(dispatch_notifications,
                            f"Alerta SIGI: estoque de {m.name} está em {m.stock:g} {m.unit}; "
                            f"mínimo cadastrado {m.minimum_stock:g} {m.unit}.")
    return {"id": material_id}

@app.get("/materials", dependencies=[Depends(admin)])
def list_materials():
    with db() as c:
        return [with_mat(m) for m in rows(c.execute("SELECT * FROM materials ORDER BY name"))]

@app.put("/materials/{mid}", dependencies=[Depends(admin)])
def edit_material(mid: int, m: MaterialIn, background: BackgroundTasks):
    with db() as c:
        old = c.execute("SELECT stock,minimum_stock,name,unit FROM materials WHERE id=?", (mid,)).fetchone()
        if not old:
            raise HTTPException(404, "Material não encontrado.")
        cur = c.execute("UPDATE materials SET name=?,unit=?,stock=?,unit_cost=?,minimum_stock=? WHERE id=?",
                        (m.name, m.unit, m.stock, m.unit_cost, m.minimum_stock, mid))
    was_low = old["stock"] == 0 or (old["minimum_stock"] > 0 and old["stock"] <= old["minimum_stock"])
    is_low = m.stock == 0 or (m.minimum_stock > 0 and m.stock <= m.minimum_stock)
    if is_low and (not was_low or m.name != old["name"]):
        background.add_task(dispatch_notifications,
                            f"Alerta SIGI: estoque de {m.name} está em {m.stock:g} {m.unit}; "
                            f"mínimo cadastrado {m.minimum_stock:g} {m.unit}.")
    return {"ok": True}

@app.delete("/materials/{mid}", dependencies=[Depends(admin)])
def delete_material(mid: int):
    with db() as c: c.execute("DELETE FROM materials WHERE id=?", (mid,))
    return {"ok": True}

# ---------- Departamentos ----------
class DeptIn(BaseModel):
    name: str; lead: str = ""

@app.post("/departments", dependencies=[Depends(admin)], status_code=201)
def add_dept(d: DeptIn):
    with db() as c:
        cur = c.execute("INSERT INTO departments(name,lead) VALUES(?,?) RETURNING id", (d.name, d.lead))
        department_id = cur.fetchall()[0]["id"]
    return {"id": department_id}

@app.get("/departments", dependencies=[Depends(admin)])
def list_depts():
    with db() as c:
        ds = rows(c.execute("SELECT * FROM departments ORDER BY name"))
        counts = {r["department_id"]: r["n"] for r in c.execute(
            "SELECT department_id, COUNT(*) n FROM employees WHERE active=1 GROUP BY department_id")}
        return [{**d, "people": counts.get(d["id"], 0)} for d in ds]

@app.put("/departments/{did}", dependencies=[Depends(admin)])
def edit_dept(did: int, d: DeptIn):
    with db() as c:
        cur = c.execute("UPDATE departments SET name=?,lead=? WHERE id=?", (d.name, d.lead, did))
        if not cur.rowcount: raise HTTPException(404, "Departamento não encontrado.")
    return {"ok": True}

@app.delete("/departments/{did}", dependencies=[Depends(admin)])
def delete_dept(did: int):
    with db() as c:
        c.execute("UPDATE employees SET department_id=NULL WHERE department_id=?", (did,))
        c.execute("DELETE FROM departments WHERE id=?", (did,))
    return {"ok": True}

# ---------- Espaços ----------
SPACE_KINDS = ("gestao", "producao", "pessoas", "financeiro")

class SpaceIn(BaseModel):
    name: str; kind: str = "gestao"; admin_email: str = ""

@app.post("/spaces", dependencies=[Depends(admin)], status_code=201)
def add_space(s: SpaceIn):
    if s.kind not in SPACE_KINDS: raise HTTPException(400, "Tipo de espaço inválido.")
    with db() as c:
        cur = c.execute("INSERT INTO spaces(name,kind,admin_email) VALUES(?,?,?) RETURNING id", (s.name, s.kind, s.admin_email))
        space_id = cur.fetchall()[0]["id"]
    return {"id": space_id}

@app.get("/spaces", dependencies=[Depends(manager_or_admin)])
def list_spaces():
    with db() as c:
        return rows(c.execute("SELECT * FROM spaces ORDER BY id"))

@app.put("/spaces/{sid}", dependencies=[Depends(admin)])
def edit_space(sid: int, s: SpaceIn):
    if s.kind not in SPACE_KINDS: raise HTTPException(400, "Tipo de espaço inválido.")
    with db() as c:
        cur = c.execute("UPDATE spaces SET name=?,kind=?,admin_email=? WHERE id=?",
                        (s.name, s.kind, s.admin_email, sid))
        if not cur.rowcount: raise HTTPException(404, "Espaço não encontrado.")
    return {"ok": True}

@app.delete("/spaces/{sid}", dependencies=[Depends(admin)])
def delete_space(sid: int):
    with db() as c: c.execute("DELETE FROM spaces WHERE id=?", (sid,))
    return {"ok": True}

class BookingIn(BaseModel):
    space_id: int
    title: str = Field(min_length=1, max_length=160)
    starts_at: str
    ends_at: str
    department_id: Optional[int] = None

def booking_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Data inválida. Informe data e hora com fuso horário.")
    if parsed.tzinfo is None:
        raise HTTPException(400, "Informe o fuso horário no agendamento.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="minutes")

@app.get("/space-bookings", dependencies=[Depends(manager_or_admin)])
def list_space_bookings(start: Optional[str] = None, end: Optional[str] = None):
    conditions, params = [], []
    if start:
        conditions.append("ends_at>?")
        params.append(booking_time(start))
    if end:
        conditions.append("starts_at<?")
        params.append(booking_time(end))
    query = ("SELECT b.*,s.name AS space_name,d.name AS department_name "
             "FROM space_bookings b JOIN spaces s ON s.id=b.space_id "
             "LEFT JOIN departments d ON d.id=b.department_id")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY starts_at,space_name"
    with db() as c:
        return rows(c.execute(query, params))

@app.post("/space-bookings", dependencies=[Depends(principal)], status_code=201)
def add_space_booking(booking: BookingIn, who=Depends(principal)):
    if who["role"] not in ("admin", "manager"):
        raise HTTPException(403, "Somente gestores podem agendar espaços.")
    title = booking.title.strip()
    start, end = booking_time(booking.starts_at), booking_time(booking.ends_at)
    if not title or start >= end:
        raise HTTPException(400, "Informe um título e um horário final posterior ao inicial.")
    department_id = booking.department_id
    if who["role"] == "manager":
        if who["department_id"] is None:
            raise HTTPException(403, "Vincule sua conta a um departamento antes de agendar.")
        department_id = who["department_id"]
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if not c.execute("SELECT 1 FROM spaces WHERE id=?", (booking.space_id,)).fetchone():
            raise HTTPException(404, "Espaço não encontrado.")
        if department_id is not None and not c.execute("SELECT 1 FROM departments WHERE id=?",
                                                       (department_id,)).fetchone():
            raise HTTPException(404, "Departamento não encontrado.")
        overlap = c.execute("SELECT 1 FROM space_bookings WHERE space_id=? "
                            "AND starts_at<? AND ends_at>? LIMIT 1",
                            (booking.space_id, end, start)).fetchone()
        if overlap:
            raise HTTPException(409, "Este espaço já está reservado nesse horário.")
        cur = c.execute("INSERT INTO space_bookings(space_id,department_id,title,starts_at,ends_at,booked_by) "
                        "VALUES(?,?,?,?,?,?) RETURNING id",
                        (booking.space_id, department_id, title, start, end, who["user"]))
        return {"id": cur.fetchall()[0]["id"]}

@app.delete("/space-bookings/{booking_id}")
def delete_space_booking(booking_id: int, who=Depends(principal)):
    if who["role"] not in ("admin", "manager"):
        raise HTTPException(403, "Somente gestores podem cancelar reservas.")
    with db() as c:
        booking = c.execute("SELECT booked_by,department_id FROM space_bookings WHERE id=?",
                            (booking_id,)).fetchone()
        if not booking:
            raise HTTPException(404, "Agendamento não encontrado.")
        if who["role"] == "manager" and (booking["booked_by"] != who["user"] or
                                           booking["department_id"] != who["department_id"]):
            raise HTTPException(403, "Você só pode cancelar reservas do seu departamento feitas por você.")
        cur = c.execute("DELETE FROM space_bookings WHERE id=?", (booking_id,))
        if not cur.rowcount:
            raise HTTPException(404, "Agendamento não encontrado.")
    return {"ok": True}

class ClockIpPolicy(BaseModel):
    ranges: list[str] = Field(max_length=20)

@app.get("/settings/clock-ip", dependencies=[Depends(admin)])
def get_clock_ip_policy():
    with db() as c:
        stored = settings(c).get("clock_ip_ranges", "[]")
    try:
        return {"ranges": json.loads(stored)}
    except json.JSONDecodeError as error:
        raise HTTPException(500, "A política de IP salva está inválida.") from error

@app.put("/settings/clock-ip", dependencies=[Depends(admin)])
def update_clock_ip_policy(policy: ClockIpPolicy):
    try:
        ranges = list(dict.fromkeys(str(ip_network(value.strip(), strict=False))
                                    for value in policy.ranges))
    except ValueError:
        raise HTTPException(400, "Informe faixas CIDR válidas, por exemplo 192.168.1.0/24.")
    with db() as c:
        c.execute("INSERT INTO settings(k,v) VALUES('clock_ip_ranges',?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(ranges),))
    return {"ranges": ranges}

# ---------- Painéis de custo / DRE sintético ----------
class AnalysisIn(BaseModel):
    title: str; revenue: float = Field(default=0, ge=0); material: float = Field(default=0, ge=0)
    opex: float = Field(default=0, ge=0); tax: float = Field(default=0, ge=0)

def with_analysis(a):
    custos = a["opex"] + a["tax"]
    lucro = a["revenue"] - a["material"] - custos
    return {**a, "custos": round(custos, 2), "lucro": round(lucro, 2),
            "margem_pct": round(lucro / a["revenue"] * 100, 2) if a["revenue"] else 0}

@app.post("/cost-analyses", dependencies=[Depends(admin)], status_code=201)
def add_analysis(a: AnalysisIn):
    with db() as c:
        cur = c.execute("INSERT INTO cost_analyses(title,revenue,material,opex,tax) VALUES(?,?,?,?,?) RETURNING id",
                        (a.title, a.revenue, a.material, a.opex, a.tax))
        analysis_id = cur.fetchall()[0]["id"]
    return {"id": analysis_id}

@app.get("/cost-analyses", dependencies=[Depends(admin)])
def list_analyses():
    with db() as c:
        return [with_analysis(a) for a in rows(c.execute("SELECT * FROM cost_analyses ORDER BY id DESC"))]

@app.put("/cost-analyses/{aid}", dependencies=[Depends(admin)])
def edit_analysis(aid: int, a: AnalysisIn):
    with db() as c:
        cur = c.execute("UPDATE cost_analyses SET title=?,revenue=?,material=?,opex=?,tax=? WHERE id=?",
                        (a.title, a.revenue, a.material, a.opex, a.tax, aid))
        if not cur.rowcount: raise HTTPException(404, "Análise não encontrada.")
    return {"ok": True}

@app.delete("/cost-analyses/{aid}", dependencies=[Depends(admin)])
def delete_analysis(aid: int):
    with db() as c: c.execute("DELETE FROM cost_analyses WHERE id=?", (aid,))
    return {"ok": True}

def dre_of(c):
    ans = [with_analysis(a) for a in rows(c.execute("SELECT * FROM cost_analyses"))]
    if ans:
        rec = round(sum(a["revenue"] for a in ans), 2)
        mp = round(sum(a["material"] for a in ans), 2)
        ct = round(sum(a["custos"] for a in ans), 2)
    else:
        rec = round(sum(p["price"] for p in rows(c.execute("SELECT price FROM products"))), 2)
        mp = round(sum(m["stock"] * m["unit_cost"] for m in rows(c.execute("SELECT stock,unit_cost FROM materials"))), 2)
        ct = round(sum(p["overhead"] for p in rows(c.execute("SELECT overhead FROM products"))), 2)
    lucro = round(rec - mp - ct, 2)
    return {"receita": rec, "materia": mp, "custos": ct, "lucro": lucro,
            "margem_pct": round(lucro / rec * 100, 2) if rec else 0, "n": len(ans)}

# ---------- Integrações ----------
INT_KINDS = ("webhook", "csv", "whatsapp", "erp")

class IntegrationIn(BaseModel):
    name: str; kind: str; endpoint: str = ""; active: bool = True

@app.post("/integrations", dependencies=[Depends(admin)], status_code=201)
def add_integration(i: IntegrationIn):
    if i.kind not in INT_KINDS: raise HTTPException(400, "Tipo de integração inválido.")
    with db() as c:
        cur = c.execute("INSERT INTO integrations(name,kind,endpoint,active) VALUES(?,?,?,?) RETURNING id",
                        (i.name, i.kind, i.endpoint, int(i.active)))
        integration_id = cur.fetchall()[0]["id"]
    return {"id": integration_id}

@app.get("/integrations", dependencies=[Depends(admin)])
def list_integrations():
    with db() as c:
        return rows(c.execute("SELECT * FROM integrations ORDER BY id"))

@app.patch("/integrations/{iid}", dependencies=[Depends(admin)])
def toggle_integration(iid: int, i: IntegrationIn):
    if i.kind not in INT_KINDS: raise HTTPException(400, "Tipo de integração inválido.")
    with db() as c:
        cur = c.execute("UPDATE integrations SET name=?,kind=?,endpoint=?,active=? WHERE id=?",
                        (i.name, i.kind, i.endpoint, int(i.active), iid))
        if not cur.rowcount: raise HTTPException(404, "Integração não encontrada.")
    return {"ok": True}

@app.delete("/integrations/{iid}", dependencies=[Depends(admin)])
def delete_integration(iid: int):
    with db() as c: c.execute("DELETE FROM integrations WHERE id=?", (iid,))
    return {"ok": True}

class NotificationChannelIn(BaseModel):
    kind: Literal["email", "whatsapp"]
    recipient: str = Field(min_length=3, max_length=254)

def send_notification(channel, message):
    if channel["kind"] == "email":
        import smtplib
        from email.message import EmailMessage

        host, username, password = (os.getenv(name, "").strip() for name in
                                    ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
        sender = os.getenv("SMTP_FROM", username).strip()
        if not host or not username or not password or not sender:
            raise HTTPException(503, "Configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD e SMTP_FROM no ambiente.")
        email = EmailMessage()
        email["Subject"] = "Aviso operacional SIGI"
        email["From"] = sender
        email["To"] = channel["recipient"]
        email.set_content(message)
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=8) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(email)
    else:
        token = os.getenv("WHATSAPP_TOKEN", "").strip()
        phone_id = os.getenv("WHATSAPP_PHONE_ID", "").strip()
        if not token or not phone_id:
            raise HTTPException(503, "Configure WHATSAPP_TOKEN e WHATSAPP_PHONE_ID no ambiente.")
        endpoint = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
        body = json.dumps({"messaging_product": "whatsapp", "to": channel["recipient"],
                           "type": "text", "text": {"body": message}}).encode()
        request = Request(endpoint, data=body, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        })
        with urlopen(request, timeout=8) as response:
            if response.status >= 300:
                raise RuntimeError(f"WhatsApp respondeu com HTTP {response.status}.")

def dispatch_notifications(message):
    import smtplib

    with db() as c:
        channels = rows(c.execute("SELECT kind,recipient FROM notification_channels WHERE active=1"))
    for channel in channels:
        try:
            send_notification(channel, message)
        except (HTTPException, OSError, ValueError, RuntimeError, smtplib.SMTPException) as error:
            logging.getLogger(__name__).exception(
                "Falha ao enviar notificação %s para %s: %s",
                channel["kind"], channel["recipient"], error,
            )

@app.get("/notification-channels", dependencies=[Depends(admin)])
def list_notification_channels():
    with db() as c:
        return rows(c.execute("SELECT id,kind,recipient,active FROM notification_channels ORDER BY id"))

@app.post("/notification-channels", dependencies=[Depends(admin)], status_code=201)
def add_notification_channel(channel: NotificationChannelIn):
    recipient = channel.recipient.strip()
    if channel.kind == "email" and ("@" not in recipient or "." not in recipient.rsplit("@", 1)[-1]):
        raise HTTPException(400, "Informe um endereço de e-mail válido.")
    if channel.kind == "whatsapp" and not recipient.lstrip("+").isdigit():
        raise HTTPException(400, "Informe o telefone WhatsApp com código do país.")
    with db() as c:
        cur = c.execute("INSERT INTO notification_channels(kind,recipient,active) VALUES(?,?,1) RETURNING id",
                        (channel.kind, recipient))
        channel_id = cur.fetchall()[0]["id"]
    return {"id": channel_id}

@app.delete("/notification-channels/{channel_id}", dependencies=[Depends(admin)])
def delete_notification_channel(channel_id: int):
    with db() as c:
        cur = c.execute("DELETE FROM notification_channels WHERE id=?", (channel_id,))
        if not cur.rowcount:
            raise HTTPException(404, "Canal de notificação não encontrado.")
    return {"ok": True}

class NotificationTest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)

@app.post("/notifications/test", dependencies=[Depends(admin)])
def test_notifications(payload: NotificationTest):
    with db() as c:
        channels = rows(c.execute("SELECT kind,recipient FROM notification_channels WHERE active=1"))
    if not channels:
        raise HTTPException(400, "Cadastre um canal de notificação antes de enviar.")
    for channel in channels:
        send_notification(channel, payload.message)
    return {"sent": len(channels)}

# ---------- Ponto com localização em tempo real ----------
@app.get("/clock/employees")
def clock_employees(request: Request):
    with db() as c:
        verify_clock_ip(request.client.host if request.client else "", settings(c))
        return rows(c.execute("SELECT id,name FROM employees WHERE active=1 ORDER BY name"))

def check_employee(c, employee_id: int, pin: str):
    e = c.execute("SELECT * FROM employees WHERE id=? AND active=1", (employee_id,)).fetchone()
    if not e: raise HTTPException(404, "Funcionário não encontrado.")
    if e["failed_pins"] >= MAX_PIN_TRIES:
        raise HTTPException(423, "PIN bloqueado por tentativas erradas. Fale com o administrador.")
    if not hmac.compare_digest(hash_pin(pin, e["pin_salt"]), e["pin_hash"]):
        c.execute("UPDATE employees SET failed_pins=failed_pins+1 WHERE id=?", (e["id"],))
        c.commit()
        raise HTTPException(401, "PIN incorreto.")
    c.execute("UPDATE employees SET failed_pins=0 WHERE id=?", (e["id"],))   # também abre a transação de escrita
    return e

class Live(BaseModel):
    employee_id: int; pin: str
    lat: float = Field(ge=-90, le=90); lng: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(default=0, ge=0)

def verify_clock_ip(client_host, settings_row):
    try:
        ranges = json.loads(settings_row.get("clock_ip_ranges", "[]"))
        if ranges:
            address = ip_address(client_host or "")
            if not any(address in ip_network(value) for value in ranges):
                raise HTTPException(403, "Este ponto só pode ser registrado pela rede autorizada.")
    except (ValueError, json.JSONDecodeError) as error:
        raise HTTPException(403, "Não foi possível validar o IP da rede autorizada.") from error

@app.post("/clock/live")
def clock_live(b: Live, request: Request):
    """Chamado a cada poucos segundos pelo aparelho enquanto o funcionário está na tela de ponto.
    Só informa a distância (para o funcionário se posicionar); não grava nada."""
    with db() as c:
        check_employee(c, b.employee_id, b.pin)
        configured = settings(c)
        verify_clock_ip(request.client.host if request.client else "", configured)
        w = workplace_of(configured)
    if not w: raise HTTPException(409, "Local de trabalho não configurado.")
    d = haversine(w["lat"], w["lng"], b.lat, b.lng)
    gps_ok = b.accuracy_m <= MAX_GPS_ERROR_M
    return {"distance_m": round(d), "radius_m": round(w["radius_m"]), "accuracy_m": round(b.accuracy_m),
            "gps_ok": gps_ok, "inside": gps_ok and d <= w["radius_m"]}

class Sample(BaseModel):
    lat: float = Field(ge=-90, le=90); lng: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(default=0, ge=0); ts_ms: int = 0

class Clock(BaseModel):
    employee_id: int; pin: str
    consent: bool = False
    samples: list[Sample] = Field(min_length=1, max_length=20)
    task_id: Optional[int] = None

@app.get("/clock/tasks")
def clock_tasks(employee_id: int, pin: str, request: Request):
    with db() as c:
        employee = check_employee(c, employee_id, pin)
        verify_clock_ip(request.client.host if request.client else "", settings(c))
        return rows(c.execute(
            "SELECT id,title,due FROM tasks WHERE status=1 AND (assignee='' OR lower(assignee)=lower(?)) "
            "ORDER BY due,id", (employee["name"],)))

def entry_hash(prev, nsr, cpf, at, eid, kind, lat, lng):
    return hashlib.sha256(f"{prev}|{nsr}|{cpf}|{at}|{eid}|{kind}|{lat:.6f}|{lng:.6f}".encode()).hexdigest()

@app.post("/clock")
def clock(b: Clock, request: Request, background: BackgroundTasks):
    if not b.consent:
        raise HTTPException(400, "É preciso autorizar o uso da localização para bater ponto.")
    now = utcnow(); now_ms = now.timestamp() * 1000
    with db() as c:
        e = check_employee(c, b.employee_id, b.pin)
        s = settings(c); w = workplace_of(s)
        verify_clock_ip(request.client.host if request.client else "", s)
        if not w: raise HTTPException(409, "Local de trabalho não configurado.")
        best = min(b.samples, key=lambda x: x.accuracy_m)
        dist = haversine(w["lat"], w["lng"], best.lat, best.lng)
        spread = max(haversine(p.lat, p.lng, q.lat, q.lng) for p in b.samples for q in b.samples)

        reason = ""
        if any(x.ts_ms and abs(now_ms - x.ts_ms) > 120_000 for x in b.samples):
            reason = "A hora do aparelho parece incorreta ou a localização é antiga. Ajuste o relógio e tente de novo."
        elif best.accuracy_m > MAX_GPS_ERROR_M:
            reason = f"Sinal de GPS fraco (±{best.accuracy_m:.0f} m). Vá para um local aberto."
        elif spread > MAX_SPREAD_M:
            reason = "Localização instável. Fique parado em local aberto e tente de novo."
        elif dist > w["radius_m"]:
            reason = f"Você está a {dist:.0f} m da sede (máximo {w['radius_m']:.0f} m)."
        else:
            prev = c.execute("SELECT lat,lng,at FROM time_entries WHERE employee_id=? AND accepted=1 "
                             "ORDER BY id DESC LIMIT 1", (e["id"],)).fetchone()
            if prev:
                dt = (now - datetime.fromisoformat(prev["at"])).total_seconds()
                if 0 < dt < 3600 and haversine(prev["lat"], prev["lng"], best.lat, best.lng) / dt > MAX_SPEED_MS:
                    reason = "Deslocamento impossível desde o último registro. Procure o gestor."
        ok = not reason

        a, z = day_range(local(now).date())
        n = c.execute("SELECT COUNT(*) FROM time_entries WHERE employee_id=? AND accepted=1 AND at>=? AND at<?",
                      (e["id"], a, z)).fetchone()[0]
        kind = "entrada" if n % 2 == 0 else "saida"
        task_id = b.task_id
        previous_task = None
        if kind == "saida":
            previous_entry = c.execute(
                "SELECT task_id FROM time_entries WHERE employee_id=? AND accepted=1 AND at>=? AND at<? "
                "ORDER BY id DESC LIMIT 1", (e["id"], a, z)).fetchone()
            previous_task = previous_entry["task_id"] if previous_entry else None
            if task_id not in (None, previous_task):
                raise HTTPException(400, "Na saída, mantenha a tarefa vinculada à entrada.")
            task_id = previous_task
        if task_id is not None:
            task = c.execute("SELECT title,assignee,status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or (kind == "entrada" and task["status"] != 1):
                raise HTTPException(400, "Selecione uma tarefa que esteja em andamento.")
            if kind == "entrada" and task["assignee"] and task["assignee"].casefold() != e["name"].casefold():
                raise HTTPException(403, "Esta tarefa está atribuída a outra pessoa.")
        at = iso(now)
        nsr = prev_h = h = None
        if ok:   # NSR sequencial + encadeamento de hash: qualquer alteração posterior é detectável
            last = c.execute("SELECT nsr,hash FROM time_entries WHERE nsr IS NOT NULL ORDER BY nsr DESC LIMIT 1").fetchone()
            nsr = (last["nsr"] if last else 0) + 1
            prev_h = last["hash"] if last else "0" * 64
            h = entry_hash(prev_h, nsr, e["cpf"], at, e["id"], kind, best.lat, best.lng)
        c.execute("INSERT INTO time_entries(employee_id,kind,at,lat,lng,distance_m,accepted,reason,cpf,nsr,prev_hash,hash,samples,spread_m,task_id,labor_rate)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (e["id"], kind, at, best.lat, best.lng, round(dist, 1), int(ok), reason, e["cpf"], nsr, prev_h, h,
                   len(b.samples), round(spread, 1), task_id, e["salary"] / 220))
        if task_id is not None and kind == "saida":
            run_task_automations(c, task_id, background)
    if not ok: raise HTTPException(403, reason)
    return {"kind": kind, "at": at, "distance_m": round(dist), "nsr": nsr, "hash": h,
            "comprovante": {"titulo": "Comprovante de Registro de Ponto do Trabalhador",
                            "empresa": {"nome": s.get("co_name", ""), "cnpj": s.get("co_cnpj", "")},
                            "trabalhador": {"nome": e["name"], "cpf": e["cpf"]},
                            "nsr": nsr, "tipo": kind, "data_hora": at, "fuso": f"UTC{TZ_OFFSET:+g}",
                            "hash_sha256": h}}

@app.get("/time-entries", dependencies=[Depends(admin)])
def time_entries(employee_id: Optional[int] = None, limit: int = 100):
    q, a = "SELECT t.*, e.name FROM time_entries t JOIN employees e ON e.id=t.employee_id", []
    if employee_id: q += " WHERE employee_id=?"; a.append(employee_id)
    with db() as c:
        return rows(c.execute(q + " ORDER BY t.id DESC LIMIT ?", (*a, limit)))

@app.get("/time-entries/verify", dependencies=[Depends(admin)])
def verify_entries():
    with db() as c:
        rs = rows(c.execute("SELECT * FROM time_entries WHERE nsr IS NOT NULL ORDER BY nsr"))
    prev = "0" * 64
    for i, r in enumerate(rs):
        good = entry_hash(prev, r["nsr"], r["cpf"], r["at"], r["employee_id"], r["kind"], r["lat"], r["lng"])
        if r["nsr"] != i + 1 or r["prev_hash"] != prev or r["hash"] != good:
            return {"ok": False, "checked": i, "broken_nsr": r["nsr"]}
        prev = r["hash"]
    return {"ok": True, "checked": len(rs)}

def csv_safe(v):
    v = str(v if v is not None else "")
    return "'" + v if v[:1] in "=+-@" else v

@app.get("/time-entries/export.csv", dependencies=[Depends(admin)])
def export_entries(month: Optional[str] = None):
    q = ("SELECT t.nsr,t.cpf,e.name,t.at,t.kind,t.hash FROM time_entries t JOIN employees e "
         "ON e.id=t.employee_id WHERE t.nsr IS NOT NULL")
    args = []
    if month:
        a, z = month_bounds(month); q += " AND t.at>=? AND t.at<?"; args = [a, z]
    with db() as c: rs = c.execute(q + " ORDER BY t.nsr", args).fetchall()
    out = io.StringIO(); w = csv.writer(out, delimiter=";")
    w.writerow(["NSR", "CPF", "Nome", "Data/hora (UTC)", "Tipo", "Hash SHA-256"])
    for r in rs: w.writerow([csv_safe(x) for x in r])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="marcacoes.csv"'})

# ---------- Painel ----------
@app.get("/dashboard", dependencies=[Depends(admin)])
def dashboard():
    now = utcnow(); today = local(now).date()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    month = local(now).strftime("%Y-%m")
    with db() as c:
        a, z = day_range(today)
        present = c.execute("""SELECT COUNT(*) FROM (SELECT employee_id FROM time_entries
            WHERE accepted=1 AND at>=? AND at<? GROUP BY employee_id HAVING (COUNT(*) & 1)=1) AS present_employees""",
                            (a, z)).fetchone()[0]
        tasks = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
        task_rows = rows(c.execute("SELECT id,title,assignee,due,status,severity FROM tasks ORDER BY id DESC"))
        prods = [with_costs(p) for p in rows(c.execute("SELECT * FROM products"))]
        emps = rows(c.execute("SELECT * FROM employees WHERE active=1"))
        attendance_series = []
        for day in days:
            start, end = day_range(day)
            count = c.execute("SELECT COUNT(DISTINCT employee_id) FROM time_entries "
                              "WHERE accepted=1 AND at>=? AND at<?", (start, end)).fetchone()[0]
            attendance_series.append({"date": day.isoformat(), "employees": count})
        att = [day["employees"] for day in attendance_series]
        recent = rows(c.execute("""SELECT t.at,t.kind,t.distance_m,t.accepted,t.reason,e.name FROM time_entries t
            JOIN employees e ON e.id=t.employee_id ORDER BY t.id DESC LIMIT 6"""))
        wp = "lat" in settings(c)
        dre = dre_of(c)
        lots = rows(c.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 8"))
        materials = rows(c.execute("SELECT id,name,stock,unit,minimum_stock FROM materials ORDER BY name"))
        month_hours = timesheet_data(c, month)
        n_mat = c.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
        n_dep = c.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        n_sp = c.execute("SELECT COUNT(*) FROM spaces").fetchone()[0]
    employee_overtime = {}
    hours_worked = overtime_hours = 0
    for employee in emps:
        record = month_hours.get(employee["id"], {"days": {}, "total": 0, "overtime": 0, "open": None})
        worked = record["total"]
        overtime = record["overtime"]
        if record["open"]:
            elapsed = max(0, (now - record["open"]).total_seconds() / 3600)
            worked += elapsed
            today_key = local(record["open"]).date().isoformat()
            completed_today = record["days"].get(today_key, 0)
            overtime += max(0, completed_today + elapsed - DAILY_HOURS) - max(0, completed_today - DAILY_HOURS)
        hours_worked += worked
        overtime_hours += overtime
        employee_overtime[employee["id"]] = overtime
    alerts = []
    for material in materials:
        if material["stock"] <= 0:
            alerts.append({"severity": "critico", "title": f"Estoque zerado: {material['name']}",
                           "detail": f"Sem saldo disponível. Mínimo cadastrado: {material['minimum_stock']:g} {material['unit']}.",
                           "tab": "materia"})
        elif material["minimum_stock"] > 0 and material["stock"] <= material["minimum_stock"]:
            alerts.append({"severity": "atencao", "title": f"Estoque abaixo do mínimo: {material['name']}",
                           "detail": f"{material['stock']:g} {material['unit']} disponíveis; mínimo de "
                                     f"{material['minimum_stock']:g} {material['unit']}.",
                           "tab": "materia"})
    for task in task_rows:
        if task["status"] == 2:
            continue
        critical = task["severity"] == "critico"
        overdue = bool(task["due"] and task["due"] < today.isoformat())
        if critical or overdue:
            reasons = []
            if critical:
                reasons.append("prioridade crítica")
            if overdue:
                reasons.append(f"prazo vencido em {task['due']}")
            alerts.append({"severity": "critico" if critical else "atencao",
                           "title": f"Tarefa em risco: {task['title']}",
                           "detail": " · ".join(reasons), "tab": "quadros"})
    for employee in emps:
        overtime = employee_overtime[employee["id"]]
        if overtime >= DAILY_HOURS:
            alerts.append({"severity": "atencao", "title": f"Horas extras elevadas: {employee['name']}",
                           "detail": f"{overtime:.1f} h extras neste mês (alerta a partir de {DAILY_HOURS} h).",
                           "tab": "ponto"})
    alerts.sort(key=lambda alert: (alert["severity"] != "critico", alert["title"]))
    gross = sum(e["salary"] for e in emps); ins = sum(inss(e["salary"]) for e in emps)
    ben = sum(e["benefits"] for e in emps); charges = gross * EMPLOYER_CHARGES
    return {"present_now": present, "employees_active": len(emps),
            "tasks": [tasks.get(i, 0) for i in range(3)],
            "avg_margin_pct": round(sum(p["margin_pct"] for p in prods) / len(prods), 1) if prods else 0,
            "margins": [{"name": p["name"], "pct": p["margin_pct"]} for p in prods],
            "salaries": [e["salary"] for e in emps], "attendance_14d": att,
            "attendance_series": attendance_series,
            "hours_worked": round(hours_worked, 1),
            "overtime_hours": round(overtime_hours, 1),
            "overtime_threshold_hours": DAILY_HOURS, "current_month": month,
            "alerts": alerts, "updated_at": iso(now), "recent": recent,
            "payroll": {"net": round(gross - ins + ben, 2), "inss": round(ins, 2), "charges": round(charges, 2),
                        "company_cost": round(gross + charges + ben, 2)},
            "setup": {"workplace": wp, "employees": bool(emps), "products": bool(prods), "tasks": bool(sum(tasks.values())),
                      "materials": bool(n_mat), "departments": bool(n_dep), "spaces": bool(n_sp)},
            "dre": dre, "lots": lots}

# ---------- Front end (mesmo domínio da API) ----------
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
