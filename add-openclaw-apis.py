#!/usr/bin/env python3
"""Script d'ajout des endpoints OpenClaw au dashboard Mission Control."""

import sys
from pathlib import Path

# Chemin vers le fichier principal
MAIN_FILE = Path(__file__).parent / "main.py"

# Contenu à ajouter après la fonction openclaw_status
NEW_API_ROUTES = '''
# ── List agents ─────────────────────────────────────────────────────────────
@app.get("/api/openclaw/agents")
def list_openclaw_agents():
    """Liste complète des agents OpenClaw avec status."""
    try:
        paths = ["/api/agents", "/agents", "/v1/agents"]
        for path in paths:
            try:
                data = _oc_get(path)
                if isinstance(data, dict) and "agents" in data:
                    agents = data["agents"]
                    break
                elif isinstance(data, list):
                    agents = data
                    break
                else:
                    continue
            except Exception:
                continue
        else:
            raise HTTPException(404, detail="Agents introuvables")

        # Formater les données
        formatted_agents = []
        for a in agents:
            oc_id = str(a.get("id") or a.get("session_id") or "unknown")
            name = a.get("name") or a.get("title") or oc_id
            
            # Statut normalisé
            status_raw = (a.get("status") or a.get("state") or "").lower()
            if status_raw in ["running", "active", "working", "started"]:
                status_team = "working"
            elif status_raw in ["idle", "paused", "waiting"]:
                status_team = "idle"
            else:
                status_team = "offline"

            formatted_agents.append({
                "id": oc_id,
                "name": name,
                "model": a.get("model") or a.get("llm", ""),
                "status": status_team,
                "state": a.get("status") or a.get("state"),
                "started_at": a.get("started_at") or a.get("created_at"),
                "tasks_done": a.get("completed", 0),
                "emoji": _oc_agent_emoji(name) if name else "🤖"
            })

        return {
            "agents": formatted_agents,
            "count": len(formatted_agents)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, detail=str(e))


# ── Agent details ────────────────────────────────────┼───────────────┤
@app.get("/api/openclaw/agents/{agent_id}")
def get_openclaw_agent(agent_id: str):
    """Détails d'un agent spécifique."""
    try:
        # Essayer différentes variations de l'ID
        for variation in [agent_id, f"session:{agent_id}", f"id:{agent_id}"]:
            try:
                data = _oc_get(f"/api/agents/{variation}")
                return {
                    "id": data.get("id") or data.get("session_id"),
                    "name": data.get("name"),
                    "model": data.get("model"),
                    "status": "working" if data.get("status") in ["running", "active"] else "idle",
                    "started_at": data.get("started_at"),
                }
            except Exception:
                continue
        raise HTTPException(404, detail="Agent non trouvé")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, detail=str(e))


# ── Agent start ─────────────────────────────────────┼───────────────┤
@app.post("/api/openclaw/agents/{agent_id}/start")
async def start_openclaw_agent(agent_id: str):
    """Démarrer un agent OpenClaw."""
    try:
        result = _oc_post(f"/api/agents/{agent_id}/start")
        return {"ok": True, "message": f"Agent {agent_id} démarré", "result": result}
    except Exception as e:
        raise HTTPException(400, detail=str(e))


# ── Agent stop ──────────────────────────────────────┼───────────────┤
@app.post("/api/openclaw/agents/{agent_id}/stop")
async def stop_openclaw_agent(agent_id: str):
    """Arrêter un agent OpenClaw."""
    try:
        result = _oc_post(f"/api/agents/{agent_id}/stop")
        return {"ok": True, "message": f"Agent {agent_id} arrêté", "result": result}
    except Exception as e:
        raise HTTPException(400, detail=str(e))


# ── Agent logs ──────────────────────────────────────┼───────────────┤
@app.get("/api/openclaw/agents/{agent_id}/logs")
def get_openclaw_agent_logs(agent_id: str, limit: int = 100):
    """Récupérer les logs d'un agent."""
    try:
        data = _oc_get(f"/api/agents/{agent_id}/logs?limit={limit}")
        if isinstance(data, dict) and "logs" in data:
            logs = data["logs"]
        else:
            logs = data if isinstance(data, list) else []
        
        return {
            "agent_id": agent_id,
            "logs": logs[:limit],
            "count": len(logs),
            "timestamp": datetime.now().isoformat()
        }
    except HTTPException as e:
        if e.status_code == 404:
            raise
        raise HTTPException(502, detail=str(e))
'''

# Chercher l'endroit où ajouter (après openclaw_status)
content = MAIN_FILE.read_text(encoding="utf-8")

# Trouver la position après openclaw_status
marker = "# ── List agents"
if marker in content:
    print(f"✅ Endpoint {marker} existe déjà, rien à faire !")
    sys.exit(0)

# Ajouter les nouvelles routes avant SSE ou après openclaw_status
insert_pos = content.find("# ── Status ─────────────")
if insert_pos == -1:
    print("❌ Impossible de trouver la position d'insertion")
    sys.exit(1)

# Insérer les nouvelles routes
new_content = content[:insert_pos] + NEW_API_ROUTES + "\n\n" + content[insert_pos:]

# Écrire le nouveau fichier
MAIN_FILE.write_text(new_content, encoding="utf-8")

print("✅ Endpoints OpenClaw ajoutés avec succès !")
print(f"📍 Ajouté après: # ── Status ─────────────")
print("\nNouveaux endpoints:")
print("  • GET /api/openclaw/agents          - Liste des agents")
print("  • GET /api/openclaw/agents/{id}     - Détails agent")
print("  • POST /api/openclaw/agents/{id}/start   - Démarrer")
print("  • POST /api/openclaw/agents/{id}/stop    - Arrêter")
print("  • GET /api/openclaw/agents/{id}/logs     - Logs")
