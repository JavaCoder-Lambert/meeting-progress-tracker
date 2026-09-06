# 会议进度台

面向个人项目管理者的会议记录与项目进度工具。将钉钉群消息或会议记录粘贴进系统，大模型生成结构化草稿，人工确认后形成任务、风险、里程碑和周报。

## 功能

- 单管理员登录
- 项目计划：手动维护阶段、里程碑、任务归属和风险，8 周时间线展示交付节奏
- 工作计划：今日、本周、下周、待安排视图，独立安排日期与截止日期，往期未完成持续显示
- 会议收件箱：按未解析、解析失败、待确认、已入库展示下一步，可从失败处重试
- 新会议默认“保存并开始解析”，也可仅保存原文后再解析
- 防丢暂存：录入自动恢复浏览器暂存，审阅自动保存到服务器；补建项目或人员后回到原草稿
- AI 草稿异常优先：项目缺失、日期异常、人员未匹配、疑似重复默认展开；支持只看异常、批量接受推荐或批量忽略异常项
- 任务详情中的一次进展更新会记录本次完成、下一步、当前说明、状态、进度和截止日期，并同步到时间线与周报
- 首页行动台聚合待确认、逾期、本周到期、长期未更新和未解决风险，并按负责人生成可复制的钉钉跟进文案
- 周报提供预览与 Markdown 标签页，支持一键复制
- `/` 聚焦任务搜索，`n` 新建会议；在输入框、可编辑区域或使用组合/修饰键时不会劫持按键
- JSON 与 CSV ZIP 导出
- SQLite 持久化和 Docker Compose 部署
- 后台 AI 解析：提交后可继续查看项目，刷新或关闭页面不丢失；失败可重试
- 美国服务器部署模板：Caddy 自动 HTTPS、独立解析 worker、静态文件压缩与版本缓存、在线数据库备份

## 本机 Docker 启动

使用自有服务器和域名时直接按 [服务器部署手册](docs/deployment.md) 操作；它使用独立的 `docker-compose.server.yml`，已包含 HTTPS。

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

AI 解析由独立 `worker` 处理，页面提交后立即返回，刷新或暂时离开页面不会取消已排队的请求。默认模型 deadline 为 `LLM_TIMEOUT_SECONDS=180` 秒，首次请求和最多一次格式修复共享此上限；网页进程不再需要等待模型。超时或 worker 中断后会显示失败，重试由你决定，避免后台反复产生模型费用。`GUNICORN_TIMEOUT` 只保护网页进程，与模型等待时间独立。若仍希望缩短解析完成时间，可选择同一服务商的轻量模型。

### 域名与 HTTPS

推荐使用 [Caddy 服务器栈](docs/deployment.md)，只需填写域名和证书通知邮箱。若已有同机 Nginx，可继续使用本机 Compose：代理转发到 `.env` 的 `APP_PORT`，例如 `http://127.0.0.1:18080`，并正确设置 `Host`、`X-Forwarded-For`、`X-Forwarded-Proto`。生产环境必须使用 HTTPS，并将完整 HTTPS 地址写入 `CSRF_TRUSTED_ORIGINS`。解析已在后台执行，不需要为解析请求额外拉长代理超时。

只在本机通过 HTTP 直接验收容器时，可临时设置 `DJANGO_SECURE_SSL_REDIRECT=false`；绑定域名后应恢复为 `true`。注意：非 Debug 的会话与 CSRF Cookie 仍标记为 Secure，因此普通 HTTP 下登录并不可靠；健康检查可带 `X-Forwarded-Proto: https`，完整登录验收请使用 HTTPS，或使用独立临时数据库并以 `DJANGO_DEBUG=true` 运行本地服务。

### 升级

升级前先备份，再拉取新代码并执行：

```bash
docker compose stop worker app
docker compose run --rm --no-deps app python manage.py backup_db
git pull --ff-only
docker compose up -d --build --wait
```

只有 app 的入口脚本会自动执行数据库迁移，worker 会等 app 健康后开始工作。上述命令模式不会迁移数据库。从没有 `backup_db` 的旧版首次升级时，先拉代码并 `docker compose build app`，再用新镜像执行上述 `run ... backup_db` 保存原库，最后 `up`。

### 备份与恢复

在线备份包含会议、计划、任务、登录账户及解析队列，使用 SQLite backup API 读取已提交数据，并验证完整性。命令输出备份路径和 SHA256；不会覆盖已有备份，也不会导出 `.env` 中的 API Key。

```bash
docker compose exec -T app python manage.py backup_db
docker compose cp app:/data/backups/命令输出的文件名.sqlite3 ./backup.sqlite3
```

备份默认存在数据卷 `/data/backups`，请定期复制到服务器以外的可信位置；同一磁盘上的备份不能防止磁盘故障。当前没有上传附件功能，预留的 `/data/uploads` 不属于数据库备份。备份文件包含业务数据和账户密码哈希，应妥善保管。

恢复命令只允许目标不存在，不会覆盖现有数据库；可先在独立路径演练：

```bash
docker compose exec -T app python manage.py restore_db /data/backups/命令输出的文件名.sqlite3 --target /data/recovery-check/app.sqlite3
```

正式恢复时先停止 app 和 worker，并恢复到新数据卷；按 [恢复与回退步骤](docs/deployment.md#恢复与回退) 切换，旧数据卷仍保留。

## 本地开发

```bash
UV_CACHE_DIR=/tmp/meeting-progress-uv-cache uv sync --extra dev
mkdir -p data staticfiles
uv run python manage.py migrate
ADMIN_USERNAME=admin ADMIN_PASSWORD=dev-password uv run python manage.py bootstrap_admin
uv run python manage.py runserver
```

在第二个终端启动后台解析进程：`uv run python manage.py run_parse_worker`。两个进程必须使用相同 `DATA_DIR` 和大模型环境变量。本地 `uv run` 不会自动读取 `.env`，可用 `uv run --env-file .env ...` 为两个命令明确加载配置。

测试：

开发测试需要 Node.js 18+（仅用于执行原生 JavaScript 行为测试，不是应用运行依赖）。JavaScript 测试使用 Node 内建工具和确定性 DOM/FormData 适配器，不需要浏览器下载、npm 包或在线资源。执行 `node --test tests/js/*.test.cjs` 可同时检查页面交互、会议录入暂存和审阅自动保存。

```bash
uv run pytest -q
node --test tests/js/*.test.cjs
node --check static/js/app.js
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
git diff --check
```

本地浏览器验收可在独立临时数据库启动 Debug 服务，避免写入 `data/app.sqlite3`；不要向真实 LLM 服务提交测试会议。可模拟后台 worker 验证入队、页面离开与刷新后的状态恢复；仓库内的 Node 行为测试覆盖重复点击、断网重连和表单 intent 保留。

## 计划怎么使用

1. 在项目页添加阶段（例如数据改造、开发联调、验收上线），设置各阶段起止日期。
2. 添加任务并选择所属阶段，设置任务截止日期；里程碑记录内测、验收、上线等关键节点。
3. 在「工作计划 → 待安排」里把任务安排到今天或下周一，也可指定日期。安排只改变推进日期，不会修改交付截止日期或把未更新的任务标成已有进展。
4. 在任务详情记录实际进展；项目页切到「计划时间线」检查未来 8 周。风险解决后在风险记录中关闭，可从项目页展开关闭历史重新查看。

项目总体进度保留人工判断；任务完成数单独展示，避免用任务数量平均值代替项目真实完成度。会议列表、任务列表和工作计划分别按 20、30、25 条分页。

## 数据位置

容器内数据库为 `/data/app.sqlite3`，预留上传目录为 `/data/uploads`。API Key 只来自环境变量，不写入数据库和导出文件。

## 暂存与补建资料

- **还在录入会议**：标题、日期和原文自动暂存在当前浏览器，按登录账号隔离。切换菜单或刷新后，回到“快速记录”会恢复；只有服务器确认会议保存成功才清理对应暂存。尚未提交的输入不会跨设备同步，清除浏览器数据会删除它；共用电脑时请主动清空。
- **已解析、正在审阅**：修改后约 0.7 秒自动暂存到服务器，也可点“暂存草稿”。项目没选、日期未填完同样可暂存，不会提前创建正式任务。会议列表中的“继续暂存草稿”可恢复上次修改，使用同一服务器和账号换设备也能继续。
- **缺少项目或人员**：每项任务上有“新建项目并返回本项”“新增人员并返回本项”，先保存整份草稿，再预填识别到的名称；新建成功后返回并选中对应资料。顶部入口用于普通补建，不自动改变某项任务的选择。
- **断网或多标签冲突**：暂存失败时保留页面内容并阻止菜单跳转；重新连接后可手动重试。旧标签不能覆盖新版。出现冲突或登录过期时先复制当前修改，再打开最新草稿核对。关闭页面时若有未确认保存的修改，浏览器会尽可能提示；强制退出浏览器/电脑断电不能保证保留尚未到达服务器的审阅输入。
- **不启用 JavaScript**：没有自动暂存；手动“暂存草稿”和“新建后返回”仍可用。

数据库迁移 `0004_draft_review_stash` 只新增审阅暂存字段，不改动原有会议原文和 AI 解析结果。升级前按上面的备份步骤备份，再重新构建容器；服务端暂存包含在 SQLite 备份中。
