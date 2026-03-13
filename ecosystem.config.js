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
      },
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
    },
  ],
};
