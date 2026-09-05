# 会议进度台

面向个人项目管理者的会议记录与项目进度工具。将钉钉群消息或会议记录粘贴进系统，大模型生成结构化草稿，人工确认后形成任务、风险、里程碑和周报。

## 功能

- 单管理员登录
- 会议收件箱：按未解析、解析失败、待确认、已入库展示下一步，可从失败处重试
- 新会议默认“保存并开始解析”，也可仅保存原文后再解析
- AI 草稿异常优先：项目缺失、日期异常、人员未匹配、疑似重复默认展开；支持只看异常、批量接受推荐或批量忽略异常项
- 任务详情中的一次进展更新会记录本次完成、下一步、风险、状态、进度和截止日期，并同步到时间线与周报
- 首页行动台聚合待确认、逾期、本周到期、长期未更新和未解决风险，并按负责人生成可复制的钉钉跟进文案
- 周报提供预览与 Markdown 标签页，支持一键复制
- `/` 聚焦任务搜索，`n` 新建会议；在输入框、可编辑区域或使用组合/修饰键时不会劫持按键
- JSON 与 CSV ZIP 导出
- SQLite 持久化和 Docker Compose 部署

## Docker 部署

```bash
cp .env.example .env
```

编辑 `.env`：至少替换 `DJANGO_SECRET_KEY`、`ADMIN_PASSWORD`、`DJANGO_ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS`、`LLM_API_KEY` 和 `LLM_MODEL`。将 `DJANGO_ALLOWED_HOSTS` 中的示例域名替换为实际域名，并保留 `127.0.0.1` 供容器健康检查使用。

生产启动会拒绝示例占位值和弱凭据：Secret Key 至少 50 个字符且至少包含 5 种不同字符；首次创建管理员时，密码至少 12 个字符、不能是常见密码或纯数字、不能过于接近用户名，并且至少包含 4 种不同字符。生成 Secret Key：

`PYPI_INDEX_URL` 默认使用阿里云 PyPI 镜像以改善国内服务器构建稳定性；海外服务器可改为 `https://pypi.org/simple`。

端口默认只绑定 `127.0.0.1`，适合本机使用和同机反向代理。只有明确需要从其他机器直接访问端口时，才把 `APP_BIND` 改成服务器指定网卡地址。

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

启动：

```bash
docker compose up -d --build
docker compose ps
docker compose port app 8000
```

`docker compose port app 8000` 会显示实际发布的地址（例如 `127.0.0.1:18080`）。将这段输出用于健康检查：`curl -fsS -H 'X-Forwarded-Proto: https' http://127.0.0.1:18080/health/`。首次启动创建管理员。以后修改 `.env` 中的 `ADMIN_PASSWORD` 不会覆盖数据库中的密码，请在“设置”页面修改。

`docker compose up -d --build` 会重建镜像，但已运行容器不会因为仅修改了 `.env` 自动获得新环境变量。修改端口、绑定地址、安全开关、LLM 或超时配置后，请显式重建容器（不删除数据卷）：

```bash
docker compose up -d --force-recreate
```

解析是同步的真实模型请求，供应商生成复杂会议可能需要 20–90 秒；但默认 `LLM_TIMEOUT_SECONDS=60`，约 60 秒仍未完成的请求会被应用中止。等待反馈和失败后的重试入口不会让模型生成变快。该值是首次请求和最多一次格式修复共享的端到端墙钟上限；若提高它，必须同时把 `GUNICORN_TIMEOUT` 和反向代理读取超时提高到更大的值。`GUNICORN_TIMEOUT` 只是进程级保护，不是模型请求的 deadline。若更看重响应速度，可在 `.env` 中选择同一服务商提供的轻量模型。

### 域名与 HTTPS

应用监听服务器的 `.env` 中 `APP_PORT`；反向代理应转发到同一个本机端口，例如 `APP_PORT=18080` 时使用 `http://127.0.0.1:18080`，并传递 `Host`、`X-Forwarded-For` 和 `X-Forwarded-Proto`。绑定域名的生产环境必须使用 HTTPS，并将完整 HTTPS 地址写入 `CSRF_TRUSTED_ORIGINS`。若使用 Nginx，请为解析接口设置高于 `LLM_TIMEOUT_SECONDS` 的读取超时，例如 `proxy_read_timeout 300s;`，避免代理层先于应用 deadline 断开。

只在本机通过 HTTP 直接验收容器时，可临时设置 `DJANGO_SECURE_SSL_REDIRECT=false`；绑定域名后应恢复为 `true`。注意：非 Debug 的会话与 CSRF Cookie 仍标记为 Secure，因此普通 HTTP 下登录并不可靠；健康检查可带 `X-Forwarded-Proto: https`，完整登录验收请使用 HTTPS，或使用独立临时数据库并以 `DJANGO_DEBUG=true` 运行本地服务。

### 升级

升级前先备份，再拉取新代码并执行：

```bash
docker compose up -d --build
```

入口脚本会自动执行数据库迁移。

### 备份与恢复

一致性备份时短暂停止应用：

```bash
docker compose stop app
docker run --rm -v meeting-progress-tracker_tracker_data:/data -v "$PWD":/backup alpine tar czf /backup/tracker-data.tar.gz -C /data .
docker compose start app
```

恢复到空数据卷：

```bash
docker compose down
docker volume create meeting-progress-tracker_tracker_data
docker run --rm -v meeting-progress-tracker_tracker_data:/data -v "$PWD":/backup alpine sh -c 'cd /data && tar xzf /backup/tracker-data.tar.gz'
docker compose up -d
```

卷名受目录名或 `COMPOSE_PROJECT_NAME` 影响，可先运行 `docker volume ls` 确认实际名称。

## 本地开发

```bash
UV_CACHE_DIR=/tmp/meeting-progress-uv-cache uv sync --extra dev
mkdir -p data staticfiles
uv run python manage.py migrate
ADMIN_USERNAME=admin ADMIN_PASSWORD=dev-password uv run python manage.py bootstrap_admin
uv run python manage.py runserver
```

测试：

开发测试需要 Node.js 18+（仅用于执行原生 JavaScript 行为测试，不是应用运行依赖）。`pytest` 会自动运行仓库内的 `tests/js/app.test.cjs`；该测试使用 Node 内建工具和确定性 DOM/FormData 适配器，不需要浏览器下载、npm 包或在线资源。也可以单独执行 `node --test tests/js/app.test.cjs`。

```bash
uv run pytest -q
node --test tests/js/app.test.cjs
node --check static/js/app.js
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
git diff --check
```

本地浏览器验收可在独立临时数据库启动 Debug 服务，避免写入 `data/app.sqlite3`；不要向真实 LLM 服务提交测试会议。`auto_parse` 可通过拦截或模拟验证，仓库内的 Node 行为测试覆盖其一次性触发与表单 intent 保留。

## 数据位置

容器内数据库为 `/data/app.sqlite3`，预留上传目录为 `/data/uploads`。API Key 只来自环境变量，不写入数据库和导出文件。
