import json
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import Settings
from app.erp.permissions import parse_permissions


def configuration(**overrides):
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:synthetic@localhost/test",
        session_key=Fernet.generate_key().decode(),
        erp_base_url="http://127.0.0.1:19999/jshERP-boot",
        **overrides,
    )


SAMPLES = json.loads(
    (
        Path(__file__).resolve().parents[2] / "docs/integrations/samples/"
        "talk2stock-e72aae4-v1/read-responses.json"
    ).read_text()
)


def permissions(name):
    row = SAMPLES["users"][name]
    return parse_permissions(
        *(row[key]["body"]["data"] for key in ["buttons", "price", "role", "depots"])
    )
