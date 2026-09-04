import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALID_SECRET_KEY = "test-only-secret-key-with-more-than-fifty-characters-1234567890"


def _example_environment():
    values = {}
    for line in (PROJECT_ROOT / ".env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_production_settings_require_secret_key():
    env = os.environ.copy()
    env.pop("DJANGO_SECRET_KEY", None)
    env["DJANGO_DEBUG"] = "false"
    result = subprocess.run([sys.executable, "-c", "import tracker.settings"], text=True, capture_output=True, env=env)
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_production_settings_reject_example_secret_key():
    env = os.environ.copy()
    env["DJANGO_DEBUG"] = "false"
    env["DJANGO_SECRET_KEY"] = _example_environment()["DJANGO_SECRET_KEY"]
    result = subprocess.run(
        [sys.executable, "-c", "import tracker.settings"],
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
    )

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


@pytest.mark.parametrize("secret_key", ["short-but-not-placeholder", "x" * 64])
def test_production_settings_reject_weak_secret_key(secret_key):
    env = os.environ.copy()
    env["DJANGO_DEBUG"] = "false"
    env["DJANGO_SECRET_KEY"] = secret_key
    result = subprocess.run(
        [sys.executable, "-c", "import tracker.settings"],
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
    )

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_example_hosts_allow_container_healthcheck():
    env = os.environ.copy()
    env["DJANGO_DEBUG"] = "false"
    env["DJANGO_SECRET_KEY"] = VALID_SECRET_KEY
    env["DJANGO_ALLOWED_HOSTS"] = _example_environment()["DJANGO_ALLOWED_HOSTS"]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import django; django.setup(); "
                "from django.test import Client; "
                "response = Client().get('/health/', HTTP_HOST='127.0.0.1:8000', "
                "HTTP_X_FORWARDED_PROTO='https'); "
                "print(response.status_code)"
            ),
        ],
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )

    assert result.stdout.strip() == "200"


def test_database_path_uses_data_dir(tmp_path):
    env = os.environ.copy()
    env["DJANGO_DEBUG"] = "true"
    env["DATA_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", "from tracker.settings import DATABASES; print(DATABASES['default']['NAME'])"],
        text=True, capture_output=True, env=env, check=True,
    )
    assert Path(result.stdout.strip()) == tmp_path / "app.sqlite3"
