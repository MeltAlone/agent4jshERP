"""Legacy host-only settings generator; use init_stack.py for the complete bundled stack."""
import argparse
import base64
import os
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".env"))
    args = parser.parse_args()
    password = secrets.token_urlsafe(32)
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    content = (
        "A4J_ENVIRONMENT=development\n"
        f"A4J_DATABASE_URL=postgresql+psycopg://assistant:{password}@127.0.0.1:15432/assistant\n"
        f"A4J_POSTGRES_PASSWORD={password}\nA4J_SESSION_KEY={key}\n"
        "A4J_ERP_BASE_URL=http://127.0.0.1:19999/jshERP-boot\n"
        "A4J_PUBLIC_ORIGIN=http://127.0.0.1:18081\n"
        "A4J_PURCHASE_WRITES_ENABLED=false\n"
    )
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
    print("已创建本地配置；原文件不会被覆盖，密钥不会输出。")


if __name__ == "__main__":
    main()
