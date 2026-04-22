# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AntiHub-ALL is a Docker Compose monorepo integrating AntiHub full-stack services:
- **AntiHub/** — Next.js 16 frontend (TypeScript, React 19, Tailwind CSS 4)
- **AntiHub-Backend/** — FastAPI backend (Python 3.10+, SQLAlchemy 2.0, Alembic)
- **AntiHook/** — Tauri v2 desktop configuration tool (Rust + React/Vite)
- **4-docs/** — This folder contains some project documents. Please check after each implementation to see if any documents need to be updated. 

## Common Commands

### Docker Deployment (Recommended)
```bash
cp .env.example .env
docker compose up -d

# Start only the core trio (using external PG/Redis)
docker compose -f docker-compose.core.yml up -d

# Manually sync database
docker compose -f docker-compose.yml -f docker/docker-compose.db-init.yml run --rm db-init

# Generate encryption key
docker compose run --rm backend python generate_encryption_key.py
```

### Module Local Development

**Frontend (AntiHub/)**
```bash
cd AntiHub && pnpm install
pnpm dev      # Development server
pnpm build    # Build
pnpm lint     # ESLint check
```

**Backend (AntiHub-Backend/)**
```bash
cd AntiHub-Backend && uv sync
uv run uvicorn app.main:app --reload --port 8008

# Database migration
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"
uv run alembic downgrade -1
```

**Desktop App (AntiHook/)**
```bash
cd AntiHook && npm install
npm run tauri dev
```
必须配置的密钥（在 `.env` 中）：
- `JWT_SECRET_KEY` — JWT 签名密钥
- `PLUGIN_API_ENCRYPTION_KEY` — Fernet 加密密钥（32字节 base64）

可选配置：
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — 首次启动自动创建管理员
- `CODEX_SUPPORTED_MODELS` — 覆盖 Codex 模型列表
- `CODEX_PROXY_URL` — Codex 出站代理

**添加新环境变量时**，需要同时更新对应的 `*.example` 文件并文档化默认值。

## 测试

目前没有统一的测试运行器。验证方式：
1. Docker 冒烟测试：`docker compose up`
2. 手动验证受影响的 UI 路由 / API 端点

## 提交和 PR 指南

提交信息遵循 `<type>: <summary>` 格式（常见类型：`feat:`、`fix:`；`!` 表示破坏性变更）。

PR 应包含：
- **改动说明** — 做了什么、为什么做
- **验证方式** — 具体的验证命令和步骤
- **UI 变更截图** — 如有前端改动，需提供截图
- **环境变量更新** — 如添加新的环境变量，需更新 `*.example` 文件并文档化默认值

## API 文档

后端启动后访问：
- Swagger UI: `http://localhost:8008/api/docs`
- ReDoc: `http://localhost:8008/api/redoc`

## 注意事项

- 前端已内置 `/backend/* -> http://backend:8000/*` 转发
- 生产环境 API 文档会被禁用
