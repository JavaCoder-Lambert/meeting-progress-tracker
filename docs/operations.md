# 日常运维、备份和恢复

适用于单个 app、单个 worker、本机 SQLite 命名卷的独立服务器 Compose。脚本是几个明确命令的组合，需 Python 3.11+ 和 Docker Compose v2；它不安装服务、不修改环境文件、不管理域名，也不调用模型。`backup`、`scheduled`、`upgrade`、`restore` 按环境文件与 Compose 文件解析后的物理路径标识操作范围，共用非阻塞进程锁；相对路径、`..` 或符号链接指向同一组物理文件时不能绕过锁。已有操作时立即失败并提示稍后重试。升级内部备份复用已持有锁，不会自锁。不同物理配置文件即使最终指向同一 Compose project 也不保证互斥，生产上应固定同一组入口路径；外部直接操作 Docker 同样不受此锁保护。

## 常用检查

```bash
docker compose --env-file .env.server -f docker-compose.server.yml ps
python3 docker/scripts/operations.py check-release
docker compose --env-file .env.server -f docker-compose.server.yml logs --tail=80 app worker caddy
curl --fail --silent --show-error https://实际域名/ready/
df -h
```

设置页展示版本和最近一次成功校验的本机快照记录；这不代表文件仍在或异地转存已成功。异地成功以相应备份目录中的 `.offsite-verified.json` 和定时任务退出码为准。日志可能含业务错误信息，转发前检查敏感内容；不要运行会展开整个环境变量的 `docker compose config`，校验使用 `config --quiet`。

## 升级入口

升级命令、失败后的停写处理和旧镜像配旧快照回退统一见[部署手册：备份与升级](deployment.md#备份与升级)。操作前暂停定时任务；`upgrade` 核对源码完整 commit/干净工作树，检查私有目录、可用空间、隔离目标镜像的生产配置，全通过才停写、备份和切换版本。配置错误只报告字段名；运行预检不挂正式数据卷、不执行 migrate/bootstrap、不调用模型。

成功发布独立写入 `<env-file>.release.json`（默认 `.env.server.release.json`），记录 app 与 worker 两个容器各自的实际镜像 ID、OCI commit、`/data` 命名卷及发布时间；只有两者各恰好一个容器且三项身份一致才会记录。升级启动后与 `check-release` 都重复核对两项服务。该文件已被 Git 与 Docker 构建上下文忽略，私有环境文件不自动改写。同步环境文件的版本/卷名后运行 `check-release`；再次升级及日常 `up` 前都要核对，避免旧环境值回退镜像或切错卷：

```bash
python3 docker/scripts/operations.py check-release && \
  docker compose --env-file .env.server -f docker-compose.server.yml up -d --no-build --wait
```

首次部署或已明确完成旧镜像恢复后，使用 `record-release --commit 完整commit` 核对并登记实际运行状态；普通检查不能用该命令消除未知漂移。它核对实际 OCI 版本标签、镜像 ID、有效环境和卷，不要求新版工具目录 HEAD 等于恢复的旧应用 commit；源码一致性由构建/升级阶段确认。无标签 legacy 镜像不能猜测 commit 写入状态，按迁移指南构建已核实、带标签的同版本镜像后再登记。若环境文件位置变化，显式提供 `--release-file /原位置/发布状态.json`。外部直接运行 Compose 无法被工具拦截，需保留上述检查链。

## 手动备份与恢复

在仓库根目录执行。私有目录需属于部署用户；若 `/srv` 不可写，由管理员创建具体目录并授予该用户，不要放宽备份文件权限：

```bash
install -d -m 700 /srv/tracker-backups
python3 docker/scripts/operations.py backup --output-dir /srv/tracker-backups
```

正常退出时标准输出只有备份目录，便于 `BUNDLE="$(...)"` 捕获；诊断写入标准错误。目录包含 `backup.sqlite3` 和 `manifest.json`，权限分别为 600、600，目录为 700。清单只记版本、镜像、卷、时间、SHA256 和用途，不复制 `.env` 或 key。SQLite 副本包括业务数据及密码哈希，应放在加密磁盘/受控存储上。

每次导出都先运行现有 `backup_db` 做一致性快照，再在宿主对比 SHA256、检查 SQLite 完整性。宿主验证成功才删除本次容器内暂存快照；失败可能留下没有清单的私有目录或容器快照，需排障后核对具体路径人工处理。原有手动备份不会被脚本删除。

恢复演练使用新卷，不启动当前服务或改变现有卷：

```bash
python3 docker/scripts/operations.py restore /srv/tracker-backups/tracker-ops-实际目录 \
  --volume meeting-progress-tracker_restore_drill_20260908
```

上述命令默认使用清单保留的旧镜像，需该镜像仍在本机；跨机使用已构建的相同版本镜像，显式提供 `--image-tag COMMIT --app-version COMMIT`。脚本同时检查镜像 ID 和 OCI `org.opencontainers.image.revision`：目标必须带完整 40 位源 commit，备份中的版本必须是同一完整 commit 或至少 7 位十六进制前缀，不能用子串匹配；显式 `--app-version` 也须匹配，不会覆盖快照原版本。跨架构重建允许镜像 ID 改变，但不能换代码版本；默认回退还核对清单 ID。缺少镜像、无版本标签或 `unknown` 备份版本均在运行恢复容器前拒绝，不自动构建或用参数猜测源版本。Compose 的 `run` 没有 `--no-build`，脚本以镜像预检、不可变 ID 和 `--pull never` 固定镜像。命令拒绝已存在的卷，并经标准输入传入私有临时文件，避免 600 宿主文件被容器 app 用户拒读。正式回退见[部署手册](deployment.md)，旧版版本依据与原生数据见[迁移指南](migration.md)。演练卷需核对准确名字后人工处理，脚本不会删卷。

## 定时备份与异地复制

先配好专门的 SSH 主机别名，例如 `tracker-backup`，核实主机密钥，准备受限 SSH key 和目标机私有目录。源机需要 rsync 3.x、SSH；目标机需要 rsync 和 Python 3（含 sqlite3），目标目录必须归备份账户所有且权限 700。使用另一个故障域的可信主机或存储，不能把同一台服务器的另一目录当异地副本。下面路径和主机只是示例，不会自动执行传输：

```bash
python3 docker/scripts/operations.py scheduled \
  --output-dir /srv/tracker-backups \
  --remote tracker-backup:/srv/tracker-offsite --keep 14
```

流程为本机快照校验 → rsync 传完整目录 → 在远端重新比 SHA256 和检查 SQLite → 在本机写异地成功凭据 → 清理超出保留数量的本机历史。SSH 使用非交互模式，认证或远端校验失败则非零退出，显示本机快照位置，保留新旧备份，不执行清理。相同命令再次执行会产生新的快照，旧失败副本需单独检查。

`--keep 14` 只约束同一个异地目标已校验成功的本工具本机备份。它只删除名称匹配、本工具清单和校验值匹配、含成功凭据且只有三个规定文件的目录；未转存备份、损坏副本、有额外文件的目录、符号链接、升级前快照及用户文件均保留。不会递归清理任意目录。已有人工备份和失败残留仍需观察磁盘占用。

远端不会自动删除；建议远端另保留至少 30 天及每月一份长期快照，使用备份账户下专用目录的存储生命周期策略，明确指定这批目录后再配置。不要使用 `rsync --delete` 镜像本机清理结果。每月从异地取回一个完整目录，在新卷按恢复步骤演练；仅传输完成不等于可恢复。

未设置异地信息时可先运行：

```bash
python3 docker/scripts/operations.py scheduled --output-dir /srv/tracker-backups
```

这只生成本机副本，会明确提示“未配置异地目标”，不会连接远端，也不会清理历史。

## 调度示例（手动安装）

先手动执行并验证上述异地命令成功，然后由部署用户安装 cron。以下示例每天按服务器本地时间 02:15 执行，使用 `flock` 避免重叠；服务器时区可能是 UTC，应用的中国业务时区不影响 cron。日志所在目录也需私有：

```cron
15 2 * * * cd /srv/meeting-progress-tracker && /usr/bin/flock -n /srv/tracker-backups/.scheduled.lock /usr/bin/python3 docker/scripts/operations.py scheduled --output-dir /srv/tracker-backups --remote tracker-backup:/srv/tracker-offsite --keep 14 >> /srv/tracker-backups/scheduled.log 2>&1
```

本项目不自动安装该计划任务。先用 `command -v docker python3 rsync ssh flock` 核实实际路径，并为 cron 配好 PATH 和该用户的 SSH 配置；日志提前创建为 600。配置自己可接收的 cron 失败通知，并定期检查最近成功凭据与磁盘，日志文件自行轮转。升级窗口暂停计划任务，避免备份与升级交叉进行；手动命令也不要与正在运行的任务重叠。

当前范围没有增加 CI 发布平台、远端镜像签名或自动环境文件管理。目录/空间检查只能确认检查时的状态，不能保证迁移期间空间仍充足；日志与容器健康、业务抽查和恢复演练仍需完成。

## 解析诊断日志

解析服务默认启用不向 root logger 传播的专用 INFO handler，以单行 JSON 输出 `context`、`first_call`、必要时的 `repair_call`、`model_total`、`persistence`、`total`；`model_total` 截止模型结果校验，worker 的 `total` 从取到任务开始并包含持久化。专用 formatter 丢弃日志消息和非白名单 extra，只输出事件名、会议记录 ID、阶段耗时、输入原文字数，以及上下文或结果中的项目/人员/任务/风险/里程碑/待确认项数量，不会输出会议原文、API Key、请求体、完整模型响应、异常消息或 traceback。归档项目及其任务不会进入模型上下文。排障时用会议记录 ID 串联 worker 日志，并以阶段缺失或异常耗时定位边界，不要临时打印请求与响应内容。

## 恢复决策

容器重启后数据仍在，不代表备份有效；至少保留一份服务器外副本和一份升级前快照。数据库损坏或升级回退时先停 app、再停 worker，保留现场卷，用正确旧镜像和对应旧快照恢复到新卷。已有新写入的场景先保存新卷快照，再决定回退点；SQLite 没有自动合并两端新增数据的操作。
