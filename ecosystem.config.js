module.exports = {
  apps: [
    {
      name: "dashboard",
      script: "venv/bin/python",
      args: "-m uvicorn main:app --host 0.0.0.0 --port 8080",
      cwd: "/home/dashboard",
      interpreter: "none",
      env: {
        PYTHONPATH: "/home/dashboard",
        IBKR_FLEX_TOKEN: process.env.IBKR_FLEX_TOKEN || "",
        IBKR_FLEX_QUERY_ID: process.env.IBKR_FLEX_QUERY_ID || "",
        MEMORIES_PATH: "/root/memories.md",
      },
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
    },
  ],
};
