#!/usr/bin/env python3
"""从已核实的上游结构生成独立、可丢弃的 ERP 契约验证数据。"""
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

SQL_SHA256 = "6d3da401054267a58a616ed89cc496a6b43038a485e075c74971e02215e6328f"
SNAPSHOT = "talk2stock-e72aae4-v1"
FIXTURE_VERSION = 1
HERE = Path(__file__).resolve().parent


def extract_public_sql(raw: bytes) -> str:
    if hashlib.sha256(raw).hexdigest() != SQL_SHA256:
        raise ValueError("源 SQL 与已核实快照不匹配；先核实新契约，禁止自动套用。")
    source = raw.decode("utf-8")
    tables = re.findall(r"CREATE TABLE `jsh_\w+`\s*\(.*?\n\) ENGINE[^;]+;", source, re.S)
    menus = re.findall(r"^INSERT INTO `jsh_function` VALUES .*?;", source, re.M)
    if not tables or not menus:
        raise ValueError("未找到完整结构和公开菜单。")
    return "SET NAMES utf8mb4;\n" + "\n".join(tables + menus) + "\n"


def sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def fixture_sql(passwords: dict[str, str]) -> str:
    statements = []

    def insert(table, **fields):
        columns = ",".join(f"`{k}`" for k in fields)
        values = ",".join(sql_value(v) for v in fields.values())
        statements.append(f"INSERT INTO `jsh_{table}` ({columns}) VALUES ({values});")

    insert("sequence", seq_name="depot_number_seq", min_value=1, max_value=999999999999999999, current_val=1000, increment_val=1, remark="虚构验证序列")
    users = [(9001, "fixture-owner", 9001, 0, 8001),
             (9002, "fixture-buyer", 9001, 1, 8001),
             (9003, "fixture-readonly", 9001, 1, 8002),
             (9004, "fixture-masked", 9001, 1, 8003),
             (9101, "fixture-other", 9101, 0, 8101),
             (9999, "admin", 0, 0, 8999)]
    for uid, name, tenant, manager, role in users:
        insert("user", id=uid, username=name, login_name=name,
               password=hashlib.md5(passwords[name].encode()).hexdigest(),
               tenant_id=tenant, ismanager=manager, status=0, description="虚构验证账号")
        insert("user_business", type="UserRole", key_id=str(uid), value=f"[{role}]", tenant_id=tenant)
        depots = "[6101]" if tenant == 9101 else "[6001]" if uid != 9001 else "[6001][6002]"
        insert("user_business", type="UserDepot", key_id=str(uid), value=depots, tenant_id=tenant)
    menu_ids = [21, 23, 24, 25, 26, 32, 198, 241, 246]
    for rid, tenant, restricted, masked in [(8001, 9001, False, False), (8002, 9001, True, False),
                                             (8003, 9001, False, True), (8101, 9101, False, False),
                                             (8999, 0, False, False)]:
        insert("role", id=rid, name=f"验证角色{rid}", type="个人数据" if restricted else "全部数据",
               price_limit="4,7" if masked else "", enabled=1, tenant_id=tenant, sort="1")
        buttons = [{"funId":mid, "btnStr":("" if restricted else "1,2,3,7") if mid == 241
                    else "1,3" if mid in (23,25) else ""} for mid in menu_ids]
        insert("user_business", type="RoleFunctions", key_id=str(rid),
               value="".join(f"[{mid}]" for mid in menu_ids),
               btn_str=json.dumps(buttons, ensure_ascii=False), tenant_id=tenant)
    for tenant, name in [(9001, "fixture-owner"), (9101, "fixture-other")]:
        insert("tenant", tenant_id=tenant, login_name=name, user_num_limit=20,
               enabled=1, create_time="2026-01-01 00:00:00", expire_time="2099-12-31 00:00:00")
        insert("system_config", tenant_id=tenant, company_name=f"虚构验证租户{tenant}", depot_flag="1")
    for key, value in {"checkcode_flag":"0", "platform_name":"ERP 契约验证", "register_flag":"0"}.items():
        insert("platform_config", platform_key=key, platform_key_info="隔离验证配置", platform_value=value)
    for depot, name, tenant in [(6001,"验证仓库一",9001),(6002,"验证仓库二",9001),(6101,"隔离租户仓库",9101)]:
        insert("depot", id=depot, name=name, type=0, sort="1", enabled=1,
               is_default=1 if depot != 6002 else 0, tenant_id=tenant, warehousing="0", truckage="0")
    for tenant, offset in [(9001,0),(9101,100)]:
        insert("unit", id=5001+offset, name="瓶/箱=12瓶", basic_unit="瓶", other_unit="箱", ratio="12",
               enabled=1, tenant_id=tenant)
        insert("supplier", id=4001+offset, supplier="虚构饮品供应商" if tenant == 9001 else "隔离租户供应商",
               type="供应商", enabled=1, isystem=1, advance_in="0", begin_need_get="0", begin_need_pay="0",
               all_need_get="0", all_need_pay="0", tax_rate="0", tenant_id=tenant, creator=tenant, sort="1")
        for index, size, price in [(1,"330ml","2.50"),(2,"500ml","3.20"),(3,"550ml","1.50")]:
            mid=3000+offset+index
            insert("material", id=mid, name="虚构矿泉水" if index == 3 else "虚构可乐", standard=size, model=f"fixture-{size}",
                   unit="", unit_id=5001+offset, enabled=1, tenant_id=tenant, remark="纯虚构契约验证商品")
            for extra, unit, cost in [(0,"瓶",price),(1,"箱",str(Decimal(price)*12))]:
                insert("material_extend", id=20000+offset+index*10+extra, material_id=mid,
                       bar_code=f"FIX{tenant}{index}{extra}", commodity_unit=unit, purchase_decimal=cost,
                       commodity_decimal=cost, wholesale_decimal=cost, low_decimal="0",
                       default_flag="1" if extra == 0 else "0", tenant_id=tenant, create_time="2026-01-01 00:00:00")
            for depot, count in ([(6001,120),(6002,48)] if tenant == 9001 else [(6101,24)]):
                insert("material_initial_stock", material_id=mid, depot_id=depot, number=str(count),
                       low_safe_stock="0", high_safe_stock="1000", tenant_id=tenant)
                insert("material_current_stock", material_id=mid, depot_id=depot, current_number=str(count), tenant_id=tenant)
    return "\n".join(statements)+"\n"


def prepare(source: Path, backend: str, web: str, output: Path):
    for image in (backend, web):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", image):
            raise ValueError("镜像名格式无效。")
    public = extract_public_sql((source / "jshERP-boot/docs/jsh_erp.sql").read_bytes())
    passwords = {name:secrets.token_urlsafe(24) for name in
                 ["fixture-owner","fixture-buyer","fixture-readonly","fixture-masked","fixture-other","admin"]}
    db_password = secrets.token_urlsafe(32)
    redis_password = secrets.token_urlsafe(32)
    old_mask = os.umask(0o077)
    try:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        (output / "init.sql").write_text(public + fixture_sql(passwords))
        (output / "credentials.json").write_text(json.dumps(passwords, indent=2)+"\n")
        (output / "compose.env").write_text(f"VERIFY_DB_PASSWORD={db_password}\nVERIFY_REDIS_PASSWORD={redis_password}\n"
             f"VERIFY_BACKEND_IMAGE={backend}\nVERIFY_WEB_IMAGE={web}\n")
        manifest = {"contract":SNAPSHOT,"fixture_version":FIXTURE_VERSION,"source_sql_sha256":SQL_SHA256,
                    "backend_image":backend,"web_image":web,"data":"synthetic-only",
                    "init_sql_sha256":hashlib.sha256((output/"init.sql").read_bytes()).hexdigest()}
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    finally:
        os.umask(old_mask)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--backend-image",required=True)
    parser.add_argument("--web-image",required=True)
    args=parser.parse_args()
    try:
        prepare(args.source,args.backend_image,args.web_image,HERE/".runtime")
    except (ValueError, OSError) as exc:
        parser.exit(1,f"准备失败：{exc}\n")
    print("已生成独立验证数据与本地凭据；未输出密码。按 README 启动隔离 Compose。")


if __name__ == "__main__":
    main()
