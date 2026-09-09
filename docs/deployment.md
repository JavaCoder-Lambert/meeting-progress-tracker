# 美国服务器部署

这个配置适合单人使用、小数据量、单台 Linux 服务器。网页服务、解析 worker 和 Caddy 共三个容器；SQLite 位于本机持久卷。业务时间固定为 `Asia/Shanghai`，不会因服务器在美国而把“今日/本周”切换成美国时间。

## 首次部署

服务器安装 Docker Engine 和 Compose v2。为实际域名配置指向服务器公网 IP 的 A 记录；只有服务器具备可用 IPv6 时才配置 AAAA 记录。放行 TCP 80/443，UDP 443 为可选 HTTP/3；数据库和应用的 8000 端口无需公网开放。80/443 不能同时被另一个服务占用，已有反向代理时请使用 README 的同机代理方案。

在服务器仓库目录执行：

```bash
cp .env.server.example .env.server
chmod 600 .env.server
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

填写 `.env.server`：

| 配置 | 填写方式 |
| --- | --- |
| `APP_DOMAIN` | 实际域名，例如 `progress.your-domain.com`，不含 `https://`、端口或路径 |
| `ACME_EMAIL` | 接收证书通知的邮箱 |
| `DJANGO_SECRET_KEY` | 上一步生成的随机字符串，升级时保留原值 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 初始管理员账户，密码至少 12 位；以后在设置页修改密码 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 大模型配置，使用模型服务商提供的对应值 |
| `LLM_TIMEOUT_SECONDS` | 解析总等待上限，默认 180 秒 |
| `PYPI_INDEX_URL` | 美国服务器默认 `https://pypi.org/simple` |
| `TRACKER_DATA_VOLUME` | 持久卷名称，常规升级必须保留；正式恢复时才改为新卷名 |
| `IMAGE_TAG` / `APP_VERSION` | 发布时两项都填完整 Git commit；前者选择镜像，后者展示版本。默认 `server` / `development` 兼容旧配置 |
| `TRACKER_PROXY_SUBNET` / `TRACKER_PROXY_IP` | 专用代理网络及其中 Caddy 的固定 IPv4，默认 `172.30.7.0/29` / `172.30.7.2`；与宿主网络冲突时同时修改，应用只信任该 Caddy 地址 |

以下使用独立的服务器 Compose 文件，不要把它与本机 `docker-compose.yml` 合并。先校验，不打印展开后的密钥：

```bash
docker compose --env-file .env.server -f docker-compose.server.yml config --quiet
test -z "$(git status --porcelain --untracked-files=all)"
export IMAGE_TAG="$(git rev-parse HEAD)"
export APP_VERSION="$IMAGE_TAG"
docker compose --env-file .env.server -f docker-compose.server.yml build app
docker compose --env-file .env.server -f docker-compose.server.yml up -d --no-build --wait
docker compose --env-file .env.server -f docker-compose.server.yml ps
```

把输出的 commit 同时保存到 `.env.server` 的 `IMAGE_TAG` 和 `APP_VERSION`，再执行 `python3 docker/scripts/operations.py record-release --commit "$IMAGE_TAG"`。它核对实际容器、镜像标签和环境后写入独立的 `.env.server.release.json`；原环境文件不由脚本改写。已有本地数据时，先按[迁移指南](migration.md)恢复到新卷，再运行首次 `up`；不要先启动空数据库再尝试覆盖。

随后访问 `https://你的域名`。Caddy 会申请并自动续期证书，HTTP 自动跳转 HTTPS；证书保存在 `caddy_data` 卷。第一次申请可能需要短暂等待，查看 `docker compose --env-file .env.server -f docker-compose.server.yml logs --tail=80 caddy` 定位 DNS、端口或证书错误。不要将 Caddy 后的 app 端口直接暴露公网，因为 Django 信任由 Caddy 设置的 HTTPS 转发头。

app 启动会执行迁移和首次管理员初始化；worker 使用相同数据库，在 app 健康后启动。保持单个 worker 和一个 app 实例，数据库卷必须位于服务器本机磁盘，不放 NFS/共享网络文件系统。镜像只使用随项目发布的静态文件，无外部字体或 CDN。

`/accounts/login/` 与 `/admin/login/` 共享同一客户端的失败计数：五分钟内失败五次后拒绝继续提交，成功登录清零，后台管理能力保持可用。Caddy 在独立 `login_proxy` 网络中覆盖单值 `X-Tracker-Client-IP`，应用只接受固定 Caddy 地址的该头，不使用用户提供的 `X-Forwarded-For`。本地直连默认不信代理头。当前 Caddy 必须直面客户端；若另加 CDN/负载均衡，需单独核实可信链路，否则会按前级代理地址计数。限流保存在单个 Gunicorn worker 的内存中，重启会清空；不要增加 app 或 Gunicorn worker 数量而继续假定共享限流。

## 上线验收

1. 访问 HTTP 域名会跳转 HTTPS，HTTPS 证书有效，能登录、退出和重新登录。
2. `ps` 显示 app、worker 健康；`https://你的域名/health/` 与 `/ready/` 返回 200。`/ready/` 会只读检测业务表和解析任务表是否可访问；worker 健康检查使用 `python manage.py run_parse_worker --check`。
3. 新会议开始解析后可以进入项目页，再回会议页查看同一次解析；确认后只入库一次。模型仍然需要生成时间，但不会占用网页请求。
4. 从中国办公网络检查项目、工作计划和任务页面；浏览器第二次加载静态文件会使用指纹缓存，CSS/JS 支持 gzip 压缩。
5. 检查今日、本周计划按中国日期显示，执行一次在线备份和独立路径恢复演练。
6. 重启 app/worker 后已有项目仍存在；日志不出现数据库锁错误。解析进程若被中断，过期任务会标记失败，可由你决定重试。

`python manage.py check --deploy` 可用来补充检查。默认不将整个域名的所有子域纳入 HSTS，也不加入浏览器预加载名单，因此可能出现 `security.W005` / `security.W021`；这是为了不改变其他子域的 HTTPS 行为。正式服务器不应出现 `security.W008`（未启用 HTTPS 跳转）。

## 备份与升级

宿主机需 Python 3.11+。备份命令不展开密钥；升级预检会在内存中消费 Compose 的有效配置并传入隔离容器，日志和清单不输出密钥值。备份目录由当前部署用户持有且权限为 700，文件为 600：

```bash
install -d -m 700 /srv/tracker-backups
python3 docker/scripts/operations.py backup --output-dir /srv/tracker-backups
```

脚本输出一个备份目录，包含 `backup.sqlite3` 和 `manifest.json`。宿主副本经过 SHA256 和 SQLite 完整性校验；清单记录实际容器镜像 ID、保留的旧镜像标签、版本、卷名和时间。密钥及 `.env.server` 不进入清单。脚本只删除本次创建且成功导出的容器内临时快照，既有手动备份保持原样。在线快照包含已提交事务；升级和迁移的最终快照必须停写后生成。

升级前使用已发布 commit 的干净构建目录，保持原数据卷、原密钥和模型配置。`IMAGE_TAG` 必须是该目录 HEAD 的完整 commit，`APP_VERSION`、构建上下文与镜像版本标签必须一致；未提交的受跟踪变更（即使路径已命中忽略）和普通未跟踪文件都会阻止升级。被忽略的本地工具/文档不等于已发布代码：先确认 `git ls-files --error-unmatch docker/scripts/operations.py` 成功，不绕过忽略规则把本地文件硬塞进提交。旧版没有工具入口时使用[迁移指南](migration.md)的独立新版工具目录。

`upgrade` 在任何停机前检查发布状态漂移、备份目录所有权/700 权限与写入能力、状态文件写入能力、宿主备份目录和数据卷空间（两倍数据库及 WAL 大小加 64 MiB 余量），随后构建镜像。目标镜像使用无网络、只读文件系统、仅 `/tmp` 临时空间的容器校验生产配置，覆盖 entrypoint，不挂正式数据卷，不执行 bootstrap 或 migrate；错误只列字段名。构建后再次检查空间，全部通过才停 app、再停 worker（允许 210 秒退出），用旧镜像保存停写快照，再以预检的不可变镜像 ID 同时启动 app/worker 并等待健康。此流程只读取 Git 状态，不改源码、环境文件或调用模型：

```bash
NEW_COMMIT="$(git rev-parse HEAD)"
python3 docker/scripts/operations.py upgrade \
  --image-tag "$NEW_COMMIT" --app-version "$NEW_COMMIT" \
  --output-dir /srv/tracker-backups
```

成功并核对实际容器后，脚本原子写入 `.env.server.release.json`，只记录镜像 ID、commit、镜像名、卷名和时间。立即把两项新 commit 保存到 `.env.server`，清理 shell 中过时的对应值，再运行 `python3 docker/scripts/operations.py check-release`；未同步的旧环境会被明确拒绝。脚本保留 `meeting-progress-tracker:rollback-镜像ID前12位`；升级前快照标记为 `pre-upgrade`，不参与自动保留清理。预检/构建失败不停止服务，停写后快照失败不启动新版本；新版本 `up --wait` 失败则分别尽力停止 app、worker，并逐项报告结果，保留原启动错误。任何停止失败都表示尚不能确认全部停写，需立即核对容器。不会自动恢复旧服务写入。旧版本必须已具备 `backup_db` / `restore_db`；终端打印实际快照位置，按下面步骤恢复。

修改非版本配置后，使用 `python3 docker/scripts/operations.py check-release && docker compose --env-file .env.server -f docker-compose.server.yml up -d --no-build --force-recreate --wait` 让 app/worker 同时获得配置。直接绕过检查运行 Compose 仍会采用环境文件中的值，脚本无法拦截外部命令。不要使用 `down -v`。空间检查是当时的余量检查，不预演数据库迁移，也不能排除随后磁盘写满或端口/证书故障；保留升级前快照和配置，真实发布仍要完成下面验收。

## 恢复与回退

恢复会拒绝任何已存在的目标卷，也不会覆盖已存在的数据库。旧版本代码必须配升级前旧快照，不能直接连接已被新版本迁移过的数据库。恢复前强制核对目标镜像完整 OCI commit 与快照版本：接受完整相等或至少 7 位十六进制前缀，拒绝 `unknown`、无标签或不匹配；`--app-version` 不能改写快照的版本。跨架构重建仅允许镜像 ID 不同，源 commit 必须相同。以下在同一主机回退；把 `BUNDLE` 换成升级时打印的真实目录：

若回退到 `d0be442` 等尚无 `/ready/` 的旧版，下面恢复命令添加 `--compose-file "$MIGRATION_COMPOSE"`，启动命令的 `-f` 也改为迁移时保留的 `$MIGRATION_COMPOSE`（探针为 `/health/`）；沿用同一 `COMPOSE_PROJECT_NAME`。不要用新版探针判断旧版故障，详见[迁移说明](migration.md)。

```bash
BUNDLE=/srv/tracker-backups/tracker-ops-实际升级前目录
OLD_TAG="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["rollback_image"].split(":")[-1])' "$BUNDLE/manifest.json")"
OLD_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["app_version"])' "$BUNDLE/manifest.json")"
RESTORE_VOLUME="meeting-progress-tracker_restore_$(date -u +%Y%m%dT%H%M%SZ)"
docker image inspect "meeting-progress-tracker:$OLD_TAG" --format '{{.Id}}'
docker compose --env-file .env.server -f docker-compose.server.yml stop app
docker compose --env-file .env.server -f docker-compose.server.yml stop worker
python3 docker/scripts/operations.py restore "$BUNDLE" --volume "$RESTORE_VOLUME"
IMAGE_TAG="$OLD_TAG" APP_VERSION="$OLD_VERSION" TRACKER_DATA_VOLUME="$RESTORE_VOLUME" \
  docker compose --env-file .env.server -f docker-compose.server.yml up -d --no-build --wait
```

恢复脚本经标准输入把 600 权限的宿主备份传入容器，容器内使用 700 临时目录和 600 文件，调用 `restore_db` 后清除临时文件；无需让文件对其他用户可读。恢复期间不启动应用、不跑迁移；首次 `up` 才运行匹配版本的迁移。

验收后把 `IMAGE_TAG`、`APP_VERSION`、`TRACKER_DATA_VOLUME` 的上述值写回 `.env.server`，执行 `python3 docker/scripts/operations.py record-release --commit "$OLD_VERSION"` 显式登记回退（旧版仍传对应迁移 Compose），随后 `check-release`。`record-release` 要求完整 commit 与容器版本标签及有效环境一致；无版本标签的旧镜像不能用猜测值登记，需按迁移指南从已核实源码构建带标签的同版本镜像。原数据卷仍然保留。升级后有新写入时，回退旧快照会丢失这段修改；先为新卷保存停写快照并记录待核对数据，不清理 rollback 标签。

异地备份、调度和日常验收详见[运维手册](operations.md)，本地现有数据搬迁详见[迁移指南](migration.md)。

当前 `/data/uploads` 只是预留目录，没有附件上传功能。数据库备份覆盖当前全部业务数据；未来启用附件后需要为上传目录补独立备份。

## 配置依据

- [Docker Compose 启动依赖](https://docs.docker.com/compose/how-tos/startup-order/)：worker 等待 app 健康。
- [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https) 与 [反向代理头](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)：域名证书、HTTPS 跳转和可信代理边界。
- [Django 安全代理设置](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-proxy-ssl-header) 与 [WhiteNoise Django 集成](https://whitenoise.readthedocs.io/en/stable/django.html)：生产静态压缩、指纹缓存。
- [Python SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup)：在线一致性快照。
