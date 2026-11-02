/** Synlora main 1.8.3 Linux/PM2 生产配置。 */
module.exports = {
  apps: [
    {
      name: "synlora-main",
      cwd: "./apps/web/backend",
      script: "run_uvicorn.py",
      interpreter: process.env.SYNLSAGENT_PYTHON || "/polymer/conda/envs/synlysagent/bin/python",
      env: {
        PYTHONUTF8: "1",
        HOST: process.env.SYNLORA_HOST || "0.0.0.0",
        PORT: process.env.SYNLORA_PORT || "8006",
      },
      max_restarts: 10,
      restart_delay: 3000,
    },
  ],
};
