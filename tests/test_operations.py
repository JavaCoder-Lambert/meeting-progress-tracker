"""Exercise the shipped operator CLI; Docker is the external boundary."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docker/scripts/operations.py"
NEW_COMMIT = "b" * 40
OLD_COMMIT = "d0be442eae1296f9d52270873a07147c4f81043b"


@pytest.fixture
def operator(tmp_path):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE projects (name TEXT)")
        connection.execute("INSERT INTO projects VALUES ('migration fixture')")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("""#!/usr/bin/env python3
import hashlib, json, os, pathlib, shutil, subprocess, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_ROOT'])
with (root / 'docker.log').open('a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'inspect':
    upgraded = (root / 'started-version').exists()
    service = 'worker' if args[-1] == 'worker-container' else 'app'
    mismatched = os.environ.get('FAKE_MISMATCH_SERVICE') == service
    print(json.dumps({'image_id': 'sha256:' + ('b' if upgraded else 'a') * 64,
        'image': 'meeting-progress-tracker:' + ('b' * 40 if upgraded else 'old'),
        'app_version': None if os.environ.get('FAKE_NO_REVISION') else ('c' * 40 if mismatched else ('b' * 40 if upgraded else os.environ['FAKE_OLD_COMMIT'])),
        'mounts': [{'Type': 'volume', 'Destination': '/data', 'Name': 'wrong-data' if mismatched else 'old-data'}]}))
elif args[0] == 'tag':
    pass
elif args[0] == 'exec':
    print(json.dumps({'database_bytes': 2**50 if os.environ.get('FAKE_HUGE_DATABASE') else 1024,
        'free_bytes': 0 if os.environ.get('FAKE_DISK_FULL') else 2**60}))
elif args[:2] == ['image', 'inspect']:
    if os.environ.get('FAKE_IMAGE_MISSING'):
        raise SystemExit(25)
    if 'app_version' in args[args.index('--format') + 1]:
        newer = args[-1].endswith('b' * 40)
        revision = 'b' * 40 if newer else os.environ['FAKE_OLD_COMMIT']
        print(json.dumps({'image_id': os.environ.get('FAKE_TARGET_IMAGE_ID', 'sha256:' + ('b' if newer else 'a') * 64),
            'app_version': os.environ.get('FAKE_TARGET_REVISION', revision)}))
    else:
        print('sha256:' + ('b' if args[-1].endswith('b' * 40) else 'a') * 64)
elif args[:2] == ['volume', 'inspect']:
    sys.exit(0 if os.environ.get('FAKE_VOLUME_EXISTS') else 1)
elif args[0] == 'compose':
    if 'ps' in args:
        service = args[-1]
        if os.environ.get('FAKE_MISSING_SERVICE') != service:
            print(service + '-container')
    elif 'config' in args:
        environment = {'APP_VERSION': os.environ.get('APP_VERSION', os.environ['FAKE_OLD_COMMIT']),
            'DJANGO_DEBUG': 'false', 'DJANGO_SECRET_KEY': 'fixture-only-secret-with-over-fifty-characters-and-no-real-credentials-12345',
            'DJANGO_ALLOWED_HOSTS': 'progress.example.com,127.0.0.1', 'CSRF_TRUSTED_ORIGINS': 'https://progress.example.com',
            'DJANGO_SECURE_SSL_REDIRECT': 'true', 'ADMIN_USERNAME': 'owner', 'ADMIN_PASSWORD': 'test-private-password-123',
            'LLM_TIMEOUT_SECONDS': '180', 'GUNICORN_TIMEOUT': '60', 'LOGIN_TRUSTED_PROXY_CIDRS': '172.30.7.2/32'}
        if os.environ.get('FAKE_BAD_FIELD'):
            environment[os.environ['FAKE_BAD_FIELD']] = os.environ.get('FAKE_BAD_VALUE', '')
        service = {'environment': environment,
            'image': 'meeting-progress-tracker:' + os.environ.get('IMAGE_TAG', 'old'),
            'build': {'context': os.environ['FAKE_SOURCE_ROOT'], 'args': {'APP_VERSION': environment['APP_VERSION']}}}
        print(json.dumps({'services': {'app': service, 'worker': service},
            'volumes': {'tracker_data': {'name': os.environ.get('TRACKER_DATA_VOLUME', 'old-data')}}}))
    elif 'backup_db' in args:
        if os.environ.get('FAKE_BACKUP_FAIL'):
            raise SystemExit(21)
        staging = root / 'container-data' / args[-1].removeprefix('/data/')
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / 'source.sqlite3', staging)
        print('SHA256：' + hashlib.sha256((root / 'source.sqlite3').read_bytes()).hexdigest())
    elif 'run' in args and '-c' in args:
        # Execute the actual container command with /data redirected to this fixture.
        code_index = args.index('-c') + 1
        code = args[code_index]
        inputs = [str(root / 'container-data' / item.removeprefix('/data/'))
                  if item.startswith('/data/') else item for item in args[code_index + 1:]]
        sys.exit(subprocess.run([sys.executable, '-c', code, *inputs], cwd=root).returncode)
    elif 'build' in args:
        if os.environ.get('FAKE_BUILD_FAIL'):
            raise SystemExit(22)
    elif 'stop' in args:
        service = args[-1]
        if (root / 'up-attempted').exists() and os.environ.get('FAKE_STOP_FAIL') == service:
            raise SystemExit(24)
        (root / ('running-' + service)).unlink(missing_ok=True)
    elif 'up' in args:
        (root / 'up-attempted').touch()
        (root / 'started-version').write_text(os.environ.get('APP_VERSION', ''))
        (root / 'started-volume').write_text(os.environ.get('TRACKER_DATA_VOLUME', ''))
        for service in ['app', 'worker']:
            (root / ('running-' + service)).touch()
        if os.environ.get('FAKE_UP_FAIL'):
            raise SystemExit(23)
    else:
        raise SystemExit('Unexpected compose invocation: ' + repr(args))
elif args[0] == 'run':
    code_index = args.index('-c') + 1
    environment = {**os.environ, 'PYTHONPATH': os.environ['FAKE_SOURCE_ROOT']}
    environment['DATA_DIR'] = str(root / 'preflight-data')
    sys.exit(subprocess.run([os.environ['FAKE_PYTHON'], '-c', args[code_index]], env=environment, cwd=root).returncode)
else:
    raise SystemExit('Unexpected docker invocation: ' + repr(args))
""")
    docker.chmod(0o700)
    (bin_dir / "git").write_text("""#!/usr/bin/env python3
import os, sys
if 'rev-parse' in sys.argv:
    print('b' * 40)
elif 'status' in sys.argv:
    print(' M core/views.py' if os.environ.get('FAKE_DIRTY_SOURCE') else '', end='')
else:
    raise SystemExit('Unexpected git invocation')
""")
    (bin_dir / "git").chmod(0o700)
    (tmp_path / "fixture.env").write_text("PRIVATE_VALUE=fixture-secret-only\n")
    (tmp_path / "manage.py").write_text("""
import json, os, pathlib, sqlite3, sys
source = pathlib.Path(sys.argv[2])
root = pathlib.Path(os.environ['FAKE_ROOT'])
(root / 'private-source.json').write_text(json.dumps({
    'path': str(source), 'mode': source.stat().st_mode & 0o777,
    'parent_mode': source.parent.stat().st_mode & 0o777}))
target = root / 'restored.sqlite3'
if target.exists():
    raise SystemExit('refuse overwrite')
with sqlite3.connect(source) as reader, sqlite3.connect(target) as writer:
    reader.backup(writer)
""")
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_ROOT": str(tmp_path),
                   "FAKE_SOURCE_ROOT": str(ROOT), "FAKE_PYTHON": sys.executable, "FAKE_OLD_COMMIT": OLD_COMMIT}

    def run(*args, **extra_env):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--env-file", str(tmp_path / "fixture.env"), *map(str, args)], cwd=ROOT,
            env={**environment, **extra_env}, capture_output=True, text=True,
        )

    return run, tmp_path


def offsite_transport(root):
    """Only replace network transport; run the real remote verification program locally."""
    remote = root / "remote"
    remote.mkdir()
    (root / "bin/rsync").write_text("""#!/usr/bin/env python3
import os, pathlib, shutil, sys
if os.environ.get('FAKE_TRANSFER_FAIL'):
    raise SystemExit(23)
source = pathlib.Path(sys.argv[-2])
destination = pathlib.Path(sys.argv[-1].split(':', 1)[1])
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(source, destination, dirs_exist_ok=True)
if os.environ.get('FAKE_REMOTE_CORRUPT'):
    (destination / 'backup.sqlite3').write_bytes(b'bad remote bytes')
""")
    (root / "bin/ssh").write_text("""#!/usr/bin/env python3
import subprocess, sys
raise SystemExit(subprocess.run(sys.argv[-1], shell=True).returncode)
""")
    (root / "bin/rsync").chmod(0o700)
    (root / "bin/ssh").chmod(0o700)
    return "backup-host:" + str(remote)


def test_backup_exports_private_verified_snapshot_and_actual_release(operator):
    """Catches exporting unverified bytes, public backup permissions or guessed release metadata."""
    run, root = operator
    result = run("backup", "--output-dir", root / "backups")
    assert result.returncode == 0, result.stderr
    bundle = Path(result.stdout.strip())
    database = bundle / "backup.sqlite3"
    manifest = json.loads((bundle / "manifest.json").read_text())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM projects").fetchall() == [("migration fixture",)]
    assert database.stat().st_mode & 0o777 == 0o600
    assert bundle.stat().st_mode & 0o777 == 0o700
    assert (bundle / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert manifest["sha256"] == hashlib.sha256(database.read_bytes()).hexdigest()
    assert manifest["app_version"] == OLD_COMMIT
    assert manifest["volume"] == "old-data"
    assert manifest["image_id"] == "sha256:" + "a" * 64
    assert manifest["rollback_image"] == "meeting-progress-tracker:rollback-aaaaaaaaaaaa"
    assert set(manifest["services"]) == {"app", "worker"}
    assert manifest["services"]["app"]["image_id"] == manifest["services"]["worker"]["image_id"]
    assert not any("KEY" in key or "PASSWORD" in key for key in manifest)


@pytest.mark.parametrize("problem,value", [("FAKE_MISSING_SERVICE", "worker"), ("FAKE_MISMATCH_SERVICE", "worker")])
def test_backup_requires_exactly_one_matching_app_and_worker(operator, problem, value):
    """Catches recording a release when either writer is absent or uses different release identity."""
    run, root = operator
    result = run("backup", "--output-dir", root / "backups", **{problem: value})
    assert result.returncode == 1
    assert "app/worker" in result.stderr
    assert not list((root / "backups").glob("*/manifest.json"))


def test_mutating_operations_share_a_non_blocking_deployment_lock(operator):
    """Catches concurrent backup/restore/upgrade processes changing one deployment."""
    run, root = operator
    env_alias = root / "fixture-link.env"
    env_alias.symlink_to(root / "fixture.env")
    holder_code = f"""
import importlib.util, pathlib, types, time
spec = importlib.util.spec_from_file_location('operations', {str(SCRIPT)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
args = types.SimpleNamespace(env_file={str(env_alias)!r}, compose_file={str(ROOT / 'docker-compose.server.yml')!r})
with module.operation_lock(args):
    pathlib.Path({str(root / 'lock-ready')!r}).touch()
    time.sleep(10)
"""
    holder = subprocess.Popen([sys.executable, "-c", holder_code], cwd=ROOT)
    try:
        for _ in range(100):
            if (root / "lock-ready").exists():
                break
            time.sleep(0.02)
        result = run("backup", "--output-dir", root / "backups")
        assert result.returncode == 1
        assert "正在执行" in result.stderr and "稍后重试" in result.stderr
        assert not (root / "docker.log").exists()
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_upgrade_can_take_its_nested_backup_without_relocking(operator):
    """Catches a non-reentrant nested backup deadlocking the upgrade process."""
    run, root = operator
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups")
    assert result.returncode == 0, result.stderr


def test_backup_removes_only_its_exported_container_staging_file(operator):
    """Catches unbounded volume growth while preserving user-created backup files."""
    run, root = operator
    staging = root / "container-data/backups"
    staging.mkdir(parents=True)
    personal = staging / "manual-backup.sqlite3"
    personal.write_bytes(b"operator-owned old backup")
    result = run("backup", "--output-dir", root / "backups")
    assert result.returncode == 0, result.stderr
    assert list(staging.iterdir()) == [personal]
    assert personal.read_bytes() == b"operator-owned old backup"


def test_restore_streams_private_backup_to_private_temporary_file(operator):
    """Catches unreadable host bind mounts and leftover/public container staging files."""
    run, root = operator
    backed_up = run("backup", "--output-dir", root / "backups")
    result = run("restore", backed_up.stdout.strip(), "--volume", "restored-data")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(root / "restored.sqlite3") as connection:
        assert connection.execute("SELECT name FROM projects").fetchall() == [("migration fixture",)]
    staged = json.loads((root / "private-source.json").read_text())
    assert staged["mode"] == 0o600
    assert staged["parent_mode"] == 0o700
    assert not Path(staged["path"]).exists()
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("-v" in command or "--volume" in command or "up" in command for command in commands)


def test_restore_rejects_tampered_backup_without_touching_target(operator):
    """Catches ignoring the manifest checksum during restore."""
    run, root = operator
    backed_up = run("backup", "--output-dir", root / "backups")
    bundle = Path(backed_up.stdout.strip())
    with (bundle / "backup.sqlite3").open("ab") as handle:
        handle.write(b"changed after export")
    result = run("restore", bundle, "--volume", "restored-data")
    assert result.returncode != 0
    assert "SHA256" in result.stderr
    assert not (root / "restored.sqlite3").exists()


def test_restore_refuses_existing_volume(operator):
    """Catches restoring into a potentially live database volume."""
    run, root = operator
    backed_up = run("backup", "--output-dir", root / "backups")
    result = run("restore", backed_up.stdout.strip(), "--volume", "already-used", FAKE_VOLUME_EXISTS="yes")
    assert result.returncode != 0
    assert "卷已存在" in result.stderr
    assert not (root / "restored.sqlite3").exists()


def test_restore_rejects_missing_image_before_container_can_build_current_code(operator):
    """Catches silently replacing a missing historical image with the current checkout."""
    run, root = operator
    backed_up = run("backup", "--output-dir", root / "backups")
    result = run("restore", backed_up.stdout.strip(), "--volume", "restored-data", FAKE_IMAGE_MISSING="yes")
    assert result.returncode == 1, result.stderr
    assert "镜像" in result.stderr
    assert not (root / "restored.sqlite3").exists()


def test_restore_accepts_checked_native_backup_with_explicit_release(operator):
    """Catches inability to migrate a private backup from a non-Docker local installation."""
    run, root = operator
    source = root / "source.sqlite3"
    source.chmod(0o600)
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    result = run("restore", source, "--sha256", checksum, "--volume", "native-restore",
                 "--image-tag", OLD_COMMIT, "--app-version", OLD_COMMIT)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(root / "restored.sqlite3") as connection:
        assert connection.execute("SELECT name FROM projects").fetchall() == [("migration fixture",)]


def test_native_restore_checks_declared_source_commit_against_target_oci_revision(operator):
    run, root = operator
    source = root / "source.sqlite3"
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    result = run("restore", source, "--sha256", checksum, "--volume", "native-restore",
                 "--image-tag", NEW_COMMIT, "--app-version", OLD_COMMIT)
    assert result.returncode == 1
    assert "版本" in result.stderr
    assert not (root / "restored.sqlite3").exists()


@pytest.mark.parametrize("requested_version", [OLD_COMMIT, NEW_COMMIT])
def test_restore_cannot_disguise_a_new_image_as_the_snapshot_version(operator, requested_version):
    run, root = operator
    backed_up = run("backup", "--output-dir", root / "backups")
    assert backed_up.returncode == 0, backed_up.stderr
    (root / "docker.log").write_text("")
    result = run("restore", backed_up.stdout.strip(), "--volume", "restored-data",
                 "--image-tag", NEW_COMMIT, "--app-version", requested_version)
    assert result.returncode == 1
    assert "版本" in result.stderr
    assert not (root / "restored.sqlite3").exists()
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("run" in command or "stop" in command for command in commands)


@pytest.mark.parametrize("snapshot_version", [OLD_COMMIT, "d0be442"])
def test_restore_accepts_rebuilt_d0be442_only_when_oci_commit_matches_snapshot(operator, snapshot_version):
    run, root = operator
    backed_up = run("backup", "--output-dir", root / "backups", FAKE_OLD_COMMIT=snapshot_version)
    assert backed_up.returncode == 0, backed_up.stderr
    result = run("restore", backed_up.stdout.strip(), "--volume", "restored-old-data",
                 "--image-tag", OLD_COMMIT, "--app-version", OLD_COMMIT,
                 FAKE_TARGET_IMAGE_ID="sha256:" + "c" * 64)
    assert result.returncode == 0, result.stderr
    assert (root / "restored.sqlite3").exists()


@pytest.mark.parametrize("snapshot_version", ["d0be44", "eae1296", "unknown"])
def test_restore_rejects_ambiguous_or_nonprefix_snapshot_versions(operator, snapshot_version):
    run, root = operator
    backed_up = run("backup", "--output-dir", root / "backups", FAKE_OLD_COMMIT=snapshot_version)
    assert backed_up.returncode == 0, backed_up.stderr
    (root / "docker.log").write_text("")
    result = run("restore", backed_up.stdout.strip(), "--volume", "restored-old-data",
                 "--image-tag", OLD_COMMIT, "--app-version", OLD_COMMIT)
    assert result.returncode == 1
    assert "版本" in result.stderr
    assert not (root / "restored.sqlite3").exists()
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("run" in command or "stop" in command for command in commands)


@pytest.mark.parametrize("revision", ["", "d0be442", NEW_COMMIT])
def test_restore_requires_full_matching_target_oci_revision_even_for_pinned_old_image(operator, revision):
    run, root = operator
    backed_up = run("backup", "--output-dir", root / "backups")
    assert backed_up.returncode == 0, backed_up.stderr
    (root / "docker.log").write_text("")
    result = run("restore", backed_up.stdout.strip(), "--volume", "restored-old-data", FAKE_TARGET_REVISION=revision)
    assert result.returncode == 1
    assert "版本" in result.stderr
    assert not (root / "restored.sqlite3").exists()
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("run" in command or "stop" in command for command in commands)


def test_upgrade_builds_before_stopping_writes_and_preserves_old_snapshot(operator):
    """Catches starting a release before its old database and image can be recovered."""
    run, root = operator
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups")
    assert result.returncode == 0, result.stderr
    bundle = Path(result.stdout.strip())
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["app_version"] == OLD_COMMIT
    assert manifest["purpose"] == "pre-upgrade"
    assert (root / "started-version").read_text() == NEW_COMMIT
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    positions = {name: next(i for i, command in enumerate(commands) if name in command)
                 for name in ["build", "stop", "backup_db", "up"]}
    assert positions["build"] < positions["stop"] < positions["backup_db"] < positions["up"]


def test_upgrade_keeps_actual_running_volume_when_shell_configuration_drifted(operator):
    """Catches accidentally starting the new release against a fresh/incorrect volume."""
    run, root = operator
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups",
                 TRACKER_DATA_VOLUME="mistaken-new-data")
    assert result.returncode == 0, result.stderr
    assert (root / "started-volume").read_text() == "old-data"


def test_upgrade_rejects_public_backup_directory_before_stopping_any_writer(operator):
    run, root = operator
    output = root / "public-backups"
    output.mkdir(mode=0o755)
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", output)
    assert result.returncode == 1
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("stop" in command for command in commands)


def test_upgrade_rejects_full_data_volume_before_stopping_any_writer(operator):
    run, root = operator
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups", FAKE_DISK_FULL="yes")
    assert result.returncode == 1
    assert "空间" in result.stderr
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("stop" in command for command in commands)


@pytest.mark.parametrize("failure,forbidden", [("FAKE_BUILD_FAIL", "stop"), ("FAKE_BACKUP_FAIL", "up")])
def test_upgrade_aborts_before_next_destructive_stage(operator, failure, forbidden):
    """Catches stopping healthy service after failed build, or switching after failed snapshot."""
    run, root = operator
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups", **{failure: "yes"})
    assert result.returncode == 1, result.stderr
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any(forbidden in command for command in commands)


@pytest.mark.parametrize("stop_failure", ["", "app", "worker"])
def test_upgrade_failure_stops_both_writers_and_preserves_original_error(operator, stop_failure):
    """Catches leaving a writable new release running after up --wait has failed."""
    run, root = operator
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups",
                 FAKE_UP_FAIL="yes", FAKE_STOP_FAIL=stop_failure)
    assert result.returncode == 1
    for service in ["app", "worker"]:
        assert (root / f"running-{service}").exists() == (stop_failure == service)
        assert f"{service}：{'停止失败' if stop_failure == service else '已停止'}" in result.stderr
    assert "exit status 23" in result.stderr
    assert len(list((root / "backups").glob("*/manifest.json"))) == 1


@pytest.mark.parametrize("tag,version,dirty", [
    ("release-whatever", None, ""), ("c" * 40, None, ""),
    (NEW_COMMIT, "made-up-version", ""), (NEW_COMMIT, None, "yes"),
])
def test_upgrade_rejects_unverifiable_source_before_build_or_stop(operator, tag, version, dirty):
    run, root = operator
    arguments = ["upgrade", "--image-tag", tag, "--output-dir", root / "backups"]
    if version:
        arguments += ["--app-version", version]
    result = run(*arguments, FAKE_DIRTY_SOURCE=dirty)
    assert result.returncode == 1
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("build" in command or "stop" in command for command in commands)


@pytest.mark.parametrize("field,value", [
    ("DJANGO_SECRET_KEY", "private-key-must-not-leak"),
    ("DJANGO_DEBUG", "true"), ("LLM_TIMEOUT_SECONDS", "wrong-timeout-private"),
    ("ADMIN_PASSWORD", "private"), ("DJANGO_ALLOWED_HOSTS", "*"),
])
def test_upgrade_checks_target_runtime_config_without_mounting_live_data_or_leaking_values(operator, field, value):
    run, root = operator
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups",
                 FAKE_BAD_FIELD=field, FAKE_BAD_VALUE=value)
    assert result.returncode == 1
    assert field in result.stderr
    if "private" in value:
        assert value not in result.stdout + result.stderr
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("stop" in command for command in commands)
    preflight = next(command for command in commands if command[0] == "run")
    assert "--read-only" in preflight and "--network" in preflight
    assert not any(flag in preflight for flag in ["--volume", "-v", "--mount"])


def test_upgrade_records_verified_release_without_rewriting_environment_and_detects_stale_environment(operator):
    run, root = operator
    before = (root / "fixture.env").read_bytes()
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups")
    assert result.returncode == 0, result.stderr
    state = json.loads((root / "fixture.env.release.json").read_text())
    assert state["commit"] == NEW_COMMIT
    assert state["image_id"] == "sha256:" + "b" * 64
    assert state["volume"] == "old-data"
    assert "PRIVATE_VALUE" not in state
    assert (root / "fixture.env").read_bytes() == before
    checked = run("check-release")
    assert checked.returncode == 1
    assert "IMAGE_TAG" in checked.stderr and "APP_VERSION" in checked.stderr
    synchronized = run("check-release", IMAGE_TAG=NEW_COMMIT, APP_VERSION=NEW_COMMIT, TRACKER_DATA_VOLUME="old-data")
    assert synchronized.returncode == 0, synchronized.stderr
    assert (root / "fixture.env").read_bytes() == before


def test_upgrade_rejects_insufficient_host_space_before_stopping_writers(operator):
    run, root = operator
    result = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups", FAKE_HUGE_DATABASE="yes")
    assert result.returncode == 1
    assert "空间" in result.stderr
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("stop" in command for command in commands)


def test_record_release_rejects_legacy_unlabelled_image_without_inventing_commit(operator):
    run, root = operator
    result = run("record-release", "--commit", NEW_COMMIT, FAKE_NO_REVISION="yes")
    assert result.returncode == 1
    assert not (root / "fixture.env.release.json").exists()


def test_record_release_checks_actual_runtime_and_environment_before_explicit_registration(operator):
    run, root = operator
    (root / "started-version").write_text(NEW_COMMIT)
    mismatch = run("record-release", "--commit", NEW_COMMIT)
    assert mismatch.returncode == 1
    assert not (root / "fixture.env.release.json").exists()
    recorded = run("record-release", "--commit", NEW_COMMIT,
                   IMAGE_TAG=NEW_COMMIT, APP_VERSION=NEW_COMMIT, TRACKER_DATA_VOLUME="old-data")
    assert recorded.returncode == 0, recorded.stderr
    state = json.loads((root / "fixture.env.release.json").read_text())
    assert state["commit"] == NEW_COMMIT and state["image_id"] == "sha256:" + "b" * 64


def test_upgrade_rejects_stale_release_environment_before_stopping_writers(operator):
    run, root = operator
    succeeded = run("upgrade", "--image-tag", NEW_COMMIT, "--output-dir", root / "backups")
    assert succeeded.returncode == 0, succeeded.stderr
    (root / "docker.log").write_text("")
    retry = run("upgrade", "--image-tag", "c" * 40, "--output-dir", root / "backups")
    assert retry.returncode == 1
    assert "当前环境不一致" in retry.stderr
    commands = [json.loads(line) for line in (root / "docker.log").read_text().splitlines()]
    assert not any("build" in command or "stop" in command for command in commands)


def test_scheduled_retention_only_removes_verified_tool_bundles(operator):
    """Catches retention deleting arbitrary directories or backups never verified offsite."""
    run, root = operator
    remote = offsite_transport(root)
    backups = root / "backups"
    first = run("scheduled", "--output-dir", backups, "--remote", remote, "--keep", "1")
    assert first.returncode == 0, first.stderr
    first_bundle = Path(first.stdout.strip())
    local_only = run("backup", "--output-dir", backups)
    unverified_bundle = Path(local_only.stdout.strip())
    unrelated = backups / "personal-document"
    unrelated.write_text("keep me")
    second = run("scheduled", "--output-dir", backups, "--remote", remote, "--keep", "1")
    assert second.returncode == 0, second.stderr
    newest = Path(second.stdout.strip())
    assert not first_bundle.exists()
    assert newest.is_dir()
    assert unverified_bundle.is_dir()
    assert unrelated.read_text() == "keep me"
    assert (root / "remote" / first_bundle.name / "backup.sqlite3").exists()
    assert (newest / ".offsite-verified.json").is_file()


@pytest.mark.parametrize("failure", ["FAKE_TRANSFER_FAIL", "FAKE_REMOTE_CORRUPT"])
def test_failed_offsite_keeps_history_and_reports_local_snapshot(operator, failure):
    """Catches claiming offsite success or pruning after transfer/checksum failure."""
    run, root = operator
    remote = offsite_transport(root)
    first = run("scheduled", "--output-dir", root / "backups", "--remote", remote, "--keep", "1")
    assert first.returncode == 0, first.stderr
    result = run("scheduled", "--output-dir", root / "backups", "--remote", remote, "--keep", "1", **{failure: "yes"})
    assert result.returncode == 1
    assert "本机快照已保留" in result.stderr
    assert Path(first.stdout.strip()).exists()
    assert len(list((root / "backups").glob("*/manifest.json"))) == 2
    assert len(list((root / "backups").glob("*/.offsite-verified.json"))) == 1
