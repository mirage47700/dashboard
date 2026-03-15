from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3, os, subprocess, json, asyncio, re, requests as _requests
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

MEMORIES_PATH  = os.getenv("MEMORIES_PATH", "/root/memories.md")
OBSIDIAN_VAULT = os.getenv("OBSIDIAN_VAULT", "")

from twilio_voice import router as twilio_router
DB_PATH        = Path(__file__).parent / "mission_control.db"
OPENCLAW_URL   = os.getenv("OPENCLAW_URL", "http://localhost:8000")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "")

sse_clients: list[asyncio.Queue] = []

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
def get_db():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = get_db()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'todo',
            project_id INTEGER,
            assigned_to TEXT DEFAULT 'openclaw',
            priority TEXT DEFAULT 'medium',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            progress INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            category TEXT DEFAULT 'general',
            project_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS team_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT DEFAULT '',
            agent_type TEXT DEFAULT 'sub',
            parent_id INTEGER,
            mission TEXT DEFAULT '',
            status TEXT DEFAULT 'idle',
            current_task TEXT,
            emoji TEXT DEFAULT '?',
            color TEXT DEFAULT '#4f8ef7'
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT DEFAULT '{}',
            timestamp TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            status TEXT DEFAULT 'alive',
            metadata TEXT DEFAULT '{}',
            timestamp TEXT DEFAULT (datetime('now'))
        );
    """)
    # Migration : ajout des colonnes openclaw_id et source si elles n'existent pas
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(team_members)").fetchall()}
    if "openclaw_id" not in existing_cols:
        c.execute("ALTER TABLE team_members ADD COLUMN openclaw_id TEXT")
    if "source" not in existing_cols:
        c.execute("ALTER TABLE team_members ADD COLUMN source TEXT DEFAULT 'manual'")

    if not c.execute("SELECT id FROM team_members").fetchone():
        c.execute("""INSERT INTO team_members (name,role,agent_type,mission,emoji,color,status,source)
            VALUES ('OpenClaw','Main Agent','main',
                'Orchestrate tasks, manage projects, maintain memories, coordinate sub-agents.',
                'C','#4f8ef7','idle','manual')""")
    c.commit()
    c.close()

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)
BASE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.include_router(twilio_router)

# ---------------------------------------------------------------------------
# SSE broadcast
# ---------------------------------------------------------------------------
async def broadcast(event: dict):
    for q in list(sse_clients):
        await q.put(event)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class TaskIn(BaseModel):
    title: str
    description: str = ""
    status: str = "todo"
    project_id: Optional[int] = None
    assigned_to: str = "openclaw"
    priority: str = "medium"

class ProjectIn(BaseModel):
    name: str
    description: str = ""
    status: str = "active"
    progress: int = 0

class DocIn(BaseModel):
    title: str
    content: str = ""
    category: str = "general"
    project_id: Optional[int] = None

class TeamMemberIn(BaseModel):
    name: str
    role: str = ""
    agent_type: str = "sub"
    parent_id: Optional[int] = None
    mission: str = ""
    status: str = "idle"
    current_task: Optional[str] = None
    emoji: str = "?"
    color: str = "#4f8ef7"

class ActivityIn(BaseModel):
    agent: str
    action: str
    details: dict = {}

class HeartbeatIn(BaseModel):
    agent: str
    status: str = "alive"
    metadata: dict = {}

class StatusPatch(BaseModel):
    status: str

# ---------------------------------------------------------------------------
# Routes — index
# ---------------------------------------------------------------------------
@app.get("/")
def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
@app.get("/api/tasks")
def list_tasks(status: Optional[str] = None, assigned_to: Optional[str] = None,
               project_id: Optional[int] = None):
    c = get_db()
    q = "SELECT t.*, p.name as project_name FROM tasks t LEFT JOIN projects p ON t.project_id=p.id WHERE 1=1"
    params = []
    if status:      q += " AND t.status=?";      params.append(status)
    if assigned_to: q += " AND t.assigned_to=?"; params.append(assigned_to)
    if project_id:  q += " AND t.project_id=?";  params.append(project_id)
    q += " ORDER BY CASE t.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, t.created_at DESC"
    rows = c.execute(q, params).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.post("/api/tasks", status_code=201)
async def create_task(t: TaskIn):
    c = get_db()
    cur = c.execute(
        "INSERT INTO tasks (title,description,status,project_id,assigned_to,priority) VALUES (?,?,?,?,?,?)",
        (t.title, t.description, t.status, t.project_id, t.assigned_to, t.priority))
    c.commit()
    row = dict(c.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone())
    c.close()
    await broadcast({"type": "task_created", "task": row})
    return row

@app.put("/api/tasks/{tid}")
async def update_task(tid: int, t: TaskIn):
    c = get_db()
    completed = datetime.now().isoformat() if t.status == "done" else None
    c.execute(
        "UPDATE tasks SET title=?,description=?,status=?,project_id=?,assigned_to=?,priority=?,updated_at=datetime('now'),completed_at=? WHERE id=?",
        (t.title, t.description, t.status, t.project_id, t.assigned_to, t.priority, completed, tid))
    c.commit()
    row = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404)
    await broadcast({"type": "task_updated", "task": dict(row)})
    return dict(row)

@app.patch("/api/tasks/{tid}/status")
async def patch_task_status(tid: int, body: StatusPatch):
    c = get_db()
    completed = datetime.now().isoformat() if body.status == "done" else None
    c.execute("UPDATE tasks SET status=?,updated_at=datetime('now'),completed_at=? WHERE id=?",
              (body.status, completed, tid))
    c.commit()
    row = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404)
    await broadcast({"type": "task_updated", "task": dict(row)})
    return dict(row)

@app.delete("/api/tasks/{tid}")
async def delete_task(tid: int):
    c = get_db()
    c.execute("DELETE FROM tasks WHERE id=?", (tid,))
    c.commit()
    c.close()
    await broadcast({"type": "task_deleted", "id": tid})
    return {"ok": True}

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@app.get("/api/projects")
def list_projects():
    c = get_db()
    rows = c.execute("""
        SELECT p.*, COUNT(t.id) as task_count,
               SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) as done_count
        FROM projects p LEFT JOIN tasks t ON t.project_id=p.id
        GROUP BY p.id ORDER BY p.created_at DESC
    """).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.post("/api/projects", status_code=201)
def create_project(p: ProjectIn):
    c = get_db()
    cur = c.execute("INSERT INTO projects (name,description,status,progress) VALUES (?,?,?,?)",
                    (p.name, p.description, p.status, p.progress))
    c.commit()
    row = dict(c.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone())
    c.close()
    return row

@app.put("/api/projects/{pid}")
def update_project(pid: int, p: ProjectIn):
    c = get_db()
    c.execute("UPDATE projects SET name=?,description=?,status=?,progress=?,updated_at=datetime('now') WHERE id=?",
              (p.name, p.description, p.status, p.progress, pid))
    c.commit()
    row = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404)
    return dict(row)

@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    c = get_db()
    c.execute("DELETE FROM projects WHERE id=?", (pid,))
    c.commit()
    c.close()
    return {"ok": True}

# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------
@app.get("/api/docs")
def list_docs(category: Optional[str] = None, q: Optional[str] = None):
    c = get_db()
    query = "SELECT * FROM docs WHERE 1=1"
    params = []
    if category:
        query += " AND category=?"; params.append(category)
    if q:
        query += " AND (title LIKE ? OR content LIKE ?)"; params.extend([f"%{q}%", f"%{q}%"])
    query += " ORDER BY updated_at DESC"
    rows = c.execute(query, params).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.post("/api/docs", status_code=201)
def create_doc(d: DocIn):
    c = get_db()
    cur = c.execute("INSERT INTO docs (title,content,category,project_id) VALUES (?,?,?,?)",
                    (d.title, d.content, d.category, d.project_id))
    c.commit()
    row = dict(c.execute("SELECT * FROM docs WHERE id=?", (cur.lastrowid,)).fetchone())
    c.close()
    return row

@app.put("/api/docs/{did}")
def update_doc(did: int, d: DocIn):
    c = get_db()
    c.execute("UPDATE docs SET title=?,content=?,category=?,project_id=?,updated_at=datetime('now') WHERE id=?",
              (d.title, d.content, d.category, d.project_id, did))
    c.commit()
    row = c.execute("SELECT * FROM docs WHERE id=?", (did,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404)
    return dict(row)

@app.delete("/api/docs/{did}")
def delete_doc(did: int):
    c = get_db()
    c.execute("DELETE FROM docs WHERE id=?", (did,))
    c.commit()
    c.close()
    return {"ok": True}

# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------
@app.get("/api/team")
def list_team():
    c = get_db()
    rows = c.execute("SELECT * FROM team_members ORDER BY (agent_type='main') DESC, id").fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.post("/api/team", status_code=201)
def create_member(m: TeamMemberIn):
    c = get_db()
    cur = c.execute(
        "INSERT INTO team_members (name,role,agent_type,parent_id,mission,status,current_task,emoji,color) VALUES (?,?,?,?,?,?,?,?,?)",
        (m.name, m.role, m.agent_type, m.parent_id, m.mission, m.status, m.current_task, m.emoji, m.color))
    c.commit()
    row = dict(c.execute("SELECT * FROM team_members WHERE id=?", (cur.lastrowid,)).fetchone())
    c.close()
    return row

@app.put("/api/team/{mid}")
def update_member(mid: int, m: TeamMemberIn):
    c = get_db()
    c.execute(
        "UPDATE team_members SET name=?,role=?,agent_type=?,parent_id=?,mission=?,status=?,current_task=?,emoji=?,color=? WHERE id=?",
        (m.name, m.role, m.agent_type, m.parent_id, m.mission, m.status, m.current_task, m.emoji, m.color, mid))
    c.commit()
    row = c.execute("SELECT * FROM team_members WHERE id=?", (mid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404)
    return dict(row)

@app.delete("/api/team/{mid}")
def delete_member(mid: int):
    c = get_db()
    c.execute("DELETE FROM team_members WHERE id=?", (mid,))
    c.commit()
    c.close()
    return {"ok": True}

# ---------------------------------------------------------------------------
# Sync OpenClaw agents → team_members
# ---------------------------------------------------------------------------

def _oc_status_to_team(status: str) -> str:
    """Convertit le statut OpenClaw en statut team_members."""
    s = (status or "").lower()
    if s in ("running", "active", "working", "started", "online"):
        return "working"
    if s in ("idle", "paused", "waiting"):
        return "idle"
    return "offline"

def _oc_agent_emoji(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("trade", "market", "stock", "ibkr")): return "📈"
    if any(k in n for k in ("mail", "email", "gmail")):            return "✉️"
    if any(k in n for k in ("scrape", "web", "crawl")):            return "🕷️"
    if any(k in n for k in ("task", "todo", "board")):             return "📋"
    if any(k in n for k in ("data", "db", "base")):                return "🗄️"
    if any(k in n for k in ("main", "master", "orche")):           return "🎛️"
    if any(k in n for k in ("news", "rss", "feed")):               return "📰"
    return "🤖"

_OC_COLORS = ["#6366f1","#22c55e","#f59e0b","#3b82f6","#a855f7","#ec4899","#14b8a6","#f97316"]

@app.post("/api/openclaw/sync-team")
def openclaw_sync_team():
    """Récupère les agents OpenClaw et les upsert dans team_members."""
    # 1. Récupère les agents depuis OpenClaw
    agents = []
    candidates = ["/api/agents", "/agents", "/v1/agents", "/api/sessions", "/sessions"]
    for path in candidates:
        try:
            r = _requests.get(f"{OPENCLAW_URL}{path}", headers=_oc_headers(), timeout=5)
            if r.ok:
                data = r.json()
                if isinstance(data, list):
                    agents = data; break
                if isinstance(data, dict):
                    for key in ("agents", "sessions", "items", "data", "results"):
                        if key in data and isinstance(data[key], list):
                            agents = data[key]; break
                    if agents: break
        except Exception:
            pass

    if not agents:
        raise HTTPException(502, detail="Impossible de récupérer les agents depuis OpenClaw.")

    c = get_db()
    added = updated = 0

    for i, a in enumerate(agents):
        oc_id   = str(a.get("id") or a.get("agent_id") or a.get("session_id") or a.get("name") or f"oc-{i}")
        name    = a.get("name") or a.get("title") or a.get("agent_name") or oc_id
        role    = a.get("role") or a.get("type") or a.get("model") or "OpenClaw Agent"
        status  = _oc_status_to_team(a.get("status") or a.get("state") or "idle")
        task    = a.get("current_task") or a.get("task") or None
        mission = a.get("description") or a.get("mission") or a.get("system_prompt", "")[:200] or ""
        emoji   = a.get("emoji") or _oc_agent_emoji(name)
        color   = _OC_COLORS[i % len(_OC_COLORS)]

        existing = c.execute("SELECT id FROM team_members WHERE openclaw_id=?", (oc_id,)).fetchone()
        if existing:
            c.execute(
                "UPDATE team_members SET name=?,role=?,status=?,current_task=?,mission=?,source='openclaw' WHERE openclaw_id=?",
                (name, role, status, task, mission, oc_id)
            )
            updated += 1
        else:
            c.execute(
                """INSERT INTO team_members
                   (name,role,agent_type,mission,status,current_task,emoji,color,openclaw_id,source)
                   VALUES (?,?,?,?,?,?,?,?,?,'openclaw')""",
                (name, role, "sub", mission, status, task, emoji, color, oc_id)
            )
            added += 1

    c.commit()
    c.close()
    return {"ok": True, "added": added, "updated": updated, "total": len(agents)}

# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------
@app.get("/api/memories")
def get_memories():
    path = Path(MEMORIES_PATH)
    if not path.exists():
        return {"vault": OBSIDIAN_VAULT, "days": []}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"vault": OBSIDIAN_VAULT, "days": _parse_memories(text)}

def _parse_memories(text: str) -> list:
    entries, current_date, current_items = [], None, []
    lines = text.splitlines()
    i = 0
    # Skip YAML / qmd frontmatter (--- ... ---)
    if lines and lines[0].strip() == '---':
        i = 1
        while i < len(lines) and lines[i].strip() != '---':
            i += 1
        i += 1  # skip closing ---
    for line in lines[i:]:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', line)
        is_header = line.startswith('#') or re.match(r'^\[?\d{4}-\d{2}-\d{2}', line.strip())
        if m and is_header:
            if current_date and current_items:
                entries.append({"date": current_date, "items": current_items})
            current_date = m.group(1)
            current_items = []
            # Strip only # and date, preserve wikilinks
            rest = re.sub(r'^#+\s*', '', line)
            rest = re.sub(r'\d{4}-\d{2}-\d{2}:?', '', rest).strip()
            if rest:
                current_items.append(rest)
        elif line.strip() and not line.startswith('#'):
            clean = line.strip().lstrip('-*+ ').strip()
            if clean:
                if not current_date:
                    current_date = "sans date"
                current_items.append(clean)
    if current_date and current_items:
        entries.append({"date": current_date, "items": current_items})
    return sorted(entries, key=lambda x: x["date"], reverse=True)

# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------
@app.get("/api/cron")
def get_cron():
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        jobs = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(None, 5)
            if len(parts) >= 6:
                jobs.append({"schedule": " ".join(parts[:5]), "command": parts[5], "raw": line,
                             "human": _cron_human(" ".join(parts[:5]))})
            elif parts:
                jobs.append({"schedule": line, "command": "", "raw": line, "human": ""})
        return jobs
    except Exception:
        return []

def _cron_human(schedule: str) -> str:
    parts = schedule.split()
    if len(parts) != 5:
        return schedule
    mn, hr, dom, mo, dow = parts
    if schedule == "* * * * *":
        return "Chaque minute"
    if mn != '*' and hr != '*' and dom == '*' and mo == '*' and dow == '*':
        days = ["Dim","Lun","Mar","Mer","Jeu","Ven","Sam"]
        return f"Quotidien a {hr}h{mn}"
    if mn != '*' and hr != '*' and dom == '*' and mo == '*' and dow != '*':
        days = ["Dim","Lun","Mar","Mer","Jeu","Ven","Sam"]
        d = days[int(dow)] if dow.isdigit() and int(dow) < 7 else dow
        return f"Hebdo {d} a {hr}h{mn}"
    return schedule

# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------
@app.get("/api/activity")
def list_activity(limit: int = 50):
    c = get_db()
    rows = c.execute("SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.post("/api/activity", status_code=201)
async def log_activity(a: ActivityIn):
    c = get_db()
    c.execute("INSERT INTO activity_log (agent,action,details) VALUES (?,?,?)",
              (a.agent, a.action, json.dumps(a.details)))
    c.commit()
    c.close()
    event = {"type": "activity", "agent": a.agent, "action": a.action,
             "details": a.details, "timestamp": datetime.now().isoformat()}
    await broadcast(event)
    return {"ok": True}

# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
@app.post("/api/heartbeat")
async def post_heartbeat(h: HeartbeatIn):
    c = get_db()
    c.execute("INSERT INTO heartbeats (agent,status,metadata) VALUES (?,?,?)",
              (h.agent, h.status, json.dumps(h.metadata)))
    task_label   = h.metadata.get("current_task")
    agent_status = "working" if task_label else "idle"
    role         = h.metadata.get("role", "OpenClaw Agent")

    # Auto-enregistrement : crée la fiche si l'agent n'existe pas encore
    existing = c.execute("SELECT id FROM team_members WHERE name=?", (h.agent,)).fetchone()
    if existing:
        c.execute("UPDATE team_members SET status=?,current_task=? WHERE name=?",
                  (agent_status, task_label, h.agent))
    else:
        c.execute("""INSERT INTO team_members
                     (name,role,agent_type,status,current_task,emoji,color,source)
                     VALUES (?,?,'sub',?,?,'🤖','#6366f1','openclaw')""",
                  (h.agent, role, agent_status, task_label))

    c.commit()
    pending = c.execute(
        "SELECT * FROM tasks WHERE status='todo' AND assigned_to=? ORDER BY created_at DESC LIMIT 10",
        (h.agent,)).fetchall()
    c.close()
    await broadcast({
        "type":         "heartbeat",
        "agent":        h.agent,
        "status":       agent_status,
        "current_task": task_label,
        "timestamp":    datetime.now().isoformat(),
    })
    return {"ok": True, "pending_tasks": [dict(r) for r in pending]}

@app.get("/api/heartbeat")
def get_heartbeat():
    c = get_db()
    rows = c.execute(
        "SELECT agent, MAX(timestamp) as last_beat, status FROM heartbeats GROUP BY agent").fetchall()
    c.close()
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------
@app.get("/events")
async def sse_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    sse_clients.append(q)

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if q in sse_clients:
                sse_clients.remove(q)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ---------------------------------------------------------------------------
# OpenClaw proxy
# ---------------------------------------------------------------------------

def _oc_headers():
    h = {"Content-Type": "application/json"}
    if OPENCLAW_TOKEN:
        h["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"
    return h

def _oc_get(path: str, timeout: int = 5):
    """GET vers l'instance OpenClaw."""
    try:
        r = _requests.get(f"{OPENCLAW_URL}{path}", headers=_oc_headers(), timeout=timeout)
        r.raise_for_status()
        return r.json()
    except _requests.exceptions.ConnectionError:
        raise HTTPException(502, detail="OpenClaw inaccessible (connexion refusée)")
    except _requests.exceptions.Timeout:
        raise HTTPException(504, detail="OpenClaw timeout")
    except _requests.exceptions.HTTPError as e:
        raise HTTPException(e.response.status_code, detail=str(e))

def _oc_post(path: str, payload: dict = None, timeout: int = 10):
    """POST vers l'instance OpenClaw."""
    try:
        r = _requests.post(
            f"{OPENCLAW_URL}{path}",
            headers=_oc_headers(),
            json=payload or {},
            timeout=timeout,
        )
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"ok": True}
    except _requests.exceptions.ConnectionError:
        raise HTTPException(502, detail="OpenClaw inaccessible (connexion refusée)")
    except _requests.exceptions.Timeout:
        raise HTTPException(504, detail="OpenClaw timeout")
    except _requests.exceptions.HTTPError as e:
        raise HTTPException(e.response.status_code, detail=str(e))

# ── Status ──────────────────────────────────────────────────────────────────
@app.get("/api/openclaw/status")
def openclaw_status():
    """Vérifie si OpenClaw est joignable et retourne quelques métriques."""
    try:
        r = _requests.get(f"{OPENCLAW_URL}/", headers=_oc_headers(), timeout=3)
        reachable = r.status_code < 500
    except Exception:
        reachable = False

    # Essaie aussi /health et /api/status
    info = {}
    if reachable:
        for probe in ["/health", "/api/status", "/status"]:
            try:
                resp = _requests.get(f"{OPENCLAW_URL}{probe}", headers=_oc_headers(), timeout=3)
                if resp.ok:
                    info = resp.json()
                    break
            except Exception:
                pass

    return {"reachable": reachable, "url": OPENCLAW_URL, "info": info}

# ── List agents ─────────────────────────────────────────────────────────────
@app.get("/api/openclaw/agents")
def openclaw_agents():
    """Liste les agents disponibles dans OpenClaw.
    Essaie plusieurs endpoints courants dans l'ordre."""
    candidates = [
        "/api/agents",
        "/agents",
        "/v1/agents",
        "/api/sessions",
        "/sessions",
    ]
    for path in candidates:
        try:
            r = _requests.get(f"{OPENCLAW_URL}{path}", headers=_oc_headers(), timeout=4)
            if r.ok:
                data = r.json()
                # Normalise : on veut toujours une liste
                if isinstance(data, list):
                    return {"agents": data, "endpoint": path}
                if isinstance(data, dict):
                    for key in ("agents", "sessions", "items", "data", "results"):
                        if key in data and isinstance(data[key], list):
                            return {"agents": data[key], "endpoint": path}
                    return {"agents": [data], "endpoint": path}
        except Exception:
            pass

    raise HTTPException(404, detail="Aucun endpoint d'agents trouvé sur OpenClaw. Vérifie l'URL ou configure OPENCLAW_URL.")

# ── Start agent ──────────────────────────────────────────────────────────────
@app.post("/api/openclaw/agents/{agent_id}/start")
def openclaw_start(agent_id: str):
    """Démarre / réveille un agent OpenClaw."""
    for path in [f"/api/agents/{agent_id}/start", f"/agents/{agent_id}/start",
                 f"/api/agents/{agent_id}/run"]:
        try:
            r = _requests.post(f"{OPENCLAW_URL}{path}", headers=_oc_headers(),
                               json={}, timeout=8)
            if r.ok:
                try:
                    return r.json()
                except Exception:
                    return {"ok": True}
        except _requests.exceptions.ConnectionError:
            raise HTTPException(502, "OpenClaw inaccessible")
        except Exception:
            pass
    raise HTTPException(404, detail=f"Endpoint start introuvable pour l'agent {agent_id}")

# ── Stop agent ───────────────────────────────────────────────────────────────
@app.post("/api/openclaw/agents/{agent_id}/stop")
def openclaw_stop(agent_id: str):
    """Arrête un agent OpenClaw."""
    for path in [f"/api/agents/{agent_id}/stop", f"/agents/{agent_id}/stop",
                 f"/api/agents/{agent_id}/kill"]:
        try:
            r = _requests.post(f"{OPENCLAW_URL}{path}", headers=_oc_headers(),
                               json={}, timeout=8)
            if r.ok:
                try:
                    return r.json()
                except Exception:
                    return {"ok": True}
        except _requests.exceptions.ConnectionError:
            raise HTTPException(502, "OpenClaw inaccessible")
        except Exception:
            pass
    raise HTTPException(404, detail=f"Endpoint stop introuvable pour l'agent {agent_id}")

# ── Logs ─────────────────────────────────────────────────────────────────────
@app.get("/api/openclaw/agents/{agent_id}/logs")
def openclaw_logs(agent_id: str, limit: int = 100):
    """Récupère les logs / l'historique d'un agent."""
    candidates = [
        f"/api/agents/{agent_id}/logs",
        f"/agents/{agent_id}/logs",
        f"/api/agents/{agent_id}/history",
        f"/api/sessions/{agent_id}/messages",
        f"/sessions/{agent_id}/logs",
    ]
    for path in candidates:
        try:
            r = _requests.get(f"{OPENCLAW_URL}{path}",
                              headers=_oc_headers(),
                              params={"limit": limit},
                              timeout=6)
            if r.ok:
                data = r.json()
                if isinstance(data, list):
                    return {"logs": data, "endpoint": path}
                if isinstance(data, dict):
                    for key in ("logs", "messages", "history", "items", "data"):
                        if key in data and isinstance(data[key], list):
                            return {"logs": data[key], "endpoint": path}
                    return {"logs": [data], "endpoint": path}
        except _requests.exceptions.ConnectionError:
            raise HTTPException(502, "OpenClaw inaccessible")
        except Exception:
            pass
    raise HTTPException(404, detail=f"Endpoint logs introuvable pour l'agent {agent_id}")
