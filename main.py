import json
import os
import sqlite3
import subprocess
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI  = os.getenv("GOOGLE_REDIRECT_URI", "")  # e.g. https://yourdomain.com/auth/google/callback
NOTION_TOKEN         = os.getenv("NOTION_TOKEN", "")
OPENROUTER_API_KEY   = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")
IBKR_FLEX_TOKEN      = os.getenv("IBKR_FLEX_TOKEN", "")
IBKR_FLEX_QUERY_ID   = os.getenv("IBKR_FLEX_QUERY_ID", "")

# Notion DB IDs
NOTION_DB_STM       = "4f3b8c95-709b-465d-a2f3-be5dbdfce9bd"
NOTION_DB_WATCHLIST = "1a2bdf3b-4bf0-8084-a91c-000be6c9c931"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "dashboard.db"
GOOGLE_TOKEN_PATH = DATA_DIR / "google_token.json"
DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'todo',
            due_date TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            color TEXT DEFAULT 'yellow',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            start_datetime TEXT NOT NULL,
            end_datetime TEXT,
            color TEXT DEFAULT 'blue',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ibkr_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE,
            order_id TEXT,
            symbol TEXT,
            asset_category TEXT,
            currency TEXT DEFAULT 'USD',
            trade_date TEXT,
            date_time TEXT,
            buy_sell TEXT,
            quantity REAL,
            price REAL,
            proceeds REAL,
            commission REAL DEFAULT 0,
            pnl REAL DEFAULT 0,
            synced_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ibkr_summary (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    # Migration: add order_id if missing (existing deployments)
    try:
        conn.execute("ALTER TABLE ibkr_trades ADD COLUMN order_id TEXT")
        conn.commit()
    except Exception:
        pass
    conn.close()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    init_db()
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        asyncio.create_task(_schedule_daily_digest())
    if IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID:
        asyncio.create_task(_schedule_ibkr_sync())
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="VPS Dashboard", lifespan=lifespan)

# Trust proxy headers from Cloudflare Tunnel so request.url_for() generates correct HTTPS URLs
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Mount Mission Control as sub-app
try:
    import importlib.util as _ilu
    _mc_spec = _ilu.spec_from_file_location(
        "mission_control_main", BASE_DIR / "mission-control" / "main.py"
    )
    _mc_mod = _ilu.module_from_spec(_mc_spec)
    _mc_spec.loader.exec_module(_mc_mod)
    app.mount("/mission-control", _mc_mod.app)
except Exception as _e:
    print(f"[mission-control] Impossible de monter le sub-app: {_e}")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    status: str = "todo"
    due_date: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[str] = None


class NoteCreate(BaseModel):
    content: str
    color: str = "yellow"


class NoteUpdate(BaseModel):
    content: Optional[str] = None
    color: Optional[str] = None


class EventCreate(BaseModel):
    title: str
    description: str = ""
    start_datetime: str
    end_datetime: Optional[str] = None
    color: str = "blue"


# ---------------------------------------------------------------------------
# Routes - UI
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/mission-control/")


@app.get("/widget.js")
async def get_widget():
    from fastapi.responses import FileResponse
    return FileResponse(BASE_DIR / "static" / "widget.js", media_type="application/javascript")


# ---------------------------------------------------------------------------
# Routes - Crons
# ---------------------------------------------------------------------------

def _cron_field_matches(field: str, value: int) -> bool:
    for part in field.split(','):
        part = part.strip()
        if part == '*':
            return True
        if '/' in part:
            base, step_s = part.split('/', 1)
            step = int(step_s)
            if base == '*':
                if value % step == 0:
                    return True
            elif '-' in base:
                a, b = base.split('-')
                if int(a) <= value <= int(b) and (value - int(a)) % step == 0:
                    return True
            else:
                if value >= int(base) and (value - int(base)) % step == 0:
                    return True
        elif '-' in part:
            a, b = part.split('-')
            if int(a) <= value <= int(b):
                return True
        else:
            try:
                if int(part) == value:
                    return True
            except ValueError:
                pass
    return False


@app.get("/api/crons")
def get_crons():
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.splitlines()
    except Exception:
        return []

    now = datetime.now()
    # cron dow: 0=Sun, 1=Mon, ..., 6=Sat (isoweekday: Mon=1..Sun=7)
    cron_dow = now.isoweekday() % 7  # Sun=0, Mon=1, ..., Sat=6
    items = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('@'):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        m_f, h_f, dom_f, mon_f, dow_f, cmd = parts

        if not _cron_field_matches(mon_f, now.month):
            continue

        dom_star = dom_f == '*'
        dow_star = dow_f == '*'
        dom_ok = _cron_field_matches(dom_f, now.day)
        dow_ok = _cron_field_matches(dow_f.replace('7', '0'), cron_dow)

        if dom_star and dow_star:
            pass
        elif not dom_star and not dow_star:
            if not (dom_ok or dow_ok):
                continue
        elif not dom_star:
            if not dom_ok:
                continue
        else:
            if not dow_ok:
                continue

        run_times = []
        for h in range(24):
            if not _cron_field_matches(h_f, h):
                continue
            for mi in range(60):
                if not _cron_field_matches(m_f, mi):
                    continue
                run_times.append(f"{h:02d}:{mi:02d}")
                if len(run_times) >= 8:
                    break
            if len(run_times) >= 8:
                break

        display_cmd = cmd if len(cmd) <= 60 else cmd[:57] + '…'
        items.append({
            "schedule": f"{m_f} {h_f} {dom_f} {mon_f} {dow_f}",
            "command":  display_cmd,
            "times":    run_times,
            "next":     run_times[0] if run_times else None,
        })

    items.sort(key=lambda x: x["next"] or "99:99")
    return items


# ---------------------------------------------------------------------------
# Routes - Tasks
# ---------------------------------------------------------------------------

@app.get("/api/tasks")
def get_tasks(status: Optional[str] = None, date_filter: Optional[str] = None):
    conn = get_db()
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if date_filter:
        query += " AND due_date = ?"
        params.append(date_filter)
    query += " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, due_date ASC"
    tasks = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(t) for t in tasks]


@app.post("/api/tasks", status_code=201)
def create_task(task: TaskCreate):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO tasks (title, description, priority, status, due_date) VALUES (?, ?, ?, ?, ?)",
        (task.title, task.description, task.priority, task.status, task.due_date),
    )
    conn.commit()
    new_id = cursor.lastrowid
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (new_id,)).fetchone()
    conn.close()
    return dict(row)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, update: TaskUpdate):
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        set_clause += ", updated_at = datetime('now')"
        conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?",
            list(fields.values()) + [task_id],
        )
        conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int):
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Routes - Notes
# ---------------------------------------------------------------------------

@app.get("/api/notes")
def get_notes():
    conn = get_db()
    notes = conn.execute("SELECT * FROM notes ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(n) for n in notes]


@app.post("/api/notes", status_code=201)
def create_note(note: NoteCreate):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO notes (content, color) VALUES (?, ?)",
        (note.content, note.color),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


@app.patch("/api/notes/{note_id}")
def update_note(note_id: int, update: NoteUpdate):
    conn = get_db()
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Note not found")
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        set_clause += ", updated_at = datetime('now')"
        conn.execute(
            f"UPDATE notes SET {set_clause} WHERE id = ?",
            list(fields.values()) + [note_id],
        )
        conn.commit()
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/notes/{note_id}", status_code=204)
def delete_note(note_id: int):
    conn = get_db()
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Routes - Events (Agenda)
# ---------------------------------------------------------------------------

@app.get("/api/events")
def get_events(start: Optional[str] = None, end: Optional[str] = None):
    conn = get_db()
    query = "SELECT * FROM events WHERE 1=1"
    params = []
    if start:
        query += " AND start_datetime >= ?"
        params.append(start)
    if end:
        query += " AND start_datetime <= ?"
        params.append(end)
    query += " ORDER BY start_datetime ASC"
    events = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(e) for e in events]


@app.post("/api/events", status_code=201)
def create_event(event: EventCreate):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO events (title, description, start_datetime, end_datetime, color) VALUES (?, ?, ?, ?, ?)",
        (event.title, event.description, event.start_datetime, event.end_datetime, event.color),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM events WHERE id = ?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/events/{event_id}", status_code=204)
def delete_event(event_id: int):
    conn = get_db()
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Google Calendar - helpers
# ---------------------------------------------------------------------------

def _google_client_config():
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def get_google_credentials():
    """Load credentials from disk. Returns None if not yet authorized."""
    if not GOOGLE_TOKEN_PATH.exists():
        return None
    data = json.loads(GOOGLE_TOKEN_PATH.read_text())
    from google.oauth2.credentials import Credentials
    creds = Credentials(
        token=data["token"],
        refresh_token=data.get("refresh_token"),
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data.get("scopes"),
    )
    if data.get("expiry"):
        raw = data["expiry"].replace("Z", "").split("+")[0]  # strip tz → naive UTC
        creds.expiry = datetime.fromisoformat(raw)
    return creds


def _persist_credentials(creds):
    existing = json.loads(GOOGLE_TOKEN_PATH.read_text()) if GOOGLE_TOKEN_PATH.exists() else {}
    existing["token"] = creds.token
    existing["expiry"] = creds.expiry.replace(tzinfo=None).isoformat() if creds.expiry else None
    if creds.refresh_token:
        existing["refresh_token"] = creds.refresh_token
    GOOGLE_TOKEN_PATH.write_text(json.dumps(existing, indent=2))


# ---------------------------------------------------------------------------
# Routes - Google OAuth2
# ---------------------------------------------------------------------------

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _get_redirect_uri(request: Request) -> str:
    """Return the OAuth callback URI: env override > auto-detected from request."""
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    return str(request.url_for("auth_google_callback"))


@app.get("/auth/google")
async def auth_google(request: Request):
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(_google_client_config(), scopes=GOOGLE_SCOPES)
    flow.redirect_uri = _get_redirect_uri(request)
    # access_type=offline ensures we get a refresh_token; prompt=consent forces it even if already granted
    authorization_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return RedirectResponse(authorization_url)


@app.get("/auth/google/callback", name="auth_google_callback")
async def auth_google_callback(request: Request, code: str, state: str = ""):
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(_google_client_config(), scopes=GOOGLE_SCOPES)
    flow.redirect_uri = _get_redirect_uri(request)
    flow.fetch_token(code=code)
    creds = flow.credentials
    token_data = {
        "token":          creds.token,
        "refresh_token":  creds.refresh_token,
        "token_uri":      creds.token_uri,
        "client_id":      creds.client_id,
        "client_secret":  creds.client_secret,
        "scopes":         list(creds.scopes) if creds.scopes else GOOGLE_SCOPES,
        "expiry":         creds.expiry.isoformat() if creds.expiry else None,
    }
    GOOGLE_TOKEN_PATH.write_text(json.dumps(token_data, indent=2))
    return RedirectResponse("/mission-control/?tab=calendar")


# ---------------------------------------------------------------------------
# Routes - Google Calendar events
# ---------------------------------------------------------------------------

@app.get("/api/google/status")
def google_status():
    return {"connected": GOOGLE_TOKEN_PATH.exists()}


@app.get("/api/events/google")
def get_google_events(start: Optional[str] = None, end: Optional[str] = None):
    """Fetch events from the primary Google Calendar. Returns [] if not authenticated."""
    creds = get_google_credentials()
    if creds is None:
        return []
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from googleapiclient.discovery import build

        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            _persist_credentials(creds)

        service = build("calendar", "v3", credentials=creds)

        now = datetime.utcnow()
        time_min = (start + "T00:00:00Z") if start else now.strftime("%Y-%m-01T00:00:00Z")
        time_max = (end   + "T23:59:59Z") if end   else (now + timedelta(days=60)).strftime("%Y-%m-%dT23:59:59Z")

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        ).execute()

        events = []
        for item in result.get("items", []):
            start_raw = item.get("start", {})
            end_raw   = item.get("end", {})
            start_dt  = start_raw.get("dateTime") or (start_raw.get("date", "") + "T00:00:00Z")
            end_dt    = end_raw.get("dateTime")   or (end_raw.get("date", "") + "T00:00:00Z")
            events.append({
                "id":             "gcal_" + item["id"],
                "title":          item.get("summary", "(Sans titre)"),
                "description":    item.get("description", ""),
                "start_datetime": start_dt,
                "end_datetime":   end_dt,
                "color":          "google",
                "source":         "google",
                "html_link":      item.get("htmlLink", ""),
            })
        return events
    except Exception as e:
        print(f"[Google Calendar] Erreur: {e}")
        return []


# ---------------------------------------------------------------------------
# Routes - Dashboard summary
# ---------------------------------------------------------------------------

@app.get("/api/summary")
def get_summary():
    today = date.today().isoformat()
    conn = get_db()
    tasks_today = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE due_date = ? AND status != 'done'", (today,)
    ).fetchone()["cnt"]
    tasks_overdue = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE due_date < ? AND status != 'done'", (today,)
    ).fetchone()["cnt"]
    tasks_done_today = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE due_date = ? AND status = 'done'", (today,)
    ).fetchone()["cnt"]
    events_today = conn.execute(
        "SELECT COUNT(*) as cnt FROM events WHERE start_datetime LIKE ?", (f"{today}%",)
    ).fetchone()["cnt"]
    conn.close()
    return {
        "date": today,
        "tasks_today": tasks_today,
        "tasks_overdue": tasks_overdue,
        "tasks_done_today": tasks_done_today,
        "events_today": events_today,
    }


# ---------------------------------------------------------------------------
# Notion helpers
# ---------------------------------------------------------------------------

def _notion_client():
    from notion_client import Client
    return Client(auth=NOTION_TOKEN)


def _prop_val(prop: dict):
    """Extract a simple Python value from a Notion property dict."""
    if not prop:
        return None
    t = prop.get("type")
    if t == "title":
        texts = prop.get("title", [])
        return texts[0]["plain_text"] if texts else ""
    if t == "number":
        return prop.get("number")
    if t == "formula":
        f = prop.get("formula", {})
        ft = f.get("type")
        return f.get(ft)
    if t == "date":
        d = prop.get("date")
        return d["start"] if d else None
    if t == "select":
        s = prop.get("select")
        return s["name"] if s else None
    if t == "checkbox":
        return prop.get("checkbox")
    if t == "rich_text":
        texts = prop.get("rich_text", [])
        return texts[0]["plain_text"] if texts else ""
    if t == "url":
        return prop.get("url")
    return None


# ---------------------------------------------------------------------------
# Routes - Notion
# ---------------------------------------------------------------------------

@app.get("/api/notion/status")
def notion_status():
    return {"connected": bool(NOTION_TOKEN)}


@app.get("/api/notion/stm")
def get_notion_stm(open_only: bool = False):
    """Return STM trading positions. open_only=true filters to open positions (no exit date)."""
    if not NOTION_TOKEN:
        return []
    try:
        notion = _notion_client()
        filters = []
        if open_only:
            filters.append({"property": "Sortie", "date": {"is_empty": True}})
        query_args = {"page_size": 50, "sorts": [{"property": "Entrée", "direction": "descending"}]}
        if filters:
            query_args["filter"] = {"and": filters} if len(filters) > 1 else filters[0]
        result = notion.data_sources.query(NOTION_DB_STM, **query_args)
        rows = []
        for item in result.get("results", []):
            p = item.get("properties", {})
            rows.append({
                "id":        item["id"],
                "nom":       _prop_val(p.get("Nom")),
                "entree":    _prop_val(p.get("Entrée")),
                "sortie":    _prop_val(p.get("Sortie")),
                "sl":        _prop_val(p.get("SL")),
                "tp":        _prop_val(p.get("TP")),
                "rr":        _prop_val(p.get("RR")),
                "pnl":       _prop_val(p.get("PnL")),
                "pnl_pct":   _prop_val(p.get("PnL Port-%")),
                "sizing":    _prop_val(p.get("Sizing")),
                "live_days": _prop_val(p.get("Live")),
                "jour":      _prop_val(p.get("Jour")),
                "url":       f"https://www.notion.so/{item['id'].replace('-','')}",
            })
        return rows
    except Exception as e:
        print(f"[Notion STM] Erreur: {e}")
        return []


@app.get("/api/notion/watchlist")
def get_notion_watchlist():
    """Return WATCHLIST items from Notion."""
    if not NOTION_TOKEN:
        return []
    try:
        notion = _notion_client()
        result = notion.data_sources.query(
            NOTION_DB_WATCHLIST,
            page_size=30,
            sorts=[{"property": "Date de création", "direction": "descending"}],
        )
        rows = []
        for item in result.get("results", []):
            p = item.get("properties", {})
            rows.append({
                "id":    item["id"],
                "nom":   _prop_val(p.get("Nom")),
                "etat":  _prop_val(p.get("État")),
                "url":   f"https://www.notion.so/{item['id'].replace('-','')}",
            })
        return rows
    except Exception as e:
        print(f"[Notion Watchlist] Erreur: {e}")
        return []


# ---------------------------------------------------------------------------
# Routes - API Usage (Anthropic / OpenRouter / Google)
# ---------------------------------------------------------------------------

@app.get("/api/usage")
async def get_api_usage():
    import httpx
    out = {}

    # --- OpenRouter --------------------------------------------------------
    if OPENROUTER_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                r = await client.get(
                    "https://openrouter.ai/api/v1/credits",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                )
                if r.status_code == 200:
                    d = r.json().get("data", {})
                    total   = d.get("total_credits", 0)
                    used    = d.get("total_usage", 0)
                    out["openrouter"] = {
                        "connected":         True,
                        "total_credits":     total,
                        "total_usage":       used,
                        "remaining":         round(total - used, 4),
                        "remaining_credits": round(total - used, 4),
                        "pct_used":          round(used / total * 100, 1) if total else 0,
                    }
                else:
                    out["openrouter"] = {"connected": False, "error": r.status_code}
        except Exception as e:
            out["openrouter"] = {"connected": False, "error": str(e)}
    else:
        out["openrouter"] = {"connected": False, "missing_key": True}

    # --- Google Calendar OAuth status ------------------------------------
    out["google"] = {"connected": GOOGLE_TOKEN_PATH.exists()}

    # --- Notion -----------------------------------------------------------
    out["notion"] = {"connected": bool(NOTION_TOKEN)}

    # --- Gemini -----------------------------------------------------------
    if GEMINI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": GEMINI_API_KEY},
                )
                out["gemini"] = {"connected": r.status_code == 200}
        except Exception:
            out["gemini"] = {"connected": False, "error": "timeout"}
    else:
        out["gemini"] = {"connected": False, "missing_key": True}

    return out


# ---------------------------------------------------------------------------
# IBKR Flex Query helpers
# ---------------------------------------------------------------------------

async def _fetch_ibkr_trades() -> dict:
    """Fetch trades from IBKR Flex Query and upsert into DB. Returns summary."""
    import asyncio
    import xml.etree.ElementTree as ET
    import httpx

    if not IBKR_FLEX_TOKEN or not IBKR_FLEX_QUERY_ID:
        return {"error": "IBKR not configured"}

    send_url = (
        "https://gdcdyn.interactivebrokers.com/Universal/servlet/"
        f"FlexStatementService.SendRequest?t={IBKR_FLEX_TOKEN}&q={IBKR_FLEX_QUERY_ID}&v=3"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(send_url)

    print(f"[IBKR] SendRequest status={r.status_code} body[:300]={r.text[:300]!r}")
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        return {"error": f"XML parse error on SendRequest: {e}", "raw": r.text[:300]}

    status = root.findtext("Status")
    if status != "Success":
        err = root.findtext("ErrorMessage") or "Unknown error"
        return {"error": f"IBKR SendRequest failed: {err}"}

    ref_code = root.findtext("ReferenceCode")
    get_url_base = (
        root.findtext("Url")
        or "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
    )

    statement_content = None
    last_raw = ""
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(6):
            if attempt > 0:
                await asyncio.sleep(5)
            get_url = f"{get_url_base}?q={ref_code}&t={IBKR_FLEX_TOKEN}&v=3"
            r2 = await client.get(get_url)
            last_raw = r2.text[:300]
            # CSV response: IBKR Flex Query configured with CSV format
            if r2.text.lstrip().startswith('"'):
                print(f"[IBKR] Detected CSV format, attempt {attempt}")
                statement_content = r2.text
                break
            try:
                root2 = ET.fromstring(r2.text)
            except ET.ParseError as e:
                print(f"[IBKR] ParseError attempt {attempt}: {e} | content[:200]: {r2.text[:200]!r}")
                continue
            err_code = root2.findtext("ErrorCode")
            if err_code in ("1019", "1018"):  # statement generation in progress
                continue
            if root2.tag == "FlexStatementResponse" or err_code:
                err_msg = root2.findtext("ErrorMessage") or f"code={err_code}"
                return {"error": f"IBKR GetStatement error: {err_msg}"}
            statement_content = r2.text
            break

    if not statement_content:
        return {"error": "IBKR statement not ready after retries", "last_raw": last_raw}

    def safe_float(val: str) -> float:
        try:
            return float(val) if val else 0.0
        except (ValueError, TypeError):
            return 0.0

    trades = []

    # ---- CSV format --------------------------------------------------------
    if statement_content.lstrip().startswith('"'):
        import csv, io
        reader = csv.DictReader(io.StringIO(statement_content))
        for row in reader:
            # IBKR CSV uses "Buy/Sell" header with slash
            trade_id = row.get("TradeID") or row.get("TransactionID") or row.get("IBOrderID", "")
            if not trade_id:
                continue
            trade_date_raw = row.get("TradeDate", "")
            if len(trade_date_raw) == 8:
                trade_date = f"{trade_date_raw[:4]}-{trade_date_raw[4:6]}-{trade_date_raw[6:8]}"
            else:
                trade_date = trade_date_raw
            date_time_raw = row.get("DateTime", trade_date).replace(";", " ")
            trades.append({
                "trade_id":       trade_id,
                "order_id":       row.get("IBOrderID", row.get("OrderID", "")),
                "symbol":         row.get("Symbol", ""),
                "asset_category": row.get("AssetClass", row.get("AssetCategory", "")),
                "currency":       row.get("CurrencyPrimary", row.get("Currency", "USD")),
                "trade_date":     trade_date,
                "date_time":      date_time_raw or trade_date,
                "buy_sell":       row.get("Buy/Sell", row.get("BuySell", "")),
                "quantity":       safe_float(row.get("Quantity")),
                "price":          safe_float(row.get("TradePrice")),
                "proceeds":       safe_float(row.get("Proceeds")),
                "commission":     safe_float(row.get("IBCommission", row.get("Commission", ""))),
                "pnl":            safe_float(row.get("FifoPnlRealized", row.get("RealizedPnL", ""))),
            })

    # ---- XML format --------------------------------------------------------
    else:
        try:
            tree = ET.fromstring(statement_content)
        except ET.ParseError as e:
            return {"error": f"XML parse error on statement: {e}"}

        for trade_el in tree.iter("Trade"):
            if trade_el.get("levelOfDetail", "").upper() not in ("EXECUTION", "TRADE"):
                continue
            trade_id = trade_el.get("tradeID") or trade_el.get("transactionID")
            if not trade_id:
                continue
            trade_date_raw = trade_el.get("tradeDate", "")
            if len(trade_date_raw) == 8:
                trade_date = f"{trade_date_raw[:4]}-{trade_date_raw[4:6]}-{trade_date_raw[6:8]}"
            else:
                trade_date = trade_date_raw
            date_time_raw = trade_el.get("dateTime", "").replace(";", " ")
            trades.append({
                "trade_id":       trade_el.get("tradeID") or trade_el.get("transactionID"),
                "order_id":       trade_el.get("ibOrderID") or trade_el.get("orderID", ""),
                "symbol":         trade_el.get("symbol", ""),
                "asset_category": trade_el.get("assetCategory", ""),
                "currency":       trade_el.get("currency", "USD"),
                "trade_date":     trade_date,
                "date_time":      date_time_raw or trade_date,
                "buy_sell":       trade_el.get("buySell", ""),
                "quantity":       safe_float(trade_el.get("quantity")),
                "price":          safe_float(trade_el.get("tradePrice")),
                "proceeds":       safe_float(trade_el.get("proceeds")),
                "commission":     safe_float(trade_el.get("ibCommission")),
                "pnl":            safe_float(trade_el.get("fifoPnlRealized")),
            })

        # ---- Ending Value (EquitySummaryByReportDateInBase, last entry) ----
        summary_updates = {}
        equity_els = list(tree.iter("EquitySummaryByReportDateInBase"))
        if equity_els:
            last_eq = equity_els[-1]
            ending_val = last_eq.get("total") or last_eq.get("endingValue")
            if ending_val:
                summary_updates["ending_value"] = ending_val
                print(f"[IBKR] EndingValue={ending_val}")

        # ---- TWR (ChangeInNAV section) -------------------------------------
        for nav_el in tree.iter("ChangeInNAV"):
            twr = nav_el.get("twr")
            if twr:
                summary_updates["twr_ytd"] = twr
                print(f"[IBKR] TWR={twr}")
                break

        if summary_updates:
            conn_s = get_db()
            for k, v in summary_updates.items():
                conn_s.execute(
                    "INSERT OR REPLACE INTO ibkr_summary (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                    (k, v),
                )
            conn_s.commit()
            conn_s.close()

    conn = get_db()
    inserted = 0
    updated = 0
    for t in trades:
        existing = conn.execute(
            "SELECT id FROM ibkr_trades WHERE trade_id = ?", (t["trade_id"],)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE ibkr_trades SET order_id=?, symbol=?, asset_category=?, currency=?,
                   trade_date=?, date_time=?, buy_sell=?, quantity=?, price=?,
                   proceeds=?, commission=?, pnl=?, synced_at=datetime('now')
                   WHERE trade_id=?""",
                (t.get("order_id"), t["symbol"], t["asset_category"], t["currency"],
                 t["trade_date"], t["date_time"], t["buy_sell"],
                 t["quantity"], t["price"], t["proceeds"], t["commission"],
                 t["pnl"], t["trade_id"]),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO ibkr_trades
                   (trade_id, order_id, symbol, asset_category, currency, trade_date, date_time,
                    buy_sell, quantity, price, proceeds, commission, pnl)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (t["trade_id"], t.get("order_id"), t["symbol"], t["asset_category"], t["currency"],
                 t["trade_date"], t["date_time"], t["buy_sell"],
                 t["quantity"], t["price"], t["proceeds"], t["commission"], t["pnl"]),
            )
            inserted += 1
    conn.commit()
    conn.close()

    print(f"[IBKR] Sync done: {len(trades)} trades fetched, {inserted} inserted, {updated} updated")
    return {"fetched": len(trades), "inserted": inserted, "updated": updated}


# ---------------------------------------------------------------------------
# Routes - IBKR
# ---------------------------------------------------------------------------

@app.get("/api/ibkr/status")
def ibkr_status():
    conn = get_db()
    row = conn.execute("SELECT MAX(synced_at) as ts, COUNT(*) as cnt FROM ibkr_trades").fetchone()
    conn.close()
    return {
        "configured": bool(IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID),
        "last_sync":   row["ts"] if row else None,
        "total_trades": row["cnt"] if row else 0,
    }


@app.post("/api/ibkr/sync")
async def ibkr_sync():
    result = await _fetch_ibkr_trades()
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@app.get("/api/ibkr/perf")
def get_ibkr_perf():
    conn = get_db()
    year = date.today().year

    # YTD — all trades with realized PnL
    ytd_rows = conn.execute(
        "SELECT pnl, commission FROM ibkr_trades WHERE trade_date >= ?",
        (f"{year}-01-01",),
    ).fetchall()
    ytd_pnl   = sum(r["pnl"] for r in ytd_rows)
    ytd_comm  = sum(r["commission"] for r in ytd_rows)
    closed_ytd = [r for r in ytd_rows if r["pnl"] != 0]
    ytd_wins  = sum(1 for r in closed_ytd if r["pnl"] > 0)

    # Monthly grouping — all time, last 24 months
    monthly_rows = conn.execute(
        """SELECT substr(trade_date, 1, 7) as month,
                  SUM(pnl)        as pnl,
                  SUM(commission) as commission,
                  COUNT(*)        as trades,
                  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN pnl != 0 THEN 1 ELSE 0 END) as closed
           FROM ibkr_trades
           WHERE trade_date >= date('now', '-24 months')
           GROUP BY month
           ORDER BY month DESC""",
    ).fetchall()

    last_sync = conn.execute("SELECT MAX(synced_at) as ts FROM ibkr_trades").fetchone()
    summary_rows = conn.execute("SELECT key, value FROM ibkr_summary").fetchall()
    conn.close()

    summary = {r["key"]: r["value"] for r in summary_rows}
    ending_value = float(summary["ending_value"]) if summary.get("ending_value") else None
    twr_raw = summary.get("twr_ytd")
    twr_ytd = float(twr_raw) if twr_raw else None

    monthly = []
    for r in monthly_rows:
        pnl   = r["pnl"] or 0.0
        comm  = r["commission"] or 0.0
        closed = r["closed"] or 0
        wins  = r["wins"] or 0
        monthly.append({
            "month":     r["month"],
            "pnl":       round(pnl, 2),
            "pnl_net":   round(pnl + comm, 2),
            "commission": round(comm, 2),
            "trades":    r["trades"] or 0,
            "wins":      wins,
            "win_rate":  round(wins / closed * 100, 1) if closed else 0,
        })

    return {
        "ytd": {
            "pnl":        round(ytd_pnl, 2),
            "pnl_net":    round(ytd_pnl + ytd_comm, 2),
            "commission": round(ytd_comm, 2),
            "trades":     len(ytd_rows),
            "wins":       ytd_wins,
            "win_rate":   round(ytd_wins / len(closed_ytd) * 100, 1) if closed_ytd else 0,
            "twr":        round(twr_ytd * 100, 2) if twr_ytd is not None else None,
        },
        "portfolio": {
            "ending_value": round(ending_value, 2) if ending_value is not None else None,
            "twr_ytd_pct":  round(twr_ytd * 100, 2) if twr_ytd is not None else None,
        },
        "monthly":   monthly,
        "last_sync": last_sync["ts"] if last_sync else None,
    }


@app.get("/api/ibkr/trades")
def get_ibkr_trades(month: Optional[str] = None, limit: int = 100):
    """Return IBKR trades grouped by order_id, optionally filtered by month (YYYY-MM)."""
    conn = get_db()
    if month:
        rows = conn.execute(
            "SELECT * FROM ibkr_trades WHERE trade_date LIKE ? ORDER BY date_time ASC",
            (f"{month}%",),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ibkr_trades ORDER BY date_time ASC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()

    # Group fills by order_id; fall back to individual rows when order_id is absent
    from collections import defaultdict
    order_map: dict = {}
    ungrouped = []
    for r in rows:
        rd = dict(r)
        oid = rd.get("order_id")
        if oid:
            if oid not in order_map:
                order_map[oid] = []
            order_map[oid].append(rd)
        else:
            ungrouped.append(rd)

    result = []
    for oid, fills in order_map.items():
        total_qty  = sum(f["quantity"] or 0 for f in fills)
        total_proc = sum(f["proceeds"] or 0 for f in fills)
        total_comm = sum(f["commission"] or 0 for f in fills)
        total_pnl  = sum(f["pnl"] or 0 for f in fills)
        avg_price  = abs(total_proc / total_qty) if total_qty else fills[0]["price"]
        first = fills[0]
        result.append({
            "order_id":       oid,
            "symbol":         first["symbol"],
            "asset_category": first["asset_category"],
            "currency":       first["currency"],
            "trade_date":     first["trade_date"],
            "date_time":      first["date_time"],
            "buy_sell":       first["buy_sell"],
            "quantity":       round(total_qty, 4),
            "price":          round(avg_price, 4),
            "proceeds":       round(total_proc, 2),
            "commission":     round(total_comm, 2),
            "pnl":            round(total_pnl, 2),
            "fills":          len(fills),
        })
    result.extend(ungrouped)
    result.sort(key=lambda x: x.get("date_time", ""), reverse=True)
    return result


# ---------------------------------------------------------------------------
# Scheduled IBKR sync (daily at 21:05 UTC)
# ---------------------------------------------------------------------------

async def _schedule_ibkr_sync():
    """Background task: sync IBKR trades every day at 21:05 UTC."""
    import asyncio
    while True:
        now_utc = datetime.utcnow()
        target = now_utc.replace(hour=21, minute=5, second=0, microsecond=0)
        if now_utc >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now_utc).total_seconds())
        try:
            await _fetch_ibkr_trades()
        except Exception as e:
            print(f"[IBKR] Erreur sync planifié: {e}")


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

async def _telegram_send(text: str) -> bool:
    """Send a message to the configured Telegram chat. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    import httpx
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            })
            return r.status_code == 200
    except Exception as e:
        print(f"[Telegram] Erreur: {e}")
        return False


def _build_digest(conn) -> str:
    """Build the daily digest message from the DB."""
    today = date.today().isoformat()
    now_dt = datetime.now().strftime("%H:%M")

    overdue = conn.execute(
        "SELECT title, due_date FROM tasks WHERE due_date < ? AND status != 'done' ORDER BY due_date ASC",
        (today,)
    ).fetchall()
    today_tasks = conn.execute(
        "SELECT title, status FROM tasks WHERE due_date = ? AND status != 'done'",
        (today,)
    ).fetchall()
    done_today = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE due_date = ? AND status = 'done'", (today,)
    ).fetchone()["cnt"]
    events = conn.execute(
        "SELECT title, start_datetime FROM events WHERE start_datetime LIKE ? ORDER BY start_datetime ASC",
        (f"{today}%",)
    ).fetchall()

    lines = [f"<b>📋 Dashboard · {today} {now_dt}</b>"]

    if overdue:
        lines.append(f"\n⚠️ <b>En retard ({len(overdue)})</b>")
        for t in overdue:
            lines.append(f"  · {t['title']} <i>({t['due_date']})</i>")

    if today_tasks:
        lines.append(f"\n📌 <b>À faire aujourd'hui ({len(today_tasks)})</b>")
        for t in today_tasks:
            lines.append(f"  · {t['title']}")

    if done_today:
        lines.append(f"\n✅ <b>{done_today} tâche(s) terminée(s)</b>")

    if events:
        lines.append(f"\n📅 <b>Événements ({len(events)})</b>")
        for e in events:
            hm = e['start_datetime'][11:16] if len(e['start_datetime']) > 10 else ""
            lines.append(f"  · {hm} {e['title']}")

    if not overdue and not today_tasks and not events:
        lines.append("\n✨ Rien de prévu — journée libre !")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routes - Telegram
# ---------------------------------------------------------------------------

class TelegramMessage(BaseModel):
    text: Optional[str] = None


@app.get("/api/telegram/status")
def telegram_status():
    return {"connected": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)}


@app.post("/api/telegram/send")
async def telegram_send(msg: TelegramMessage):
    """Send a custom text or the daily digest if no text provided."""
    conn = get_db()
    text = msg.text if msg.text else _build_digest(conn)
    conn.close()
    ok = await _telegram_send(text)
    if not ok:
        raise HTTPException(status_code=503, detail="Telegram non configuré ou erreur d'envoi")
    return {"sent": True}


@app.post("/api/telegram/digest")
async def telegram_digest():
    """Send the full daily digest to Telegram."""
    conn = get_db()
    text = _build_digest(conn)
    conn.close()
    ok = await _telegram_send(text)
    if not ok:
        raise HTTPException(status_code=503, detail="Telegram non configuré ou erreur d'envoi")
    return {"sent": True}


# ---------------------------------------------------------------------------
# Scheduled digest (daily at configured hour)
# ---------------------------------------------------------------------------

DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "8"))  # default 08:00


async def _schedule_daily_digest():
    """Background task: send digest every day at DIGEST_HOUR."""
    import asyncio
    while True:
        now = datetime.now()
        target = now.replace(hour=DIGEST_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target.replace(day=target.day + 1)
        wait_secs = (target - now).total_seconds()
        await asyncio.sleep(wait_secs)
        conn = get_db()
        text = _build_digest(conn)
        conn.close()
        await _telegram_send(text)
