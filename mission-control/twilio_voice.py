"""
Twilio Voice ↔ Kokoro TTS ↔ OpenClaw Agent
------------------------------------------
Flow :
  Appel entrant Twilio
    → /twilio/inbound     : salutation Kokoro + <Gather speech>
    → /twilio/respond     : transcription → agent → Kokoro → <Play> + <Gather>
    → /twilio/audio/{f}   : sert les fichiers MP3 générés
"""
from fastapi import APIRouter, Form, Response
from fastapi.responses import FileResponse
import httpx, os, uuid, re
from pathlib import Path
import tempfile

router = APIRouter(prefix="/twilio")

# ── Config ────────────────────────────────────────────────────────────────────
KOKORO_URL         = os.getenv("KOKORO_URL",         "http://localhost:8001")
KOKORO_VOICE       = os.getenv("KOKORO_VOICE",       "af_alloy")
PUBLIC_URL         = os.getenv("PUBLIC_URL",         "")   # URL publique Cloudflare tunnel
OPENCLAW_URL       = os.getenv("OPENCLAW_URL",       "http://localhost:8000")
OPENCLAW_CHAT_PATH = os.getenv("OPENCLAW_CHAT_PATH", "/api/chat")
OPENCLAW_TOKEN     = os.getenv("OPENCLAW_TOKEN",     "")
TWILIO_LANG        = os.getenv("TWILIO_LANG",        "fr-FR")

AUDIO_DIR = Path(tempfile.gettempdir()) / "twilio_tts"
AUDIO_DIR.mkdir(exist_ok=True)

# Sessions en mémoire : CallSid → historique [{role, content}]
_sessions: dict[str, list] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _twiml(xml: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{xml}</Response>',
        media_type="text/xml",
    )


def _gather(action: str, timeout: int = 10) -> str:
    """Ouvre un <Gather> speech vers `action`."""
    return (
        f'<Gather input="speech" action="{action}" method="POST" '
        f'language="{TWILIO_LANG}" speechTimeout="auto" timeout="{timeout}">'
    )


def _kokoro_tts(text: str) -> str | None:
    """Appelle Kokoro-FastAPI, sauvegarde le MP3, retourne le nom de fichier."""
    fname = f"{uuid.uuid4().hex}.mp3"
    path = AUDIO_DIR / fname
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{KOKORO_URL}/v1/audio/speech",
                json={
                    "model": "kokoro",
                    "input": text,
                    "voice": KOKORO_VOICE,
                    "response_format": "mp3",
                },
            )
            r.raise_for_status()
            path.write_bytes(r.content)
        return fname
    except Exception as e:
        print(f"[Kokoro TTS] {e}")
        return None


def _audio_url(fname: str) -> str:
    return f"{PUBLIC_URL.rstrip('/')}/twilio/audio/{fname}"


def _say(text: str) -> str:
    """Fallback Twilio <Say> si Kokoro est indispo."""
    safe = text[:500].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<Say language="{TWILIO_LANG}">{safe}</Say>'


def _play_or_say(text: str) -> str:
    """Génère le TTS Kokoro si possible, sinon <Say> natif Twilio."""
    if PUBLIC_URL:
        fname = _kokoro_tts(text)
        if fname:
            return f'<Play>{_audio_url(fname)}</Play>'
    return _say(text)


def _call_agent(message: str, history: list) -> str:
    """Envoie le message à l'agent OpenClaw, retourne sa réponse textuelle."""
    headers = {}
    if OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_TOKEN}"
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{OPENCLAW_URL}{OPENCLAW_CHAT_PATH}",
                json={"message": message, "history": history},
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
        # Formats de réponse courants
        return (
            data.get("response")
            or data.get("text")
            or data.get("content")
            or data.get("message")
            or data.get("reply")
            or str(data)
        )
    except Exception as e:
        print(f"[Agent] {e}")
        return "Désolé, je rencontre un problème technique. Réessayez dans un instant."


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/audio/{fname}")
def serve_audio(fname: str):
    """Sert les fichiers MP3 générés par Kokoro."""
    # Sécurité : nom de fichier alphanumérique uniquement
    if not re.fullmatch(r"[a-f0-9]{32}\.mp3", fname):
        return Response(status_code=400)
    path = AUDIO_DIR / fname
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path, media_type="audio/mpeg")


@router.post("/inbound")
def inbound_call(
    CallSid: str = Form(...),
    Caller: str = Form(default=""),
):
    """Point d'entrée : appel Twilio entrant."""
    _sessions[CallSid] = []
    greeting = "Bonjour ! Je suis votre assistant. Comment puis-je vous aider ?"
    play = _play_or_say(greeting)
    return _twiml(
        play
        + _gather("/twilio/respond")
        + "</Gather>"
        + f'<Say language="{TWILIO_LANG}">Au revoir !</Say>'
        + "<Hangup/>"
    )


@router.post("/respond")
def handle_speech(
    CallSid: str = Form(...),
    SpeechResult: str = Form(default=""),
    Confidence: str = Form(default=""),
):
    """Reçoit la transcription Twilio, interroge l'agent, répond en Kokoro."""
    history = _sessions.get(CallSid, [])

    if not SpeechResult.strip():
        play = _play_or_say("Je n'ai pas entendu. Pouvez-vous répéter ?")
        return _twiml(
            play
            + _gather("/twilio/respond")
            + "</Gather>"
            + f'<Say language="{TWILIO_LANG}">Au revoir !</Say>'
            + "<Hangup/>"
        )

    # Historique utilisateur
    history.append({"role": "user", "content": SpeechResult})

    # Appel agent
    reply = _call_agent(SpeechResult, history)

    # Historique agent
    history.append({"role": "assistant", "content": reply})
    _sessions[CallSid] = history[-20:]  # garde les 20 derniers tours

    play = _play_or_say(reply)
    return _twiml(
        play
        + _gather("/twilio/respond")
        + "</Gather>"
        + f'<Say language="{TWILIO_LANG}">Au revoir !</Say>'
        + "<Hangup/>"
    )


@router.post("/status")
def call_status(
    CallSid: str = Form(default=""),
    CallStatus: str = Form(default=""),
):
    """Callback Twilio fin d'appel : nettoyage session."""
    if CallStatus in ("completed", "failed", "busy", "no-answer"):
        _sessions.pop(CallSid, None)
    return Response(status_code=204)
