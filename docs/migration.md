# 将本地现有数据迁到美国服务器

这次迁移保留项目、计划、任务、会议、AI 草稿、解析任务、用户和密码哈希。当前业务数据都在 SQLite；`uploads` 仅为预留目录。若自行增加了附件功能，需另外迁移附件。服务器仍按 `Asia/Shanghai` 计算日期。

本批新增 `0006_project_weekplans` 和 `0007_meeting_history`，分别创建周计划/条目、风险跟进/会议更正表。迁移只新增空表，不推断或改写旧会议历史；正常 SQLite 整库备份会一起保留这些数据。升级前务必使用维护脚本校验备份；旧代码回退必须配对应旧快照恢复到全新数据卷，不对带有新写入的库直接降级代码。JSON/CSV 也包含新增集合，但它们用于查看导出，不替代完整数据库备份与 `.env` 单独迁移。

## 1. 提前准备目标环境

按[部署手册](deployment.md)安装 Docker Engine、Compose v2、Python 3.11+，准备域名、DNS、80/443 端口和磁盘空间。先安排维护窗口，等解析任务结束，避免迁移正在进行的模型请求。旧端和新端不能同时接受业务写入。

记录本地实际部署 commit、数据库位置或卷名、当前域名，以及项目、任务、会议的代表性记录。容器源需在停机前核对实际镜像 OCI `org.opencontainers.image.revision`，生成的快照须记录完整 commit 或可与目标完整 commit 匹配的至少 7 位十六进制前缀。无标签源镜像会产生 `unknown` 清单；自动恢复拒绝该清单，不能靠目标镜像补标签、修改清单或手写 `--app-version` 推断源版本。先保留现场快照并独立核实原运行源码，准备有已验证版本依据的新快照后再继续；这类缺失来源信息的历史备份不自动迁移。Apple Silicon 本机和 x86 美国服务器应各自构建源 commit，不直接把 ARM 镜像当 x86 镜像运行。若本地有未发布改动，先保存可复现的源码版本，再决定迁移使用的代码。

应用源码和运维工具分开放置。例如旧版 `d0be442eae1296f9d52270873a07147c4f81043b` 已包含 `backup_db` / `restore_db`，但没有 `operations.py`，旧 Compose 的固定镜像名也不消费 `IMAGE_TAG`。因此不要在旧源码目录照搬新版工具命令。以下通用路径让新版工具负责编排，实际恢复和启动使用源 commit 构建的旧应用镜像。

在目标机将本次发布的完整代码放到 `OPS_DIR`（必须包含 `docker/scripts/operations.py`、新版服务器 Compose 和 Caddy 配置），再从其 Git 历史取出只用于构建的旧源码。`SOURCE_COMMIT` 应填写核实过的实际源 commit；这里用已确认支持的旧版演示：

```bash
OPS_DIR=/srv/meeting-progress-tracker-tools
SOURCE_COMMIT=d0be442eae1296f9d52270873a07147c4f81043b
SOURCE_DIR="/srv/meeting-progress-tracker-source-$SOURCE_COMMIT"
export COMPOSE_PROJECT_NAME=meeting-progress-tracker-us
test -f "$OPS_DIR/docker/scripts/operations.py"
git -C "$OPS_DIR" ls-files --error-unmatch docker/scripts/operations.py
git -C "$OPS_DIR" cat-file -e "$SOURCE_COMMIT:core/management/commands/backup_db.py"
git -C "$OPS_DIR" cat-file -e "$SOURCE_COMMIT:core/management/commands/restore_db.py"
git -C "$OPS_DIR" worktree add --detach "$SOURCE_DIR" "$SOURCE_COMMIT"
```

任一检查失败就停止本流程，不运行后续停写、恢复或启动命令。若运维脚本/文档仅存在于本地未跟踪或忽略路径，它们尚未随 commit 发布；使用已包含这些文件的正式工具版本，不用强制添加忽略文件掩盖缺失。真正不含 `backup_db` / `restore_db` 的更早版本不在自动迁移路径内，须先另行验证同版本 SQLite backup API 导出/恢复方案。新旧目录及 Compose 项目名后续保持一致。

目标 `.env.server` 权限为 600，保留原 `DJANGO_SECRET_KEY`、模型 API key、`LLM_BASE_URL`、`LLM_MODEL`。不要重新生成原密钥。管理员账户和已更改的密码会随数据库迁移；环境中的初始密码不能用来覆盖已存在账户。环境文件需要单独通过受信任的 SSH/SFTP 或密码管理器转移，不放进备份清单、Git、聊天或普通邮件。

域名变化时，在目标 `OPS_DIR/.env.server` 更新 `APP_DOMAIN`、`ACME_EMAIL`；服务器 Compose 会据新域名产生 `DJANGO_ALLOWED_HOSTS` 和 `CSRF_TRUSTED_ORIGINS`。若用自有代理配置，则对应更新两项及 HTTPS 转发头；不要把 8000 端口公开。选择全新的 `TRACKER_DATA_VOLUME`。直接从 `SOURCE_DIR` 构建旧应用并显式打源 commit 标签，不使用旧 Compose 的固定镜像名；旧 Dockerfile 没有版本参数时仍由构建标签准确记录版本：

```bash
test "$(git -C "$SOURCE_DIR" rev-parse HEAD)" = "$SOURCE_COMMIT"
test -z "$(git -C "$SOURCE_DIR" status --porcelain --untracked-files=all)"
docker build --build-arg PYPI_INDEX_URL=https://pypi.org/simple \
  --label "org.opencontainers.image.revision=$SOURCE_COMMIT" \
  -t "meeting-progress-tracker:$SOURCE_COMMIT" "$SOURCE_DIR"
docker image inspect "meeting-progress-tracker:$SOURCE_COMMIT" --format '{{.Id}}'
```

把目标 `OPS_DIR/.env.server` 的 `IMAGE_TAG`、`APP_VERSION` 都设为 `SOURCE_COMMIT` 的实际值。旧应用没有新增的 `/ready/`，迁移期间从新版服务器 Compose 生成一个仅把应用探针改为旧版 `/health/` 的配置；内容不含展开后的密钥。此文件仍消费镜像标签和卷名，应用代码完全来自刚构建的旧镜像：

```bash
MIGRATION_COMPOSE="$OPS_DIR/docker-compose.migration.yml"
python3 - "$OPS_DIR/docker-compose.server.yml" "$MIGRATION_COMPOSE" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
with Path(sys.argv[2]).open("x") as output:
    output.write(text.replace("http://127.0.0.1:8000/ready/", "http://127.0.0.1:8000/health/"))
PY
docker compose --env-file "$OPS_DIR/.env.server" -f "$MIGRATION_COMPOSE" config --quiet
```

生成的 `docker-compose.migration.yml` 是本机操作文件，已在 Git/Docker 忽略规则中，保留用于旧版 `/health/` 验收与回退。不要提交或覆盖旧应用源码来补 `/ready/`。

还不要运行 `up` 或普通 `compose run`，避免提前创建恢复目标卷。若同时升级，先完成同版本迁移验收，再切回新版 `docker-compose.server.yml` 运行独立升级；两次操作沿用 `COMPOSE_PROJECT_NAME=meeting-progress-tracker-us`。

## 2. 本地停写并导出最后快照

源机也准备一份新版工具目录 `LOCAL_TOOLS`，旧应用目录保留为 `LOCAL_SOURCE`；无需在旧目录寻找不存在的脚本，也不需要更新或重建正在运行的旧应用。使用原来的 Compose 项目名称与环境文件。若原命令带 `-p`，请在源机设置相同的 `COMPOSE_PROJECT_NAME`；不要误用上面目标机的项目名。默认本地配置执行：

```bash
LOCAL_SOURCE=/实际旧应用仓库
LOCAL_TOOLS=/实际新版工具仓库
test -f "$LOCAL_TOOLS/docker/scripts/operations.py"
cd "$LOCAL_SOURCE"
docker compose --env-file .env -f docker-compose.yml stop app
docker compose --env-file .env -f docker-compose.yml stop worker
python3 "$LOCAL_TOOLS/docker/scripts/operations.py" --env-file .env --compose-file docker-compose.yml \
  backup --output-dir "$PWD/migration-backups"
```

记下输出的目录。`manifest.json` 包含实际容器的镜像、卷和版本，不依赖当前源码目录猜测；如版本为 `unknown`，保留备份并停止自动恢复流程，按第 1 步补齐已验证的源版本与快照依据。备份完成后本地 app/worker 保持停止，禁止本地继续解析、编辑或确认草稿。此命令要求源镜像已含 `backup_db`。

如果本地使用 `.venv` 直接运行 Django，先停止原来的网页进程、解析 worker 和自动重启服务，使用原运行环境、原 `DATA_DIR` 生成一致性快照：

```bash
install -d -m 700 "$PWD/migration-backups"
# 下面命令需沿用本地启动应用时的环境变量；DATA_DIR 指向实际本地数据目录。
DATA_DIR=/实际本地数据目录 .venv/bin/python manage.py backup_db "$PWD/migration-backups/native.sqlite3"
git rev-parse HEAD
```

记录命令输出的 SHA256 和实际运行代码的 commit，不要从正在使用中的 `app.sqlite3` 直接复制，也不要漏掉 WAL 中已提交的数据。原生快照默认也是 600 权限。

## 3. 转移并在空卷恢复

通过已核实身份的 SSH/SFTP 将完整备份目录传到目标机私有目录，例如 `/srv/tracker-migration`；原生部署传 `native.sqlite3` 并另外保管原机记录的 SHA256 和 commit。示例只在你填写实际主机后执行：

```bash
# 在目标机创建私有目录，确保归部署用户所有：
install -d -m 700 /srv/tracker-migration
# 在源机执行，替换下面主机及完整备份目录：
scp -pr /实际路径/tracker-ops-实际目录 部署用户@美国主机:/srv/tracker-migration/
```

确认目录权限为 700、文件为 600。回到目标机，沿用第 1 步的目录变量、源 commit 和 Compose 项目名，先恢复再首次启动。`NEW_VOLUME` 必须是从未存在的卷名。新版脚本核对目标 OCI 完整 commit 与原快照版本，并固定镜像 ID；显式 `--app-version` 同样必须匹配，不会替换快照版本。`d0be442` 短记录只能匹配以该前缀开头的完整源 commit，不能匹配任意包含这段文字的版本。若镜像或版本依据缺失会停止，不临时构建当前工具目录的应用：

```bash
NEW_VOLUME=meeting-progress-tracker_us_migration_20260908
python3 "$OPS_DIR/docker/scripts/operations.py" \
  --env-file "$OPS_DIR/.env.server" --compose-file "$MIGRATION_COMPOSE" restore \
  /srv/tracker-migration/tracker-ops-实际目录 \
  --volume "$NEW_VOLUME" --image-tag "$SOURCE_COMMIT" --app-version "$SOURCE_COMMIT"
```

原生 SQLite 快照改用下面命令，校验值必须来自源机原始备份输出：

```bash
python3 "$OPS_DIR/docker/scripts/operations.py" \
  --env-file "$OPS_DIR/.env.server" --compose-file "$MIGRATION_COMPOSE" restore \
  /srv/tracker-migration/native.sqlite3 \
  --sha256 原机记录的64位SHA256 \
  --volume "$NEW_VOLUME" --image-tag "$SOURCE_COMMIT" --app-version "$SOURCE_COMMIT"
```

两条恢复命令都会在写入前校验 SHA256 和 SQLite；以容器应用用户在私密临时目录接收标准输入，然后运行 `restore_db`。无需 `chmod 644` 或 `777`，不使用宿主绑定文件解决权限。若目标卷已存在，脚本拒绝恢复；使用另一个新名字检查重试，不删除原卷。

恢复成功后把目标 `OPS_DIR/.env.server` 的 `TRACKER_DATA_VOLUME`、`IMAGE_TAG`、`APP_VERSION` 分别保存为新卷名、源 commit、源 commit。确认旧端仍停止，然后启动。显式环境变量与文件值保持一致，防止当前 shell 中残留的设置覆盖恢复目标：

```bash
IMAGE_TAG="$SOURCE_COMMIT" APP_VERSION="$SOURCE_COMMIT" TRACKER_DATA_VOLUME="$NEW_VOLUME" \
  docker compose --env-file "$OPS_DIR/.env.server" -f "$MIGRATION_COMPOSE" up -d --no-build --wait
docker compose --env-file "$OPS_DIR/.env.server" -f "$MIGRATION_COMPOSE" ps
python3 "$OPS_DIR/docker/scripts/operations.py" \
  --env-file "$OPS_DIR/.env.server" --compose-file "$MIGRATION_COMPOSE" \
  record-release --commit "$SOURCE_COMMIT"
```

## 4. 验收与域名切换

先检查 `/health/` 返回 200、app/worker 健康；旧版没有 `/ready/` 或设置页版本展示，以镜像标签和 ID 核对版本，升级新版后才检查 `/ready/`。使用原账户和原密码登录，核对源机记录的项目、计划、任务、会议和草稿。用新版工具搭配迁移 Compose 做一次备份和新卷恢复演练。检查“中国今日/本周”日期正确。模型配置可先核对配置完整状态，实际模型请求需由你明确发起并承担服务商费用。

若提前用临时域名验收，切正式域名时更新 `OPS_DIR/.env.server` 的 `APP_DOMAIN`，使用相同的 `--env-file "$OPS_DIR/.env.server" -f "$MIGRATION_COMPOSE"` 执行 `up -d --no-build --force-recreate --wait`，重新检查 HTTPS 和登录。DNS 变更后等待缓存更新；旧地址在整个切换期间保持停写，不能让两端各自编辑后再期望 SQLite 自动合并。账户仍在，浏览器可能需要重新登录。

同版本验收通过后，需要升级时在 `OPS_DIR` 使用新版标准 Compose 和新版本 commit，保留同一项目名与刚恢复的数据卷：

```bash
cd "$OPS_DIR"
NEW_COMMIT="$(git rev-parse HEAD)"
python3 docker/scripts/operations.py upgrade --image-tag "$NEW_COMMIT" \
  --app-version "$NEW_COMMIT" --output-dir /srv/tracker-backups
```

该步骤先完成停机前配置/空间/源码预检，随后才启动新应用并执行迁移；成功后保存新的版本值，改用标准 `docker-compose.server.yml`，运行 `python3 docker/scripts/operations.py check-release` 核对独立发布清单。单纯补写 `.env.server` 不会改动现有容器，也不会自动更新发布清单。

保留 `$MIGRATION_COMPOSE`：若以后按部署手册回退到尚无 `/ready/` 的旧版，恢复工具需显式传入 `--compose-file "$MIGRATION_COMPOSE"`，启动也使用 `-f "$MIGRATION_COMPOSE"`，并保持相同的 `COMPOSE_PROJECT_NAME`，以旧版 `/health/` 探针验收。

确认验收完成和异地备份可恢复后，保留旧机器/旧卷和最终快照一段约定的观察期。不要执行 `down -v`。迁移失败且新端尚无新写入，可停新端、切回旧域名/地址、按旧配置启动旧端。新端已经有新增修改时，先停新端并备份，再确定人工补录或恢复方案，不能直接把旧端放开写入。
