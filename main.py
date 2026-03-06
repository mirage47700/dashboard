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
NOTION_TOKEN         = os.getenv("NOTION_TOKEN", "")
OPENROUTER_API_KEY   = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")

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

        CREATE TABLE IF NOT EXISTS trading_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            year INTEGER,
            source TEXT DEFAULT 'tradingcalendar.com',
            scraped_at TEXT DEFAULT (datetime('now')),
            UNIQUE(date, event_type, title)
        );

        CREATE TABLE IF NOT EXISTS economic_releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator TEXT NOT NULL,
            release_date TEXT NOT NULL,
            estimate TEXT DEFAULT '',
            actual TEXT DEFAULT '',
            revision TEXT DEFAULT '',
            source TEXT DEFAULT 'tradingcalendar.com',
            scraped_at TEXT DEFAULT (datetime('now')),
            UNIQUE(indicator, release_date)
        );

        CREATE TABLE IF NOT EXISTS stock_splits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ex_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            ratio TEXT NOT NULL,
            float_new TEXT DEFAULT '',
            float_old TEXT DEFAULT '',
            source TEXT DEFAULT 'tradingcalendar.com',
            scraped_at TEXT DEFAULT (datetime('now')),
            UNIQUE(ex_date, symbol)
        );
    """)
    conn.commit()
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
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="VPS Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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
    return templates.TemplateResponse("index.html", {"request": request})


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
    from datetime import timezone
    creds = Credentials(
        token=data["token"],
        refresh_token=data.get("refresh_token"),
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data.get("scopes"),
    )
    if data.get("expiry"):
        creds.expiry = datetime.fromisoformat(data["expiry"]).replace(tzinfo=timezone.utc)
    return creds


def _persist_credentials(creds):
    existing = json.loads(GOOGLE_TOKEN_PATH.read_text()) if GOOGLE_TOKEN_PATH.exists() else {}
    existing["token"] = creds.token
    existing["expiry"] = creds.expiry.isoformat() if creds.expiry else None
    if creds.refresh_token:
        existing["refresh_token"] = creds.refresh_token
    GOOGLE_TOKEN_PATH.write_text(json.dumps(existing, indent=2))


# ---------------------------------------------------------------------------
# Routes - Google OAuth2
# ---------------------------------------------------------------------------

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


@app.get("/auth/google")
async def auth_google(request: Request):
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(_google_client_config(), scopes=GOOGLE_SCOPES)
    flow.redirect_uri = str(request.url_for("auth_google_callback"))
    # access_type=offline ensures we get a refresh_token; prompt=consent forces it even if already granted
    authorization_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return RedirectResponse(authorization_url)


@app.get("/auth/google/callback", name="auth_google_callback")
async def auth_google_callback(request: Request, code: str, state: str = ""):
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(_google_client_config(), scopes=GOOGLE_SCOPES)
    flow.redirect_uri = str(request.url_for("auth_google_callback"))
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
    return RedirectResponse("/")


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
                        "connected":      True,
                        "total_credits":  total,
                        "total_usage":    used,
                        "remaining":      round(total - used, 4),
                        "pct_used":       round(used / total * 100, 1) if total else 0,
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


# ---------------------------------------------------------------------------
# Trading Calendar — Scraper (tradingcalendar.com)
# ---------------------------------------------------------------------------

_MONTHS = {
    'January': 1, 'February': 2, 'March': 3, 'April': 4,
    'May': 5, 'June': 6, 'July': 7, 'August': 8,
    'September': 9, 'October': 10, 'November': 11, 'December': 12,
}

# Standard economic release times (Eastern Time, 24h)
_RELEASE_TIMES_ET = {
    'CPI':   '08:30',
    'NFP':   '08:30',
    'PCE':   '08:30',
    'JOLTS': '10:00',
}

_CALENDAR_EVENT_TIMES_ET = {
    'market_holiday': None,      # all-day
    'options_expiry': '09:30',   # market open
    'index_rebalance': '16:00',  # market close
}


def _get_playwright_proxy():
    proxy_url = os.getenv('http_proxy') or os.getenv('HTTP_PROXY') or ''
    import re as _re
    m = _re.match(r'http://([^:]+):([^@]+)@([^:]+):(\d+)', proxy_url)
    if m:
        user, pwd, host, port = m.groups()
        return {'server': f'http://{host}:{port}', 'username': user, 'password': pwd}
    return None


def _parse_market_calendar(text: str) -> list:
    month_re = r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    results = []
    year = None
    section = None
    rebalance_index = 'General'

    for raw_line in text.split('\n'):
        line = raw_line.strip().replace('\u200b', '').replace('\xa0', '')
        if not line:
            continue
        if 'Tracking tradable events' in line or '©' in line or 'contact@' in line:
            break

        if 'Holiday Calendar' in line:
            section = 'holiday'
            m = re.search(r'(\d{4})', line)
            if m:
                year = int(m.group(1))
            continue
        if 'Options Expiration Calendar' in line:
            section = 'options'
            m = re.search(r'(\d{4})', line)
            if m:
                year = int(m.group(1))
            continue
        if 'Stock Index Rebalance Calendar' in line:
            section = 'rebalance'
            rebalance_index = 'General'
            continue

        if section == 'rebalance' and any(x in line for x in ['S&P', 'Russell', 'Nasdaq', 'MSCI']):
            rebalance_index = line.strip()
            continue

        if not year:
            continue

        if section == 'holiday':
            m = re.match(rf'{month_re}\s+(\d+)\s*[-–]\s*(.+)', line)
            if m:
                month_name, day, status = m.groups()
                date_str = f'{year}-{_MONTHS[month_name]:02d}-{int(day):02d}'
                results.append({
                    'date': date_str, 'event_type': 'market_holiday',
                    'title': status.strip(), 'description': '', 'year': year,
                })

        elif section == 'options':
            m = re.match(rf'{month_re}\s+(\d+)\s*(?:[-–]\s*(.+))?$', line)
            if m:
                month_name, day, note = m.group(1), m.group(2), m.group(3)
                date_str = f'{year}-{_MONTHS[month_name]:02d}-{int(day):02d}'
                title = 'Options Expiration' + (f' - {note.strip()}' if note else '')
                results.append({
                    'date': date_str, 'event_type': 'options_expiry',
                    'title': title, 'description': note.strip() if note else '', 'year': year,
                })

        elif section == 'rebalance':
            m = re.match(rf'{month_re}\s+(\d+)\s*(.*)', line)
            if m:
                month_name, day, note = m.groups()
                date_str = f'{year}-{_MONTHS[month_name]:02d}-{int(day):02d}'
                title = f'{rebalance_index} Rebalance'
                if note.strip():
                    title += f' {note.strip()}'
                results.append({
                    'date': date_str, 'event_type': 'index_rebalance',
                    'title': title, 'description': rebalance_index, 'year': year,
                })

    return results


def _parse_economic_table(text: str, indicator: str) -> list:
    date_re = re.compile(r'^\d{2}/\d{2}/\d{4}$')
    lines = text.split('\n')

    start = 0
    for i, l in enumerate(lines):
        if l.strip() == 'Revision':
            start = i + 1
            break

    results = []
    current = None

    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or stripped == '\t':
            continue
        if 'Tracking tradable events' in stripped or '©' in stripped:
            break

        if date_re.match(stripped):
            if current is not None:
                results.append(current)
            dt = datetime.strptime(stripped, '%m/%d/%Y')
            current = {
                'indicator': indicator,
                'release_date': dt.strftime('%Y-%m-%d'),
                'estimate': '', 'actual': '', 'revision': '',
                '_vals': [],
            }
        elif current is not None and stripped not in ('N/A',) or (current is not None and stripped == 'N/A'):
            if current is not None:
                current['_vals'].append(stripped)

    if current is not None:
        results.append(current)

    for r in results:
        vals = r.pop('_vals')
        r['estimate'] = vals[0] if len(vals) > 0 else ''
        r['actual'] = vals[1] if len(vals) > 1 else ''
        r['revision'] = vals[2] if len(vals) > 2 else ''

    return results


def _parse_stock_splits(text: str) -> list:
    date_re = re.compile(r'^\d{2}/\d{2}/\d{4}$')
    lines = text.split('\n')

    start = 0
    for i, l in enumerate(lines):
        if 'Float (Old)' in l.strip():
            start = i + 1
            break

    results = []
    current = None

    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or stripped == '\t':
            continue
        if 'Tracking tradable events' in stripped or '©' in stripped:
            break

        if date_re.match(stripped):
            if current is not None:
                results.append(current)
            dt = datetime.strptime(stripped, '%m/%d/%Y')
            current = {
                'ex_date': dt.strftime('%Y-%m-%d'),
                'symbol': '', 'ratio': '', 'float_new': '', 'float_old': '',
                '_vals': [],
            }
        elif current is not None and len(current['_vals']) < 4:
            current['_vals'].append(stripped)

    if current is not None:
        results.append(current)

    for r in results:
        vals = r.pop('_vals')
        r['symbol'] = vals[0] if len(vals) > 0 else ''
        r['ratio'] = vals[1] if len(vals) > 1 else ''
        r['float_new'] = vals[2] if len(vals) > 2 else ''
        r['float_old'] = vals[3] if len(vals) > 3 else ''

    return [r for r in results if r['symbol'] and r['ratio']]


def _do_scrape() -> dict:
    """Blocking scrape — run in thread pool."""
    from playwright.sync_api import sync_playwright

    proxy = _get_playwright_proxy()
    pages_to_fetch = {
        'market_calendar': '/market-calendar',
        'CPI': '/cpi',
        'NFP': '/nfp',
        'PCE': '/pce',
        'JOLTS': '/jolts',
        'stock_splits': '/stock-split-calendar',
    }
    raw_texts = {}

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True, proxy=proxy)
        ctx = browser.new_context(ignore_https_errors=True)
        for key, path in pages_to_fetch.items():
            page = ctx.new_page()
            try:
                page.goto(f'https://www.tradingcalendar.com{path}', timeout=60000)
                page.wait_for_load_state('networkidle', timeout=60000)
                raw_texts[key] = page.inner_text('body')
            except Exception as e:
                print(f'[TradingCalendar] Error scraping {path}: {e}')
                raw_texts[key] = ''
            finally:
                page.close()
        browser.close()

    results = {
        'market_calendar': _parse_market_calendar(raw_texts.get('market_calendar', '')),
        'stock_splits': _parse_stock_splits(raw_texts.get('stock_splits', '')),
    }
    for ind in ('CPI', 'NFP', 'PCE', 'JOLTS'):
        results[ind] = _parse_economic_table(raw_texts.get(ind, ''), ind)

    return results


def _save_scrape_results(results: dict):
    conn = get_db()
    for item in results.get('market_calendar', []):
        conn.execute(
            "INSERT OR REPLACE INTO trading_calendar (date, event_type, title, description, year)"
            " VALUES (?, ?, ?, ?, ?)",
            (item['date'], item['event_type'], item['title'], item['description'], item['year']),
        )
    for ind in ('CPI', 'NFP', 'PCE', 'JOLTS'):
        for item in results.get(ind, []):
            conn.execute(
                "INSERT OR REPLACE INTO economic_releases (indicator, release_date, estimate, actual, revision)"
                " VALUES (?, ?, ?, ?, ?)",
                (item['indicator'], item['release_date'], item['estimate'], item['actual'], item['revision']),
            )
    for item in results.get('stock_splits', []):
        conn.execute(
            "INSERT OR REPLACE INTO stock_splits (ex_date, symbol, ratio, float_new, float_old)"
            " VALUES (?, ?, ?, ?, ?)",
            (item['ex_date'], item['symbol'], item['ratio'], item['float_new'], item['float_old']),
        )
    conn.commit()
    conn.close()


from concurrent.futures import ThreadPoolExecutor as _TPE
_scraper_executor = _TPE(max_workers=1)


async def _run_scrape() -> dict:
    import asyncio
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(_scraper_executor, _do_scrape)
    _save_scrape_results(results)
    return results


# ---------------------------------------------------------------------------
# Routes - Trading Calendar
# ---------------------------------------------------------------------------

@app.post("/api/trading-calendar/refresh")
async def refresh_trading_calendar():
    """Trigger a full re-scrape of tradingcalendar.com."""
    results = await _run_scrape()
    return {"status": "ok", "scraped": {k: len(v) for k, v in results.items()}}


@app.get("/api/trading-calendar/market")
def get_market_calendar(event_type: Optional[str] = None, year: Optional[int] = None):
    conn = get_db()
    query = "SELECT * FROM trading_calendar WHERE 1=1"
    params: list = []
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    if year:
        query += " AND year = ?"
        params.append(year)
    query += " ORDER BY date ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/trading-calendar/economic")
def get_economic_releases(indicator: Optional[str] = None, limit: int = 50):
    conn = get_db()
    query = "SELECT * FROM economic_releases WHERE 1=1"
    params: list = []
    if indicator:
        query += " AND indicator = ?"
        params.append(indicator)
    query += " ORDER BY release_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/trading-calendar/splits")
def get_stock_splits_endpoint(upcoming_only: bool = False, limit: int = 50):
    conn = get_db()
    today = date.today().isoformat()
    query = "SELECT * FROM stock_splits WHERE 1=1"
    params: list = []
    if upcoming_only:
        query += " AND ex_date >= ?"
        params.append(today)
    query += " ORDER BY ex_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/trading-calendar/today")
def get_today_trading_events():
    """Return all tradingcalendar.com events for today with their standard release times (ET)."""
    today = date.today().isoformat()
    conn = get_db()

    events = []

    # Market calendar events (holidays, options expiry, index rebalancing)
    rows = conn.execute(
        "SELECT * FROM trading_calendar WHERE date = ? ORDER BY event_type", (today,)
    ).fetchall()
    for r in rows:
        r = dict(r)
        time_et = _CALENDAR_EVENT_TIMES_ET.get(r['event_type'])
        events.append({
            "source": "tradingcalendar.com",
            "type": r['event_type'],
            "title": r['title'],
            "description": r['description'],
            "date": today,
            "time_et": time_et,
            "all_day": time_et is None,
        })

    # Economic releases today
    rows = conn.execute(
        "SELECT * FROM economic_releases WHERE release_date = ? ORDER BY indicator", (today,)
    ).fetchall()
    for r in rows:
        r = dict(r)
        time_et = _RELEASE_TIMES_ET.get(r['indicator'], '08:30')
        desc_parts = []
        if r['estimate']:
            desc_parts.append(f"Estimate: {r['estimate']}")
        if r['actual']:
            desc_parts.append(f"Actual: {r['actual']}")
        if r['revision']:
            desc_parts.append(f"Revision: {r['revision']}")
        events.append({
            "source": "tradingcalendar.com",
            "type": "economic_release",
            "title": r['indicator'],
            "description": " | ".join(desc_parts),
            "date": today,
            "time_et": time_et,
            "all_day": False,
        })

    # Stock splits today
    rows = conn.execute(
        "SELECT * FROM stock_splits WHERE ex_date = ? ORDER BY symbol", (today,)
    ).fetchall()
    for r in rows:
        r = dict(r)
        events.append({
            "source": "tradingcalendar.com",
            "type": "stock_split",
            "title": f"{r['symbol']} Stock Split {r['ratio']}",
            "description": f"Float (New): {r['float_new']} | Float (Old): {r['float_old']}",
            "date": today,
            "time_et": "09:30",
            "all_day": False,
        })

    conn.close()
    return {"date": today, "count": len(events), "events": events}


@app.post("/api/trading-calendar/sync-gcal")
async def sync_trading_calendar_to_gcal(target_date: Optional[str] = None):
    """
    Add today's (or target_date's) tradingcalendar.com events to Google Calendar.
    Requires Google OAuth with calendar.events scope.
    """
    creds = get_google_credentials()
    if creds is None:
        raise HTTPException(status_code=401, detail="Google Calendar non connecté. Visitez /auth/google")

    day = target_date or date.today().isoformat()
    conn = get_db()

    # Collect all events for the day (same logic as /today but parameterized)
    tc_events: list = []

    rows = conn.execute(
        "SELECT * FROM trading_calendar WHERE date = ?", (day,)
    ).fetchall()
    for r in rows:
        r = dict(r)
        time_et = _CALENDAR_EVENT_TIMES_ET.get(r['event_type'])
        tc_events.append({
            "title": r['title'],
            "description": f"[{r['event_type']}] source: tradingcalendar.com",
            "date": day,
            "time_et": time_et,
            "all_day": time_et is None,
        })

    rows = conn.execute(
        "SELECT * FROM economic_releases WHERE release_date = ?", (day,)
    ).fetchall()
    for r in rows:
        r = dict(r)
        time_et = _RELEASE_TIMES_ET.get(r['indicator'], '08:30')
        parts = []
        if r['estimate']:
            parts.append(f"Estimate: {r['estimate']}")
        if r['actual']:
            parts.append(f"Actual: {r['actual']}")
        if r['revision']:
            parts.append(f"Revision: {r['revision']}")
        tc_events.append({
            "title": f"{r['indicator']} Release",
            "description": " | ".join(parts) + "\nsource: tradingcalendar.com",
            "date": day,
            "time_et": time_et,
            "all_day": False,
        })

    rows = conn.execute(
        "SELECT * FROM stock_splits WHERE ex_date = ?", (day,)
    ).fetchall()
    for r in rows:
        r = dict(r)
        tc_events.append({
            "title": f"{r['symbol']} Stock Split {r['ratio']}",
            "description": f"Float (New): {r['float_new']} | Float (Old): {r['float_old']}\nsource: tradingcalendar.com",
            "date": day,
            "time_et": "09:30",
            "all_day": False,
        })

    conn.close()

    if not tc_events:
        return {"synced": 0, "message": "Aucun événement tradingcalendar.com pour ce jour"}

    # Create events via Google Calendar API
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from googleapiclient.discovery import build

        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            _persist_credentials(creds)

        service = build("calendar", "v3", credentials=creds)
        created = []

        for ev in tc_events:
            if ev["all_day"]:
                body = {
                    "summary": ev["title"],
                    "description": ev.get("description", ""),
                    "start": {"date": ev["date"]},
                    "end": {"date": ev["date"]},
                }
            else:
                dt_start = f"{ev['date']}T{ev['time_et']}:00"
                # Add 1 hour for end time
                h, mi = map(int, ev['time_et'].split(':'))
                end_h = (h + 1) % 24
                dt_end = f"{ev['date']}T{end_h:02d}:{mi:02d}:00"
                body = {
                    "summary": ev["title"],
                    "description": ev.get("description", ""),
                    "start": {"dateTime": dt_start, "timeZone": "America/New_York"},
                    "end": {"dateTime": dt_end, "timeZone": "America/New_York"},
                }

            result = service.events().insert(calendarId="primary", body=body).execute()
            created.append({"title": ev["title"], "id": result.get("id"), "link": result.get("htmlLink")})

        return {"synced": len(created), "date": day, "events": created}

    except Exception as e:
        print(f"[GCal Sync] Erreur: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur Google Calendar: {e}")
