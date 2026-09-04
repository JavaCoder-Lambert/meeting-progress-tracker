import os
import subprocess
import sys
from pathlib import Path


def test_production_settings_require_secret_key():
    env = os.environ.copy()
    env.pop("DJANGO_SECRET_KEY", None)
    env["DJANGO_DEBUG"] = "false"
    result = subprocess.run([sys.executable, "-c", "import tracker.settings"], text=True, capture_output=True, env=env)
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_database_path_uses_data_dir(tmp_path):
    env = os.environ.copy()
    env["DJANGO_DEBUG"] = "true"
    env["DATA_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", "from tracker.settings import DATABASES; print(DATABASES['default']['NAME'])"],
        text=True, capture_output=True, env=env, check=True,
    )
    assert Path(result.stdout.strip()) == tmp_path / "app.sqlite3"
