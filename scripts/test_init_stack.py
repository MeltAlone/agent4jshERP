"""Local initialization preserves credentials and rejects altered or partial fixtures."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from check_vendor import ROOT, check_vendor, digest
from init_stack import initialize


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(ROOT / "third_party", self.root / "third_party")

    def test_fresh_and_repeat_preserve_every_private_file(self):
        self.assertTrue(initialize(self.root))
        paths = [self.root / ".env", *list((self.root / ".runtime/erp").iterdir())]
        before = {p: p.read_bytes() for p in paths}
        self.assertFalse(initialize(self.root))
        self.assertEqual(before, {p: p.read_bytes() for p in paths})
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in paths))

    def test_existing_settings_remain_byte_prefix(self):
        raw = (
            "# retained settings\nA4J_DATABASE_URL=existing\nA4J_POSTGRES_PASSWORD=existing\n"
            'A4J_SESSION_KEY=existing\nA4J_MODEL_API_KEY="private-do-not-change"'
        )
        (self.root / ".env").write_text(raw)
        initialize(self.root)
        self.assertTrue((self.root / ".env").read_text().startswith(raw + "\n"))
        self.assertFalse(initialize(self.root))

    def test_missing_private_file_is_rejected_without_replacing_credentials(self):
        initialize(self.root)
        credentials = self.root / ".runtime/erp/credentials.json"
        before = credentials.read_bytes()
        (self.root / ".runtime/erp/init.sql").unlink()
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            initialize(self.root)
        self.assertEqual(credentials.read_bytes(), before)

    def test_changed_password_is_rejected(self):
        initialize(self.root)
        path = self.root / ".env"
        path.write_text(
            path.read_text().replace(
                "A4J_ERP_DB_PASSWORD=", "A4J_ERP_DB_PASSWORD=changed"
            )
        )
        with self.assertRaisesRegex(ValueError, "differs"):
            initialize(self.root)

    def test_non_public_sql_rejected_even_with_updated_manifest(self):
        vendor = self.root / "third_party/jshERP"
        sql = vendor / "jshERP-boot/docs/schema.sql"
        sql.write_text(sql.read_text() + "INSERT INTO `jsh_user` VALUES ('private');\n")
        path = vendor / "source-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["public_schema_sha256"] = digest(sql.read_bytes())
        manifest["files"]["jshERP-boot/docs/schema.sql"]["sha256"] = digest(
            sql.read_bytes()
        )
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Public schema must"):
            check_vendor(self.root)


if __name__ == "__main__":
    unittest.main()
