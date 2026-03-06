module.exports = {
  apps: [
    {
      name: "dashboard",
      script: ".venv/bin/python",
      args: "-m uvicorn main:app --host 0.0.0.0 --port 8080",
      cwd: "/home/user/dashboard",
      interpreter: "none",
      env: {
        PYTHONPATH: "/home/user/dashboard",
      },
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
    },
  ],
};
