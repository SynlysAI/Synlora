/** SynlysAgent PM2 部署配置（仓库根目录）。
 *
 * 用法：pm2 start ecosystem.config.cjs
 * 说明：interpreter 指向 conda 环境 synlysagent 的 python，由其直接执行
 * apps/web/backend/run_uvicorn.py（内部读 Settings 并 uvicorn.run 启动）。
 */
module.exports = {
  apps: [
    {
      name: "synlys-agent",
      cwd: "./apps/web/backend",
      script: "run_uvicorn.py",
      interpreter: "C:/conda_envs/synlysagent/python.exe",
      env: {
        NODE_ENV: "production",
        PYTHONUTF8: "1", // Windows 主进程日志/文件 IO 强制 UTF-8，避免中文乱码
      },
      max_restarts: 10,
      restart_delay: 3000,
    },
  ],
};
