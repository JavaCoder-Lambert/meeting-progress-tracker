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

以下使用独立的服务器 Compose 文件，不要把它与本机 `docker-compose.yml` 合并。先校验，不打印展开后的密钥：

```bash
docker compose --env-file .env.server -f docker-compose.server.yml config --quiet
docker compose --env-file .env.server -f docker-compose.server.yml up -d --build --wait
docker compose --env-file .env.server -f docker-compose.server.yml ps
```

随后访问 `https://你的域名`。Caddy 会申请并自动续期证书，HTTP 自动跳转 HTTPS；证书保存在 `caddy_data` 卷。第一次申请可能需要短暂等待，查看 `docker compose --env-file .env.server -f docker-compose.server.yml logs --tail=80 caddy` 定位 DNS、端口或证书错误。不要将 Caddy 后的 app 端口直接暴露公网，因为 Django 信任由 Caddy 设置的 HTTPS 转发头。

app 启动会执行迁移和首次管理员初始化；worker 使用相同数据库，在 app 健康后启动。保持单个 worker 和一个 app 实例，数据库卷必须位于服务器本机磁盘，不放 NFS/共享网络文件系统。镜像只使用随项目发布的静态文件，无外部字体或 CDN。

## 上线验收

1. 访问 HTTP 域名会跳转 HTTPS，HTTPS 证书有效，能登录、退出和重新登录。
2. `ps` 显示 app、worker 健康；`https://你的域名/health/` 返回 200。worker 健康检查使用 `python manage.py run_parse_worker --check`。
3. 新会议开始解析后可以进入项目页，再回会议页查看同一次解析；确认后只入库一次。模型仍然需要生成时间，但不会占用网页请求。
4. 从中国办公网络检查项目、工作计划和任务页面；浏览器第二次加载静态文件会使用指纹缓存，CSS/JS 支持 gzip 压缩。
5. 检查今日、本周计划按中国日期显示，执行一次在线备份和独立路径恢复演练。
6. 重启 app/worker 后已有项目仍存在；日志不出现数据库锁错误。解析进程若被中断，过期任务会标记失败，可由你决定重试。

`python manage.py check --deploy` 可用来补充检查。默认不将整个域名的所有子域纳入 HSTS，也不加入浏览器预加载名单，因此可能出现 `security.W005` / `security.W021`；这是为了不改变其他子域的 HTTPS 行为。正式服务器不应出现 `security.W008`（未启用 HTTPS 跳转）。

## 备份与升级

平时可以不停止服务进行一致性备份：

```bash
docker compose --env-file .env.server -f docker-compose.server.yml exec -T app python manage.py backup_db
docker compose --env-file .env.server -f docker-compose.server.yml cp app:/data/backups/输出的文件名.sqlite3 ./backup.sqlite3
```

命令会校验 SQLite 完整性并输出 SHA256。使用操作系统计划任务定期运行，并将备份复制到服务器之外的可信存储；至少保留一份升级前备份。默认备份在 `/data/backups`，不会自动删除旧备份。密码哈希和业务内容均包含在内，请限制备份文件访问权限。

升级时先停止解析 worker（默认给予 210 秒完成退出），网页短暂停止写入后保存升级前快照：

```bash
docker compose --env-file .env.server -f docker-compose.server.yml stop worker app
docker compose --env-file .env.server -f docker-compose.server.yml run --rm --no-deps app python manage.py backup_db
git pull --ff-only
docker compose --env-file .env.server -f docker-compose.server.yml up -d --build --wait
```

命令模式不会自动迁移数据库；只有默认 app 启动才迁移。若从旧版升级且镜像尚没有 `backup_db`，先拉代码并 `build app`，再使用新镜像的上述 `run ... backup_db` 命令备份，最后 `up`。新增迁移不能只靠回退代码撤销；需要回退时使用对应旧版本镜像与升级前备份。

修改 `.env.server` 后使用 `up -d --force-recreate --wait` 让 app 和 worker 同时获得新配置。不要使用 `down -v`，它会删除持久卷。

## 恢复与回退

恢复到新卷能保留原数据库。先把需要恢复的备份放到服务器仓库目录，名为 `backup.sqlite3`，记录当前 `TRACKER_DATA_VOLUME` 的值，然后停止写入：

```bash
docker compose --env-file .env.server -f docker-compose.server.yml stop worker app
```

将 `.env.server` 的 `TRACKER_DATA_VOLUME` 改为一个从未使用的卷名，例如 `meeting-progress-tracker_restore_20260905`。用同一版或更新版代码恢复（回退旧版须使用该版本产生的备份）：

```bash
docker compose --env-file .env.server -f docker-compose.server.yml run --rm --no-deps -v "$PWD/backup.sqlite3:/backup/app.sqlite3:ro" app python manage.py restore_db /backup/app.sqlite3
docker compose --env-file .env.server -f docker-compose.server.yml up -d --wait
```

`restore_db` 在写入前验证数据库，目标已存在时拒绝覆盖。镜像会把新数据卷初始化为应用用户可写，启动 app 后执行必要迁移。核对项目、计划、会议和账户后再决定何时清理旧卷；如需恢复原状态，停止 app/worker，将卷名改回记录值并启动对应版本即可。

当前 `/data/uploads` 只是预留目录，没有附件上传功能。数据库备份覆盖当前全部业务数据；未来启用附件后需要为上传目录补独立备份。

## 配置依据

- [Docker Compose 启动依赖](https://docs.docker.com/compose/how-tos/startup-order/)：worker 等待 app 健康。
- [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https) 与 [反向代理头](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)：域名证书、HTTPS 跳转和可信代理边界。
- [Django 安全代理设置](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-proxy-ssl-header) 与 [WhiteNoise Django 集成](https://whitenoise.readthedocs.io/en/stable/django.html)：生产静态压缩、指纹缓存。
- [Python SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup)：在线一致性快照。
