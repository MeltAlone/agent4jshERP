"""可审阅、可移除的指定 jshERP 快照补丁；拒绝覆盖未知版本与额外修改。"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = {
    "src/components/page/GlobalLayout.vue": "c773552a202537356748ad7929b9292ec60c4406727b6dffc24dbd9b6db30f7d",
    "src/views/material/MaterialList.vue": "8f7ce0bd5fa01274b44674dca729aaab66a936b1090ba650b5161c992f2ec5c8",
    "src/views/bill/PurchaseOrderList.vue": "768a4fe766234f3a351d43c22991445f972ed106cf9be623dd144e8a1699b14c",
}
ADDED = ["AssistantSidebar.vue", "bridge.js", "page-context.js"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def apply(root):
    backup = root / ".a4j-backup"
    if backup.exists():
        raise RuntimeError("补丁备份已存在；请先核对原安装状态")
    originals = {name: (root / name).read_bytes() for name in FILES}
    if any(sha(data) != FILES[name] for name, data in originals.items()):
        raise RuntimeError("源文件不属于已验证快照或已有修改；未写入任何文件")
    target = root / "src/components/assistant"
    if target.exists():
        raise RuntimeError("目标组件目录已存在；拒绝覆盖")
    replacements = {}
    for name, data in originals.items():
        source = data.decode()
        eol = "\r\n" if "\r\n" in source else "\n"
        if name.endswith("GlobalLayout.vue"):
            position = source.rindex("  </a-layout>")
            source = source[:position] + "    <assistant-sidebar />" + eol + source[position:]
            source = source.replace("<script>", "<script>" + eol + "  import AssistantSidebar from '@/components/assistant/AssistantSidebar.vue'", 1)
            source = source.replace("    components: {", "    components: {" + eol + "      AssistantSidebar,", 1)
        else:
            material = name.endswith("MaterialList.vue")
            route, kind = ("/material/material", "material") if material else ("/bill/purchase_order", "purchase_order")
            source = source.replace("<script>", "<script>" + eol + "  import { pageContextMixin } from '@/components/assistant/page-context'" + eol + f"  const AssistantPageContext = pageContextMixin('{route}', '{kind}')", 1)
            old = "mixins:[JeecgListMixin]," if material else "mixins:[JeecgListMixin,BillListMixin],"
            if source.count(old) != 1:
                raise RuntimeError("源文件结构不符；未写入任何文件")
            source = source.replace(old, old.replace("],", ",AssistantPageContext],"))
        replacements[name] = source.encode()
    backup.mkdir()
    manifest = {"original": FILES, "patched": {}, "added": {}}
    for name, data in originals.items():
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(data)
        (root / name).write_bytes(replacements[name])
        manifest["patched"][name] = sha(replacements[name])
    target.mkdir()
    for name in ADDED:
        data = (HERE / name).read_bytes()
        (target / name).write_bytes(data)
        manifest["added"]["src/components/assistant/" + name] = sha(data)
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("已安装指定快照补丁；原文件保存在 .a4j-backup。")


def remove(root):
    backup = root / ".a4j-backup"
    manifest = json.loads((backup / "manifest.json").read_text())
    for name, digest in {**manifest["patched"], **manifest["added"]}.items():
        if sha((root / name).read_bytes()) != digest:
            raise RuntimeError("安装后文件有额外修改；拒绝覆盖或删除")
    for name in manifest["original"]:
        (root / name).write_bytes((backup / name).read_bytes())
    for name in manifest["added"]:
        (root / name).unlink()
    (root / "src/components/assistant").rmdir()
    shutil.rmtree(backup)
    print("已移除补丁并还原原文件；ERP 与助手数据库未改变。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["apply", "remove"])
    parser.add_argument("root", type=Path, help="jshERP-web 目录")
    args = parser.parse_args()
    (apply if args.action == "apply" else remove)(args.root.resolve())


if __name__ == "__main__":
    main()
