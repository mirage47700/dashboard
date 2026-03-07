module.exports = {
  apps: [{
    name: 'mission-control',
    script: '/home/dashboard/venv/bin/uvicorn',
    args: 'main:app --host 0.0.0.0 --port 8081',
    cwd: '/home/dashboard/mission-control',
    interpreter: 'none',
    env: {
      MEMORIES_PATH: '/root/memories.md',
      DB_PATH: '/home/dashboard/mission-control/mission_control.db'
    },
    autorestart: true,
    watch: false,
    max_memory_restart: '300M',
    error_file: '/home/dashboard/logs/mission-control-error.log',
    out_file: '/home/dashboard/logs/mission-control-out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss'
  }]
};
