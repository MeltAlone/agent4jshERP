#!/usr/bin/env python3
"""核验通过正常 ERP 页面保存的虚构订单；本脚本不发送创建或更新请求。"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import subprocess

from capture_reads import FixtureClient

HERE = Path(__file__).resolve().parent


def docker(*args, stdin=None):
    return subprocess.run(["docker",*args],input=stdin,text=True,check=True,capture_output=True).stdout


def database_evidence():
    sql = """SELECT 'heads',count(*) FROM jsh_depot_head UNION ALL SELECT 'items',count(*) FROM jsh_depot_item
UNION ALL SELECT 'account_heads',count(*) FROM jsh_account_head UNION ALL SELECT 'account_items',count(*) FROM jsh_account_item;
SELECT type,sub_type,status,total_price,change_amount,creator FROM jsh_depot_head ORDER BY id;
SELECT material_id,material_extend_id,oper_number,basic_number,unit_price,all_price,depot_id FROM jsh_depot_item ORDER BY id;"""
    return docker("exec","-i","a4j-contract-erp-mysql-1","sh","-c",
                  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --default-character-set=utf8mb4 -N -uroot jsh_erp',stdin=sql)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--number")
    parser.add_argument("--baseline",action="store_true",help="页面创建前记录库存与单据基线")
    args=parser.parse_args()
    credentials=json.loads((HERE/".runtime/credentials.json").read_text())
    client=FixtureClient("http://127.0.0.1:19999/jshERP-boot")
    client.login("fixture-buyer",credentials["fixture-buyer"])
    if args.baseline:
        stocks={f'{depot}-{barcode}':client.request('/depotItem/findStockByDepotAndBarCode',{'barCode':barcode,'depotId':depot})
                for depot in [6001,6002] for barcode in ['FIX900110','FIX900111']}
        evidence=database_evidence()
        assert evidence == "heads\t0\nitems\t0\naccount_heads\t0\naccount_items\t0\n", "需要全新无订单的验证夹具"
        target=HERE/".runtime/before-order.json"
        with target.open("x") as output:
            output.write(json.dumps({"stocks":stocks,"counts":evidence},ensure_ascii=False,indent=2)+"\n")
        print("已记录创建前库存与单据基线。")
        return
    if not args.number:
        parser.error("核验订单需指定 --number；创建前使用 --baseline")
    candidates=client.request("/depotHead/list",{"search":json.dumps({"type":"其它","subType":"采购订单","number":args.number}),"currentPage":1,"pageSize":100})
    page=candidates["body"]["data"]
    assert page["total"] <=100, "样本分页不完整"
    exact=[row for row in page["rows"] if row["number"]==args.number]
    assert len(exact)==1, "样本必须唯一且对本人可见"
    head=client.request("/depotHead/getDetailByNumber",{"number":args.number})
    h=head["body"]["data"]
    assert h["id"]==exact[0]["id"] and h["creator"]==9002 and h["tenantId"]==9001
    assert h["type"]=="其它" and h["subType"]=="采购订单" and h["status"]=="0"
    assert h["organId"]==4001 and h["changeAmount"]==0 and h["deposit"]==0
    assert h["remark"].startswith("fixture-contract-v1")
    items=client.request("/depotItem/getDetailList",{"headerId":h["id"],"linkType":"basic","isReadOnly":"1","mpList":""})
    d=items["body"]["data"]
    rows=[row for row in d["rows"] if "id" in row]
    assert len(rows)==d["total"]==1
    row=rows[0]
    assert row["materialExtendId"]==20011 and row["barCode"]=="FIX900111" and row["unit"]=="箱"
    assert Decimal(str(row["operNumber"]))==Decimal("2.5") and Decimal(str(row["basicNumber"]))==Decimal("30")
    assert Decimal(str(row["unitPrice"]))==Decimal("30.125") and Decimal(str(row["allPrice"]))==Decimal("75.31")
    assert row["taxRate"]==row["taxMoney"]==0 and row["depotId"]==""
    assert Decimal(str(h["totalPrice"]))==Decimal("75.31")
    stocks={f'{depot}-{barcode}':client.request('/depotItem/findStockByDepotAndBarCode',{'barCode':barcode,'depotId':depot})
            for depot in [6001,6002] for barcode in ['FIX900110','FIX900111']}
    before=json.loads((HERE/".runtime/before-order.json").read_text())
    assert stocks==before["stocks"], "订单创建前后库存变化"
    log=docker("exec","a4j-contract-erp-web-1","cat","/var/log/nginx/fixture-order.log")
    events=[json.loads(line) for line in log.splitlines()]
    matching=[e for e in events if json.loads(json.loads(e["body"])["info"])["number"]==args.number]
    assert len(matching)==1, "页面创建请求不是一次"
    request=json.loads(matching[0]["body"])
    info=json.loads(request["info"]); request_rows=json.loads(request["rows"])
    assert info["totalPrice"]==-75.31 and info["status"]=="0" and info["changeAmount"]==0
    assert request_rows[0]["depotId"]=="" and request_rows[0]["barCode"]=="FIX900111"
    evidence=database_evidence()
    assert evidence.startswith("heads\t1\nitems\t1\naccount_heads\t0\naccount_items\t0\n")
    assert "其它\t采购订单\t0\t-75.310000\t0.000000\t9002" in evidence
    result={"number":args.number,"request":request,"head":head,"items":items,"candidates":candidates,
            "stocks_before":before["stocks"],"stocks_after":stocks,"database_before":before["counts"],
            "database_after":evidence,"checks":"passed; no ERP write from this script"}
    (HERE/".runtime/order-evidence.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print("真实页面订单核验通过：一次创建、本人可见、未审核、数量/金额/单位一致、库存不变、无入库或付款单。")


if __name__=='__main__':
    main()
