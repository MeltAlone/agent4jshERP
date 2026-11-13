"""防止验证夹具意外导入上游业务数据或覆盖已有凭据。"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare


class PrepareTests(unittest.TestCase):
    def test_rejects_unknown_source(self):
        with self.assertRaisesRegex(ValueError,"快照不匹配"):
            prepare.extract_public_sql(b"unknown version")

    def test_extraction_excludes_business_rows(self):
        raw = ("CREATE TABLE `jsh_user` (\n `id` bigint\n) ENGINE = InnoDB;\n"
               "INSERT INTO `jsh_user` VALUES (1, 'private-user');\n"
               "INSERT INTO `jsh_function` VALUES (21, 'public-menu');\n").encode()
        with patch.object(prepare,"SQL_SHA256",hashlib.sha256(raw).hexdigest()):
            result=prepare.extract_public_sql(raw)
        self.assertIn("CREATE TABLE `jsh_user`",result)
        self.assertIn("INSERT INTO `jsh_function`",result)
        self.assertNotIn("private-user",result)
        self.assertNotIn("INSERT INTO `jsh_user`",result)

    def test_private_files_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"jshERP-boot/docs").mkdir(parents=True)
            (root/"jshERP-boot/docs/jsh_erp.sql").write_bytes(b"public-schema")
            out=root/"fixture"
            with patch.object(prepare,"extract_public_sql",return_value="-- schema\n"):
                prepare.prepare(root,"erp:tested","web:tested",out)
                original=(out/"credentials.json").read_bytes()
                with self.assertRaises(FileExistsError):
                    prepare.prepare(root,"erp:tested","web:tested",out)
            self.assertEqual(original,(out/"credentials.json").read_bytes())
            for child in out.iterdir():
                self.assertEqual(child.stat().st_mode & 0o777,0o600)
            sql=(out/"init.sql").read_text()
            for password in json.loads(original).values():
                self.assertNotIn(password,sql)
            self.assertIn("jsh_material_current_stock",sql)
            self.assertIn("'4,7'",sql)

    def test_rejects_compose_injection(self):
        with self.assertRaises(ValueError):
            prepare.prepare(Path('/unused'),"erp:latest\nSECRET=oops","web:latest",Path('/unused'))


if __name__ == '__main__':
    unittest.main()
