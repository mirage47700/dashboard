# VPS Dashboard

Dashboard personnel — agenda, tâches, notes — exposé via Cloudflare Tunnel.

## Stack

- **Backend** : FastAPI + SQLite (Python 3.11)
- **Frontend** : Vanilla JS/CSS (dark theme)
- **Auth** : Cloudflare Access (Zero Trust)
- **Déploiement** : Docker Compose sur VPS

---

## Démarrage rapide (développement local)

```bash
cd dashboard
pip install -r requirements.txt
uvicorn main:app --reload
# → http://localhost:8000
```

---

## Déploiement sur VPS

### 1. Cloner le repo sur le VPS

```bash
git clone <repo-url>
cd dashboard
```

### 2. Créer le virtualenv et installer les dépendances

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Démarrer avec PM2 (premier lancement)

```bash
pm2 start ecosystem.config.js
pm2 save
pm2 startup   # pour démarrer au boot
```

Le serveur écoute sur `127.0.0.1:8000` (pas exposé publiquement).

### 4. Mettre à jour (fetch + merge + reload)

```bash
./deploy.sh
```

Ou manuellement :

```bash
git fetch origin main
git merge origin/main
.venv/bin/pip install -r requirements.txt -q
pm2 reload dashboard
```

### Commandes PM2 utiles

```bash
pm2 status          # état du process
pm2 logs dashboard  # voir les logs
pm2 restart dashboard
pm2 stop dashboard
```

---

### Déploiement avec Docker Compose (alternative)

```bash
docker compose up -d --build
```

---

## Configuration Cloudflare Tunnel

### Prérequis
- Compte Cloudflare avec un domaine
- `cloudflared` installé sur le VPS

### Étapes

#### 1. Installer cloudflared

```bash
# Debian/Ubuntu
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared jammy main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared
```

#### 2. Authentifier cloudflared

```bash
cloudflared tunnel login
```

#### 3. Créer le tunnel

```bash
cloudflared tunnel create dashboard
```

#### 4. Configurer le tunnel

Créer `~/.cloudflared/config.yml` :

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: dashboard.ton-domaine.com
    service: http://localhost:8000
  - service: http_status:404
```

#### 5. Créer un DNS record

```bash
cloudflared tunnel route dns dashboard dashboard.ton-domaine.com
```

#### 6. Lancer le tunnel comme service système

```bash
cloudflared service install
systemctl enable cloudflared
systemctl start cloudflared
```

---

## Cloudflare Access (Zero Trust Auth)

1. Dans le dashboard Cloudflare → **Zero Trust** → **Access** → **Applications**
2. **Add an application** → Self-hosted
3. **Application domain** : `dashboard.ton-domaine.com`
4. **Ajouter une Policy** :
   - Name : `Owner only`
   - Action : Allow
   - Include : Emails → `ton@email.com`
5. Cloudflare gère l'authentification — aucun code d'auth à écrire

---

## Fonctionnalités

| Section | Description |
|---------|-------------|
| **Agenda** | Mini calendrier + événements du jour avec timeline |
| **Tâches** | CRUD complet, priorités, filtres, groupes (retard/aujourd'hui/à venir) |
| **Notes** | Post-its colorés, persistés en SQLite |

### Raccourcis clavier
- `Ctrl+N` — Nouvelle tâche
- `Escape` — Fermer la modal

---

## Structure

```
dashboard/
├── main.py              # FastAPI app + API REST
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── data/                # SQLite (auto-créé)
│   └── dashboard.db
├── static/
│   ├── css/style.css
│   └── js/app.js
└── templates/
    └── index.html
```
