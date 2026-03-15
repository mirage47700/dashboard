# Intégration Dashboard — System Prompt Agent

Colle ce bloc dans le system prompt (ou les instructions) de chaque agent OpenClaw.
Remplace `MON_AGENT` par le nom de l'agent (ex: "TradingBot", "MailAgent", etc.)

---

## Bloc à insérer

```
## Intégration Dashboard Mission Control

Tu es connecté à un dashboard de supervision accessible sur http://localhost:8082.
Ton nom dans le dashboard est : MON_AGENT

### Heartbeat (obligatoire)
Toutes les 60 secondes, envoie un heartbeat pour signaler que tu es actif :

POST http://localhost:8082/api/heartbeat
{
  "agent": "MON_AGENT",
  "status": "alive",
  "metadata": {
    "current_task": "Description courte de ce que tu fais en ce moment, ou null si idle",
    "role": "Ton rôle (ex: Trading Agent, Mail Agent...)"
  }
}

La réponse contient "pending_tasks" : la liste des tâches qui t'ont été assignées
depuis le dashboard. Consulte-les et exécute-les si pertinent.

### Log d'activité (recommandé)
À chaque action importante, log-la pour qu'elle apparaisse dans le Live Feed :

POST http://localhost:8082/api/activity
{
  "agent": "MON_AGENT",
  "action": "Description de l'action effectuée",
  "details": {}   ← optionnel, peut contenir des métadonnées
}

Exemples d'actions à logger :
- Début d'une tâche longue
- Résultat d'une analyse
- Erreur rencontrée
- Tâche terminée

### Règles
- Le heartbeat est ta responsabilité : envoie-le régulièrement même si tu n'as rien à faire.
- current_task doit être null (pas de string vide) quand tu es idle.
- Ne log pas chaque micro-action, seulement les étapes significatives.
- Si le dashboard est inaccessible, continue ton travail normalement sans t'arrêter.
```

---

## Exemple Python (hook à intégrer dans l'agent)

```python
import requests
import threading
import time

DASHBOARD = "http://localhost:8082"
AGENT_NAME = "MON_AGENT"

def heartbeat(current_task: str = None):
    """À appeler toutes les 60s et à chaque changement de tâche."""
    try:
        r = requests.post(f"{DASHBOARD}/api/heartbeat", json={
            "agent": AGENT_NAME,
            "status": "alive",
            "metadata": {
                "current_task": current_task,
                "role": "OpenClaw Agent"
            }
        }, timeout=3)
        data = r.json()
        return data.get("pending_tasks", [])
    except Exception:
        return []

def log_activity(action: str, details: dict = {}):
    """À appeler après chaque action importante."""
    try:
        requests.post(f"{DASHBOARD}/api/activity", json={
            "agent": AGENT_NAME,
            "action": action,
            "details": details
        }, timeout=3)
    except Exception:
        pass

# Boucle heartbeat en arrière-plan
def _heartbeat_loop(get_current_task_fn):
    while True:
        heartbeat(get_current_task_fn())
        time.sleep(60)

def start_heartbeat(get_current_task_fn=lambda: None):
    t = threading.Thread(target=_heartbeat_loop, args=(get_current_task_fn,), daemon=True)
    t.start()
```

## Utilisation

```python
# Au démarrage de l'agent :
current_task_ref = {"value": None}
start_heartbeat(lambda: current_task_ref["value"])

# Pendant le travail :
current_task_ref["value"] = "Analyse du portefeuille IBKR"
log_activity("Début analyse portefeuille", {"source": "IBKR"})

# ... travail ...

log_activity("Analyse terminée", {"trades": 12, "pnl": "+2.3%"})
current_task_ref["value"] = None  # → status idle dans le dashboard
```
