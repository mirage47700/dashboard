module.exports = {
  apps: [
    {
      name: "dashboard",
      script: "venv/bin/python",
      args: "-m uvicorn main:app --host 0.0.0.0 --port 8000",
      cwd: "/home/dashboard",
      interpreter: "none",
      env: {
        PYTHONPATH: "/home/dashboard",
        IBKR_FLEX_TOKEN: process.env.IBKR_FLEX_TOKEN || "",
        IBKR_FLEX_QUERY_ID: process.env.IBKR_FLEX_QUERY_ID || "",
        MEMORIES_PATH: "/root/memories.md",
        GOOGLE_REDIRECT_URI: "https://dashboard.openclawgeorges.com/auth/google/callback",
        // ── Twilio Voice ──────────────────────────────────────────────────
        KOKORO_URL: process.env.KOKORO_URL || "http://localhost:8001",
        KOKORO_VOICE: process.env.KOKORO_VOICE || "af_alloy",
        PUBLIC_URL: process.env.PUBLIC_URL || "https://voice.openclawgeorges.com",
        OPENCLAW_URL: process.env.OPENCLAW_URL || "http://localhost:18789",
        OPENCLAW_CHAT_PATH: process.env.OPENCLAW_CHAT_PATH || "/api/chat",
        OPENCLAW_TOKEN: process.env.OPENCLAW_TOKEN || "",
        TWILIO_LANG: process.env.TWILIO_LANG || "fr-FR",
      },
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
    },
  ],
};
