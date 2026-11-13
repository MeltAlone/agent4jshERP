"""Prepare an independent local stack; retain existing settings and private credentials."""

import base64
import importlib.util
import json
import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path

from check_vendor import ROOT, check_vendor, digest

USERS = (
    "fixture-owner",
    "fixture-buyer",
    "fixture-readonly",
    "fixture-masked",
    "fixture-other",
    "admin",
)
ERP_KEYS = ("A4J_ERP_DB_PASSWORD", "A4J_ERP_REDIS_PASSWORD")


def fixture_sql(passwords: dict) -> str:
    path = ROOT / "integrations/jsh-erp/verification/prepare.py"
    spec = importlib.util.spec_from_file_location("erp_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fixture_sql(passwords)


def read_env(raw: str) -> dict:
    values = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            key = key.strip()
            if key in values:
                raise ValueError("Duplicate configuration field: " + key)
            values[key] = value.strip().strip("\"'")
    return values


def write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)


def initialize(root: Path = ROOT) -> bool:
    vendor = check_vendor(root)
    env = root / ".env"
    output = root / ".runtime/erp"
    if env.is_symlink() or output.is_symlink():
        raise ValueError("Private configuration must not be a symlink")
    raw = env.read_text() if env.exists() else ""
    values = read_env(raw)
    if output.exists():
        files = ("manifest.json", "credentials.json", "init.sql")
        if not all(
            (output / name).is_file() and not (output / name).is_symlink()
            for name in files
        ):
            raise ValueError(
                "Incomplete ERP initialization; restore private files, do not reset database"
            )
        manifest = json.loads((output / "manifest.json").read_text())
        passwords = json.loads((output / "credentials.json").read_text())
        expected_sql = (
            root / "third_party/jshERP/jshERP-boot/docs/schema.sql"
        ).read_text() + fixture_sql(passwords)
        if (
            manifest.get("contract") != vendor["contract"]
            or set(passwords) != set(USERS)
            or manifest.get("init_sql_sha256")
            != digest((output / "init.sql").read_bytes())
            or (output / "init.sql").read_text() != expected_sql
            or any(
                manifest.get("environment_sha256", {}).get(k)
                != digest(values.get(k, "").encode())
                for k in ERP_KEYS
            )
        ):
            raise ValueError(
                "Existing initialization differs; preserve volumes and restore matching configuration"
            )
        return False
    required = ("A4J_DATABASE_URL", "A4J_POSTGRES_PASSWORD", "A4J_SESSION_KEY")
    if raw and not all(values.get(key) for key in required):
        raise ValueError(
            "Existing .env is missing database/session fields; restore them before initialization"
        )
    if not raw:
        password = secrets.token_urlsafe(32)
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        raw = (
            "A4J_ENVIRONMENT=development\n"
            f"A4J_DATABASE_URL=postgresql+psycopg://assistant:{password}@127.0.0.1:15432/assistant\n"
            f"A4J_POSTGRES_PASSWORD={password}\nA4J_SESSION_KEY={key}\n"
            "A4J_ERP_BASE_URL=http://127.0.0.1:18081/jshERP-boot\n"
            "A4J_PUBLIC_ORIGIN=http://127.0.0.1:18081\n"
            "A4J_CONNECTION_ID=jsh-bundled-38ad91e\nA4J_ERP_CONTRACT=jsh-38ad91e-v1\n"
            "A4J_PURCHASE_WRITES_ENABLED=false\n"
        )
        values = read_env(raw)
    appended = ""
    for key in ERP_KEYS:
        if key not in values:
            values[key] = secrets.token_urlsafe(32)
            appended += f"{key}={values[key]}\n"
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", values[key]):
            raise ValueError("Requires a random URL-safe value: " + key)
    passwords = {name: secrets.token_urlsafe(24) for name in USERS}
    sql = (
        root / "third_party/jshERP/jshERP-boot/docs/schema.sql"
    ).read_text() + fixture_sql(passwords)
    manifest = {
        "contract": vendor["contract"],
        "source_commit": vendor["commit"],
        "fixture_version": 1,
        "data": "synthetic-only",
        "init_sql_sha256": digest(sql.encode()),
        "environment_sha256": {k: digest(values[k].encode()) for k in ERP_KEYS},
    }
    output.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="erp-init-", dir=output.parent))
    try:
        for name, content in (
            ("init.sql", sql),
            ("credentials.json", json.dumps(passwords, indent=2) + "\n"),
            ("manifest.json", json.dumps(manifest, indent=2) + "\n"),
        ):
            write_private(temporary / name, content)
        if env.exists():
            if env.read_text() != raw:
                raise ValueError("Configuration changed during initialization")
            if appended:
                with env.open("a") as stream:
                    stream.write(("" if raw.endswith("\n") else "\n") + appended)
        else:
            write_private(env, raw + appended)
        os.chmod(env, 0o600)
        temporary.rename(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return True


if __name__ == "__main__":
    try:
        created = initialize()
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit(f"Initialization failed: {error}") from None
    print(
        "Local stack initialized; private credentials: .runtime/erp/credentials.json"
        if created
        else "Existing local settings and ERP fixtures verified; no credentials changed"
    )
