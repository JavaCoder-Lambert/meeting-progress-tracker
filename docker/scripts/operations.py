#!/usr/bin/env python3
"""Small Compose operator commands. Requires Python 3.11+, Docker Compose v2."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid


KIND = "meeting-progress-tracker.operations.v1"
RELEASE_KIND = "meeting-progress-tracker.release.v1"
INSPECT = ('{"image_id":{{json .Image}},"image":{{json .Config.Image}},'
           '"app_version":{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
           '"mounts":{{json .Mounts}}}')
IMAGE_INSPECT = ('{"image_id":{{json .Id}},'
                 '"app_version":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}')
RESTORE = """
import hashlib, os, pathlib, shutil, subprocess, sys, tempfile
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='tracker-restore-') as staging:
    source = pathlib.Path(staging) / 'backup.sqlite3'
    with source.open('xb') as output:
        shutil.copyfileobj(sys.stdin.buffer, output)
    if hashlib.sha256(source.read_bytes()).hexdigest() != sys.argv[1]:
        raise SystemExit('SHA256 mismatch; database was not restored')
    subprocess.run([sys.executable, 'manage.py', 'restore_db', str(source)], check=True)
"""
REMOTE_VERIFY = """
import hashlib, json, pathlib, sqlite3, sys
bundle = pathlib.Path(sys.argv[1])
manifest = json.loads((bundle / 'manifest.json').read_text())
database = bundle / 'backup.sqlite3'
if manifest.get('sha256') != sys.argv[2] or hashlib.sha256(database.read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit('Remote SHA256 mismatch')
with sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True) as connection:
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise SystemExit('Remote SQLite integrity check failed')
"""
VOLUME_SPACE = """
import json, os, pathlib
data = pathlib.Path(os.environ.get('DATA_DIR', '/data'))
space = os.statvfs(data)
size = sum(path.stat().st_size for path in data.glob('app.sqlite3*') if path.is_file())
print(json.dumps({'database_bytes': size, 'free_bytes': space.f_bavail * space.f_frsize}))
"""
RUNTIME_CHECK = """
import ipaddress, json, os
from urllib.parse import urlsplit
errors = set()
secret = os.environ.get('DJANGO_SECRET_KEY', '')
if len(secret) < 50 or len(set(secret)) < 5:
    errors.add('DJANGO_SECRET_KEY')
if os.environ.get('DJANGO_DEBUG', '').lower() not in {'false', '0', 'no'}:
    errors.add('DJANGO_DEBUG')
if os.environ.get('DJANGO_SECURE_SSL_REDIRECT', '').lower() not in {'true', '1', 'yes'}:
    errors.add('DJANGO_SECURE_SSL_REDIRECT')
hosts = os.environ.get('DJANGO_ALLOWED_HOSTS', '').split(',')
if not any(host.strip() for host in hosts) or any('*' in host or '://' in host for host in hosts):
    errors.add('DJANGO_ALLOWED_HOSTS')
origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
try:
    if any(urlsplit(origin).scheme != 'https' or not urlsplit(origin).hostname or '*' in origin for origin in origins):
        errors.add('CSRF_TRUSTED_ORIGINS')
except ValueError:
    errors.add('CSRF_TRUSTED_ORIGINS')
for name, default in [('LLM_TIMEOUT_SECONDS', '180'), ('GUNICORN_TIMEOUT', '60'), ('SECURE_HSTS_SECONDS', '31536000')]:
    try:
        if int(os.environ.get(name, default)) <= 0:
            errors.add(name)
    except ValueError:
        errors.add(name)
try:
    networks = [ipaddress.ip_network(value.strip()) for value in os.environ.get('LOGIN_TRUSTED_PROXY_CIDRS', '').split(',') if value.strip()]
    if not networks or any(network.prefixlen != network.max_prefixlen for network in networks):
        errors.add('LOGIN_TRUSTED_PROXY_CIDRS')
except ValueError:
    errors.add('LOGIN_TRUSTED_PROXY_CIDRS')
if not errors:
    try:
        os.environ['DJANGO_SETTINGS_MODULE'] = 'tracker.settings'
        import django
        django.setup()
        from django.conf import settings
        from django.contrib.auth import get_user_model
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        if settings.DEBUG or not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
            errors.add('DJANGO_SECURITY_SETTINGS')
        try:
            validate_password(os.environ.get('ADMIN_PASSWORD', ''), get_user_model()(username=os.environ.get('ADMIN_USERNAME', 'admin')))
        except ValidationError:
            errors.add('ADMIN_PASSWORD')
    except Exception:
        errors.add('DJANGO_SETTINGS')
print(json.dumps(sorted(errors)))
"""


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def compose(args):
    return ["docker", "compose", "--env-file", args.env_file, "-f", args.compose_file]


@contextmanager
def operation_lock(args):
    """Serialize mutating commands for one env/Compose deployment identity."""
    identity = "\0".join((str(Path(args.env_file).expanduser().resolve()),
                           str(Path(args.compose_file).expanduser().resolve())))
    digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
    lock_path = Path(tempfile.gettempdir()) / f"meeting-progress-tracker-{digest}.lock"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("该部署正在执行备份、定时备份、升级或恢复；请等待当前操作结束后稍后重试。") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def resolved_config(args, environment=None):
    # Keep expanded credentials only in memory; never relay Compose diagnostics.
    result = subprocess.run(compose(args) + ["config", "--format", "json"],
                            env=environment, capture_output=True, text=True)
    if result.returncode:
        raise ValueError("Compose 配置校验失败；检查 APP_DOMAIN、ACME_EMAIL、DJANGO_SECRET_KEY、ADMIN_PASSWORD 等字段。")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise ValueError("Compose 配置无法解析；未输出配置内容。") from error


def preflight_runtime(args, environment, config):
    image = config["services"]["app"]["image"]
    metadata = json.loads(run(["docker", "image", "inspect", "--format", IMAGE_INSPECT, image],
                              capture_output=True, text=True).stdout)
    if (metadata["app_version"] != args.image_tag
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", metadata["image_id"])):
        raise ValueError("目标镜像 APP_VERSION/镜像 ID 与源码 commit 不一致。")
    runtime = {str(key): str(value) for key, value in config["services"]["app"]["environment"].items() if value is not None}
    runtime["DATA_DIR"] = "/tmp/tracker-preflight"
    command = ["docker", "run", "--rm", "--pull", "never", "--read-only", "--network", "none",
               "--tmpfs", "/tmp:rw,nosuid,size=16m", "--entrypoint", "python"]
    for key in runtime:
        command += ["--env", key]
    result = subprocess.run(command + [metadata["image_id"], "-c", RUNTIME_CHECK],
                            env={**environment, **runtime}, capture_output=True, text=True)
    try:
        fields = json.loads(result.stdout) if result.returncode == 0 else None
        if not isinstance(fields, list) or any(not re.fullmatch(r"[A-Z_]+", field) for field in fields):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("目标镜像运行配置预检失败；检查 DJANGO_SETTINGS，未输出容器诊断或配置值。") from None
    if fields:
        raise ValueError("目标镜像运行配置无效：" + "、".join(fields))
    return {**metadata, "image": image, "volume": config["volumes"]["tracker_data"]["name"]}


def current_release(args, preserve_image=True):
    services = {}
    for service in ("app", "worker"):
        container = run(compose(args) + ["ps", "--all", "-q", service], capture_output=True, text=True).stdout.strip()
        if not container or "\n" in container:
            raise ValueError("需要 app/worker 各恰好一个现有容器（可已停止），用于确认实际镜像与数据卷。")
        item = json.loads(run(["docker", "inspect", "--format", INSPECT, container], capture_output=True, text=True).stdout)
        volumes = [mount["Name"] for mount in item.pop("mounts")
                   if mount["Destination"] == "/data" and mount["Type"] == "volume"]
        if len(volumes) != 1:
            raise ValueError(f"app/worker 的 {service} 必须使用一个挂载到 /data 的命名卷。")
        item["volume"] = volumes[0]
        item["app_version"] = item["app_version"] or "unknown"
        services[service] = item
    identity = ("image_id", "app_version", "volume")
    if any(services["app"][field] != services["worker"][field] for field in identity):
        raise ValueError("app/worker 的实际镜像 ID、OCI commit 或 /data 数据卷不一致。")
    metadata = {**services["app"], "services": services}
    metadata["rollback_image"] = "meeting-progress-tracker:rollback-" + metadata["image_id"].removeprefix("sha256:")[:12]
    if preserve_image:
        run(["docker", "tag", metadata["image_id"], metadata["rollback_image"]])
    return metadata


def commit_matches(recorded, actual):
    return (isinstance(recorded, str) and isinstance(actual, str)
            and re.fullmatch(r"[0-9a-f]{7,40}", recorded) is not None
            and re.fullmatch(r"[0-9a-f]{40}", actual) is not None
            and actual.startswith(recorded))


@contextmanager
def pinned_compose(args, metadata, volume=None, services=("app",), revisions=()):
    requested_image = metadata["rollback_image"]
    try:
        image = json.loads(run(["docker", "image", "inspect", "--format", IMAGE_INSPECT, requested_image],
                               capture_output=True, text=True).stdout)
    except subprocess.CalledProcessError as error:
        raise ValueError(f"镜像不存在或无法读取：{requested_image}；请先构建/载入确认版本，恢复不会自动构建。") from error
    image_id = image["image_id"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("无法确认镜像 ID，未启动容器。")
    if metadata.get("image_id") and metadata["image_id"] != image_id:
        raise ValueError("旧镜像标签的 ID 与备份清单不一致，未启动容器。")
    if any(not commit_matches(revision, image.get("app_version")) for revision in revisions):
        raise ValueError("目标镜像 OCI 版本与备份版本不一致或无法确认；需要同一源 commit 的完整版本标签，不能用 --app-version 改写备份版本。")
    app_version = image["app_version"] if revisions else metadata["app_version"]
    # Compose resolves secrets normally; the temporary override contains no secrets.
    override = {"services": {service: {"image": image_id, "pull_policy": "never",
                 "environment": {"APP_VERSION": app_version}} for service in services},
                "volumes": {"tracker_data": {"name": volume or metadata["volume"]}}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
        json.dump(override, handle)
        handle.flush()
        yield compose(args) + ["-f", handle.name]


def verify_database(path, expected):
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("备份 SHA256 不匹配。")
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite 完整性校验失败。")
        if not connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
            raise ValueError("备份没有数据表。")


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def release_path(args):
    return Path(args.release_file or (args.env_file + ".release.json")).expanduser().absolute()


def read_release(args):
    path = release_path(args)
    if path.is_symlink():
        raise ValueError("发布状态文件不能为符号链接。")
    if not path.is_file():
        raise ValueError("没有发布状态文件；首次部署或明确回退后使用 record-release --commit 完整commit 核对记录。")
    state = json.loads(path.read_text())
    if state.get("kind") != RELEASE_KIND:
        raise ValueError("发布状态文件格式无效；未更改文件。")
    return state


def write_release(args, metadata):
    path = release_path(args)
    if path.is_symlink():
        raise ValueError("发布状态文件不能为符号链接。")
    if path.exists():
        read_release(args)
    state = {"kind": RELEASE_KIND, "commit": metadata["app_version"], "image": metadata["image"],
             "image_id": metadata["image_id"], "volume": metadata["volume"],
             "services": metadata["services"],
             "activated_at": datetime.now(timezone.utc).isoformat()}
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def check_release(args, previous=None, expected=None):
    state = expected or read_release(args)
    actual = previous or current_release(args, preserve_image=False)
    config = resolved_config(args)
    app = config["services"]["app"]
    fields = set()
    if actual["image_id"] != state["image_id"] or actual["app_version"] != state["commit"]:
        fields.update(["IMAGE_TAG", "APP_VERSION"])
    configured_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", app["image"]],
                        capture_output=True, text=True).stdout.strip()
    if configured_id != state["image_id"]:
        fields.add("IMAGE_TAG")
    if app["environment"].get("APP_VERSION") != state["commit"]:
        fields.add("APP_VERSION")
    if actual["volume"] != state["volume"] or config["volumes"]["tracker_data"]["name"] != state["volume"]:
        fields.add("TRACKER_DATA_VOLUME")
    recorded_services = state.get("services", {})
    for service in ("app", "worker"):
        configured = config["services"].get(service, {})
        observed = actual.get("services", {}).get(service)
        recorded = recorded_services.get(service)
        if not observed or not recorded or any(observed.get(field) != recorded.get(field)
                                               for field in ("image_id", "app_version", "volume")):
            fields.add(service.upper() + "_RELEASE")
        if configured.get("image") != app["image"] or configured.get("environment", {}).get("APP_VERSION") != state["commit"]:
            fields.add(service.upper() + "_RELEASE")
    if fields:
        raise ValueError("发布状态、实际容器与当前环境不一致：" + "、".join(sorted(fields))
                         + "；请核对发布清单并同步环境文件和 shell 后再运行 up。")
    return actual


def record_release(args):
    actual = current_release(args, preserve_image=False)
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit) or actual["app_version"] != args.commit:
        raise ValueError("commit 必须与实际容器的完整发布 commit 标签一致。")
    check_release(args, previous=actual, expected={**actual, "commit": args.commit})
    write_release(args, actual)
    print(f"已核对并记录发布状态：{release_path(args)}")


def private_output(args):
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.stat().st_mode & 0o077 or output.stat().st_uid != os.geteuid():
        raise ValueError("备份目录必须为私有目录（chmod 700 指定目录）。")
    with tempfile.TemporaryFile(dir=output) as probe:
        probe.write(b"write-check")
        probe.flush()
        os.fsync(probe.fileno())
    return output


def preflight_space(args, output):
    container = run(compose(args) + ["ps", "-q", "app"], capture_output=True, text=True).stdout.strip()
    if not container or "\n" in container:
        raise ValueError("空间预检需要恰好一个运行中的 app；未停止任何服务。")
    usage = json.loads(run(["docker", "exec", container, "python", "-c", VOLUME_SPACE],
                           capture_output=True, text=True).stdout)
    required = max(int(usage["database_bytes"]), 0) * 2 + 64 * 1024 * 1024
    if int(usage["free_bytes"]) < required or shutil.disk_usage(output).free < required:
        raise ValueError("备份目录或数据卷可用空间不足：需至少两倍数据库大小加 64 MiB 余量。")


def backup(args, metadata=None):
    metadata = metadata or current_release(args)
    output = private_output(args)
    now = datetime.now(timezone.utc)
    bundle = output / ("tracker-ops-" + now.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex)
    bundle.mkdir(mode=0o700)
    internal = "/data/backups/" + bundle.name + ".sqlite3"
    with pinned_compose(args, metadata) as command:
        invocation = command + ["run", "--rm", "--no-deps", "--pull", "never", "-T", "--entrypoint", "python", "app"]
        result = run(invocation + ["manage.py", "backup_db", internal], capture_output=True, text=True)
        checksums = re.findall(r"SHA256[：:]\s*([0-9a-f]{64})", result.stdout)
        if len(checksums) != 1:
            raise ValueError("backup_db 未返回唯一 SHA256，未发布备份清单。")
        with (bundle / "backup.sqlite3").open("xb") as handle:
            run(invocation + ["-c", "import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())", internal], stdout=handle)
        verify_database(bundle / "backup.sqlite3", checksums[0])
        write_json(bundle / "manifest.json", {"kind": KIND, "created_at": now.isoformat(),
                   "database": "backup.sqlite3", "sha256": checksums[0], **metadata})
        run(invocation + ["-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).unlink()", internal], stdout=sys.stderr)
    return bundle


def read_bundle(bundle):
    if bundle.is_symlink():
        raise ValueError("备份目录不能为符号链接。")
    metadata = json.loads((bundle / "manifest.json").read_text())
    if metadata.get("kind") != KIND or metadata.get("database") != "backup.sqlite3":
        raise ValueError("不是本工具生成的备份清单。")
    verify_database(bundle / "backup.sqlite3", metadata["sha256"])
    return metadata


def restore(args):
    bundle = Path(args.bundle).expanduser().absolute()
    if bundle.is_file():
        if not args.sha256 or not args.image_tag or not args.app_version:
            raise ValueError("单个 SQLite 备份必须提供 --sha256、--image-tag 和 --app-version。")
        verify_database(bundle, args.sha256)
        database = bundle
        metadata = {"sha256": args.sha256, "app_version": args.app_version}
    else:
        metadata = read_bundle(bundle)
        database = bundle / "backup.sqlite3"
    revisions = [metadata["app_version"]]
    if args.app_version:
        revisions.append(args.app_version)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", args.volume):
        raise ValueError("数据卷名称无效。")
    existing = subprocess.run(["docker", "volume", "inspect", args.volume], capture_output=True)
    if existing.returncode == 0:
        raise ValueError("目标卷已存在；请使用从未使用过的新卷名，原卷保持不动。")
    if args.image_tag:
        metadata["rollback_image"] = "meeting-progress-tracker:" + args.image_tag
        metadata.pop("image_id", None)  # Explicit target-machine rebuild may use another architecture.
    with pinned_compose(args, metadata, args.volume, revisions=revisions) as command:
        with database.open("rb") as source:
            run(command + ["run", "--rm", "--no-deps", "--pull", "never", "-T", "--entrypoint", "python", "app",
                           "-c", RESTORE, metadata["sha256"]], stdin=source, stdout=sys.stderr)
    print(f"已恢复到新卷 {args.volume}；尚未启动 app/worker。")


def upgrade(args):
    previous = current_release(args)
    if release_path(args).exists() or release_path(args).is_symlink():
        check_release(args, previous=previous)
    with tempfile.TemporaryFile(dir=release_path(args).parent) as probe:
        probe.write(b"release-write-check")
        probe.flush()
        os.fsync(probe.fileno())
    source = Path(args.compose_file).resolve().parent
    head = run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", args.image_tag) or args.image_tag != head:
        raise ValueError("IMAGE_TAG 必须为构建源码 HEAD 的完整 commit。")
    if args.app_version and args.app_version != head:
        raise ValueError("APP_VERSION 必须与 IMAGE_TAG、源码 HEAD 一致。")
    dirty = run(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
                capture_output=True, text=True).stdout
    if dirty:
        raise ValueError("构建源码工作树不干净；请从已发布 commit 的干净目录构建，不绕过忽略规则。")
    output = private_output(args)
    preflight_space(args, output)
    if args.image_tag in {"server", "local", previous["app_version"], previous["image"].rsplit(":", 1)[-1],
                          previous["rollback_image"].rsplit(":", 1)[-1]}:
        raise ValueError("升级请使用全新的 commit 镜像标签，保留现有镜像。")
    environment = {**os.environ, "IMAGE_TAG": args.image_tag,
                   "APP_VERSION": args.app_version or args.image_tag,
                   "TRACKER_DATA_VOLUME": previous["volume"]}
    config = resolved_config(args, environment)
    app = config["services"]["app"]
    if (Path(app["build"]["context"]).resolve() != source
            or app["build"].get("args", {}).get("APP_VERSION") != head
            or app["environment"].get("APP_VERSION") != head
            or app["image"] != "meeting-progress-tracker:" + head):
        raise ValueError("构建配置 context、IMAGE_TAG、APP_VERSION 必须对应同一源码 HEAD。")
    run(compose(args) + ["build", "app"], env=environment, stdout=sys.stderr)
    target = preflight_runtime(args, environment, config)
    preflight_space(args, output)  # A build may consume space on the same host filesystem.
    run(compose(args) + ["stop", "app"], stdout=sys.stderr)
    run(compose(args) + ["stop", "worker"], stdout=sys.stderr)
    bundle = backup(args, {**previous, "purpose": "pre-upgrade"})
    print(f"升级前快照：{bundle}\n旧镜像：{previous['rollback_image']}", file=sys.stderr)
    try:
        with pinned_compose(args, {**target, "rollback_image": target["image"]}, services=("app", "worker")) as command:
            run(command + ["up", "-d", "--no-build", "--wait"], env=environment, stdout=sys.stderr)
        actual = current_release(args, preserve_image=False)
        if any(actual[field] != target[field] for field in ("image_id", "app_version", "volume")):
            raise ValueError("启动后实际镜像或数据卷与预检目标不一致，未记录成功发布。")
        write_release(args, actual)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("新版本启动失败，正在分别停止 app/worker；升级前快照与旧镜像已保留。", file=sys.stderr)
        stopped = True
        for service in ("app", "worker"):
            try:
                run(compose(args) + ["stop", service], env=environment, stdout=sys.stderr)
                print(f"{service}：已停止", file=sys.stderr)
            except (OSError, subprocess.CalledProcessError) as stop_error:
                stopped = False
                print(f"{service}：停止失败（{stop_error}）", file=sys.stderr)
        if not stopped:
            print("无法确认全部停写；立即核对容器并停止仍运行的服务，再决定恢复。", file=sys.stderr)
        raise
    print(bundle)
    print(f"升级成功；发布状态已写入 {release_path(args)}。请保存 IMAGE_TAG={args.image_tag}、"
          f"APP_VERSION={environment['APP_VERSION']}、TRACKER_DATA_VOLUME={previous['volume']} 到部署环境文件，"
          "并运行 check-release 后再执行日常 up。", file=sys.stderr)


def prune_transferred(output, remote, keep):
    eligible = []
    for bundle in output.iterdir():
        if (bundle.is_symlink() or not bundle.is_dir()
                or not re.fullmatch(r"tracker-ops-\d{8}T\d{6}Z-[0-9a-f]{32}", bundle.name)):
            continue
        files = list(bundle.iterdir())
        if {path.name for path in files} != {"backup.sqlite3", "manifest.json", ".offsite-verified.json"}:
            continue
        if any(path.is_symlink() or not path.is_file() for path in files):
            continue
        try:
            metadata = read_bundle(bundle)
            receipt = json.loads((bundle / ".offsite-verified.json").read_text())
            if (metadata.get("purpose") == "pre-upgrade" or receipt.get("remote") != remote
                    or receipt.get("sha256") != metadata["sha256"]):
                continue
            eligible.append((metadata["created_at"], bundle))
        except (ValueError, KeyError, OSError, sqlite3.Error):
            continue
    for _, bundle in sorted(eligible, reverse=True)[keep:]:
        # No recursive delete: only these three independently verified files.
        for filename in ("backup.sqlite3", "manifest.json", ".offsite-verified.json"):
            (bundle / filename).unlink()
        bundle.rmdir()


def scheduled(args):
    if args.keep < 1:
        raise ValueError("--keep 必须至少为 1。")
    remote = None
    if args.remote:
        remote = re.fullmatch(r"([A-Za-z0-9_][A-Za-z0-9_.@-]*):(/[^\n\r]*)", args.remote)
        if not remote or ".." in Path(remote[2]).parts:
            raise ValueError("--remote 格式应为 SSH主机别名:/绝对备份目录。")
    bundle = backup(args)
    print(f"本机快照已保留：{bundle}", file=sys.stderr)
    if remote:
        destination = remote[2].rstrip("/") + "/" + bundle.name
        run(["rsync", "-a", "--protect-args", "-e", "ssh -o BatchMode=yes", "--chmod=Du=rwx,Dgo=,Fu=rw,Fgo=", "--",
             str(bundle) + "/", remote[1] + ":" + destination + "/"], stdout=sys.stderr)
        metadata = read_bundle(bundle)
        command = shlex.join(["python3", "-c", REMOTE_VERIFY, destination, metadata["sha256"]])
        run(["ssh", "-o", "BatchMode=yes", remote[1], command], stdout=sys.stderr)
        write_json(bundle / ".offsite-verified.json", {"remote": args.remote,
                   "sha256": metadata["sha256"], "verified_at": datetime.now(timezone.utc).isoformat()})
        prune_transferred(bundle.parent, args.remote, args.keep)
    else:
        print("未配置异地目标：仅创建本机备份，不传输、不清理历史。", file=sys.stderr)
    print(bundle)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.server")
    parser.add_argument("--compose-file", default="docker-compose.server.yml")
    parser.add_argument("--release-file", help="非敏感发布状态文件；默认 <env-file>.release.json")
    commands = parser.add_subparsers(dest="command", required=True)
    backup_parser = commands.add_parser("backup", help="导出已校验、带版本清单的私有快照")
    backup_parser.add_argument("--output-dir", required=True)
    restore_parser = commands.add_parser("restore", help="从备份目录恢复到从未使用的新卷，不启动服务")
    restore_parser.add_argument("bundle")
    restore_parser.add_argument("--sha256", help="恢复原生安装导出的单个 SQLite 文件时，提供原机记录的 SHA256")
    restore_parser.add_argument("--volume", required=True)
    restore_parser.add_argument("--image-tag", help="跨主机时使用目标机器已构建的同版本镜像标签")
    restore_parser.add_argument("--app-version")
    upgrade_parser = commands.add_parser("upgrade", help="先构建，再停写、备份和启动指定 commit 镜像")
    upgrade_parser.add_argument("--image-tag", required=True)
    upgrade_parser.add_argument("--app-version")
    upgrade_parser.add_argument("--output-dir", required=True)
    commands.add_parser("check-release", help="核对发布状态、实际镜像/数据卷及环境；日常 up 前执行")
    record_parser = commands.add_parser("record-release", help="首次部署或明确回退后核对并记录实际发布，不修改环境")
    record_parser.add_argument("--commit", required=True)
    scheduled_parser = commands.add_parser("scheduled", help="备份并可选异地转存；远端验证成功后才保留最近 N 份本机历史")
    scheduled_parser.add_argument("--output-dir", required=True)
    scheduled_parser.add_argument("--remote", help="SSH主机别名:/绝对目录；需预配 SSH、rsync 和远端 Python3")
    scheduled_parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args()
    try:
        if args.command in {"backup", "restore", "upgrade", "scheduled"}:
            with operation_lock(args):
                if args.command == "backup":
                    print(backup(args))
                elif args.command == "restore":
                    restore(args)
                elif args.command == "upgrade":
                    upgrade(args)
                else:
                    scheduled(args)
        elif args.command == "check-release":
            check_release(args)
            print("发布状态、实际镜像/数据卷与当前环境一致。")
        elif args.command == "record-release":
            record_release(args)
    except (ValueError, KeyError, OSError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(f"操作失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
