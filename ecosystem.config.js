module.exports = {
  apps: [
    {
      name: "dashboard",
      script: ".venv/bin/uvicorn",
      args: "main:app --host 127.0.0.1 --port 8000",
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
