"""Verify the fixed ERP source and ensure initialization contains public SQL only."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def check_vendor(root: Path = ROOT) -> dict:
    vendor = root / "third_party/jshERP"
    manifest = json.loads((vendor / "source-manifest.json").read_text())
    for name, record in manifest["files"].items():
        path = vendor / name
        if not path.resolve().is_relative_to(vendor.resolve()) or path.is_symlink():
            raise ValueError("Invalid vendor path")
        if digest(path.read_bytes()) != record["sha256"]:
            raise ValueError(f"Vendor fingerprint mismatch: {name}")
    actual = {str(p.relative_to(vendor)) for p in vendor.rglob("*") if p.is_file()}
    if actual != set(manifest["files"]) | {"NOTICE.md", "source-manifest.json"}:
        raise ValueError("Unrecorded or missing vendor files")
    raw = (vendor / "jshERP-boot/docs/schema.sql").read_bytes()
    if digest(raw) != manifest["public_schema_sha256"]:
        raise ValueError("Public schema fingerprint mismatch")
    source = raw.decode()
    tables = re.findall(
        r"CREATE TABLE `jsh_\w+`\s*\(.*?\n\) ENGINE[^;]+;", source, re.DOTALL
    )
    menus = re.findall(r"^INSERT INTO `jsh_function` VALUES .*?;", source, re.MULTILINE)
    expected = "SET NAMES utf8mb4;\n" + "\n".join(tables + menus) + "\n"
    if source != expected or len(tables) != 32 or len(menus) != 65:
        raise ValueError(
            "Public schema must contain only 32 tables and 65 public menus"
        )
    return manifest


if __name__ == "__main__":
    result = check_vendor()
    print(f"Vendor verified: {result['commit']}, public schema only")
