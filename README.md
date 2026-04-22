<h1 align="center">Antihub-ALL</h1>

<p align="center">
  <a href="https://github.com/zhongruan0522/AntiHub-ALL/stargazers">
    <img src="https://img.shields.io/github/stars/zhongruan0522/AntiHub-ALL?style=for-the-badge&logo=github&logoColor=white&labelColor=24292e&color=ffc107" alt="GitHub Stars" />
  </a>
  <a href="https://qm.qq.com/q/DT7fJCsCoS">
    <img src="https://img.shields.io/badge/QQ_Group-937931004-blue?style=for-the-badge&logo=tencentqq&logoColor=white&labelColor=12b7f5&color=12b7f5" alt="QQ Group" />
  </a>
  <a href="https://zread.ai/zhongruan0522/AntiHub-ALL">
    <img src="https://img.shields.io/badge/Zread-Ask_AI-00b0aa?style=for-the-badge&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTQuOTYxNTYgMS42MDAxSDIuMjQxNTZDMS44ODgxIDEuNjAwMSAxLjYwMTU2IDEuODg2NjQgMS42MDE1NiAyLjI0MDFWNC45NjAxQzEuNjAxNTYgNS4zMTM1NiAxLjg4ODEgNS42MDAxIDIuMjQxNTYgNS42MDAxSDQuOTYxNTZDNS4zMTUwMiA1LjYwMDEgNS42MDE1NiA1LjMxMzU2IDUuNjAxNTYgNC45NjAxVjIuMjQwMUM1LjYwMTU2IDEuODg2NjQgNS4zMTUwMiAxLjYwMDEgNC45NjE1NiAxLjYwMDFaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00Ljk2MTU2IDEwLjM5OTlIMi4yNDE1NkMxLjg4ODEgMTAuMzk5OSAxLjYwMTU2IDEwLjY4NjQgMS42MDE1NiAxMS4wMzk5VjEzLjc1OTlDMS42MDE1NiAxNC4xMTM0IDEuODg4MSAxNC4zOTk5IDIuMjQxNTYgMTQuMzk5OUg0Ljk2MTU2QzUuMzE1MDIgMTQuMzk5OSA1LjYwMTU2IDE0LjExMzQgNS42MDE1NiAxMy43NTk5VjExLjAzOTlDNS42MDE1NiAxMC42ODY0IDUuMzE1MDIgMTAuMzk5OSA0Ljk2MTU2IDEwLjM5OTlaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik0xMy43NTg0IDEuNjAwMUgxMS4wMzg0QzEwLjY4NSAxLjYwMDEgMTAuMzk4NCAxLjg4NjY0IDEwLjM5ODQgMi4yNDAxVjQuOTYwMUMxMC4zOTg0IDUuMzEzNTYgMTAuNjg1IDUuNjAwMSAxMS4wMzg0IDUuNjAwMUgxMy43NTg0QzE0LjExMTkgNS42MDAxIDE0LjM5ODQgNS4zMTM1NiAxNC4zOTg0IDQuOTYwMVYyLjI0MDFDMTQuMzk4NCAxLjg4NjY0IDE0LjExMTkgMS42MDAxIDEzLjc1ODQgMS42MDAxWiIgZmlsbD0iI2ZmZiIvPgo8cGF0aCBkPSJNNCAxMkwxMiA0TDQgMTJaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00IDEyTDEyIDQiIHN0cm9rZT0iI2ZmZiIgc3Ryb2tlLXdpZHRoPSIxLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgo8L3N2Zz4K&logoColor=white" alt="Zread AI" />
  </a>
  <a href="https://deepwiki.com/zhongruan0522/AntiHub-ALL">
    <img src="https://img.shields.io/badge/DeepWiki-Docs-6366f1?style=for-the-badge&logo=gitbook&logoColor=white" alt="DeepWiki" />
  </a>
</p>

# AntiHub-ALL Docker Deployment

Original project repositories:
- https://github.com/AntiHub-Project/AntiHub
- https://github.com/AntiHub-Project/Backend
- https://github.com/AntiHub-Project/Antigv-plugin (This repository has merged the plugin **runtime capabilities** into Backend; `AntiHub-plugin/` is only kept as a "migration assistant" and is not deployed as a runtime service)

The default `docker-compose.yml` includes PostgreSQL + Redis. You mainly just need to configure your own keys; if you want to connect to an external PG/Redis, use `docker-compose.core.yml` (only starts web + backend).

## Notes

Currently referencing the [Kiro.rs](https://github.com/hank9999/kiro.rs) fix for the latest version of CC. Antihub-ALL has synced `/backend/cc` as a CC-specialized port. Thanks again to the related reference projects.

## Current 2API Support

1. Antigravity: Fully supported
2. Kiro-OAuth(GitHub/Google): Fully supported
3. Kiro-Token: Fully supported
4. Kiro-AWS IMA: Fully supported
5. QwenCli: Development complete, pending testing
6. CodexCLI: Fully supported
7. GeminiCLI: Fully supported

## One-Click Deployment

On Linux, simply run `deploy.sh` (it will start `postgres/redis` first, sync/initialize the Backend main database, then start web/backend; if you need to migrate the old plugin DB, see "Upgrade/Migration (Optional)" below).

The script supports an interactive menu:

```bash
chmod +x deploy.sh
./deploy.sh
```

It also supports direct commands (convenient for tutorials/automation scripts):

```bash
./deploy.sh deploy     # 1) One-click deploy (first-time deployment / reinstall)
./deploy.sh upgrade    # 2) Upgrade (web/backend only, database will not be modified)
./deploy.sh uninstall  # 3) Uninstall (stop and remove containers, optionally delete data volumes)
```

## Quick Start

1) Configure environment variables:

```bash
cp .env.example .env
```

**Important Note**: `.env.example` contains sample keys, for development/testing only. For production deployment, you must generate new keys:

```bash
# Generate Fernet encryption key (for securely storing sensitive data like upstream API Keys)
docker compose run --rm backend python generate_encryption_key.py

# Or use openssl to generate other keys
openssl rand -base64 32  # For JWT_SECRET_KEY
```

Then update the following configurations in the `.env` file:
- `JWT_SECRET_KEY` - JWT token signing key
- `PLUGIN_API_ENCRYPTION_KEY` - Fernet encryption key (used to encrypt stored user API keys)

Login/Access method settings (easy to make mistakes here):
- `ADMIN_USERNAME` / `ADMIN_PASSWORD`: On first startup, an admin account will be automatically created with these; `ADMIN_PASSWORD` **must be at least 6 characters** (otherwise it will trigger a backend parameter validation failure and the frontend cannot log in).
- `COOKIE_HTTP`:
  - If you are using **Domain + HTTPS** (Reverse Proxy/Caddy/Nginx): Keep `COOKIE_HTTP=HTTPS`.
  - If you are using **Direct IP + HTTP** (Intranet/Testing): Set `COOKIE_HTTP=HTTP`, otherwise the browser will not write the login cookie (due to missing Secure flag).
- The reverse proxy must be configured to forward `/backend` to the backend (otherwise the frontend will show 404/API unavailable):
  - `/` -> `http://127.0.0.1:<WEB_PORT>` (Default `3000`)
  - `/backend` -> `http://127.0.0.1:<BACKEND_PORT>` (Default `8000`)

2) Start:

```bash
docker compose up -d
```

> 如果你自带 PostgreSQL/Redis：使用 `docker-compose.core.yml` 只启动 web + backend（并在 `.env` 中配置 `DATABASE_URL` 与 `REDIS_URL`）。

3) 访问前端：

- 直连：`http://localhost:3000`（或你在 `.env` 里设置的 `WEB_PORT`）
- 或者用你自己的反代把域名转发到前端端口

## 鸣谢

- [Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager)
- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)
- [KiroGate](https://github.com/aliom-v/KiroGate)
- [AIClient-2-API](https://github.com/justlovemaki/AIClient-2-API)
- [Kiro.rs](https://github.com/hank9999/kiro.rs)
- [Kiro-account-manager](https://github.com/hj01857655/kiro-account-manager)
