#!/usr/bin/env python3
"""仅对本地虚构夹具发起只读契约探测；不保存令牌或完整用户对象。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent


class FixtureClient:
    def __init__(self, base):
        url = urllib.parse.urlparse(base)
        if url.hostname not in ("127.0.0.1", "localhost") or url.port != 19999 or url.scheme != "http":
            raise ValueError("仅允许本地隔离夹具的 19999 端口。")
        self.base = base.rstrip("/")
        self.token = None

    def request(self, path, params=None, body=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Content-Type":"application/json"}
        if self.token:
            headers["X-Access-Token"] = self.token
        request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            response = urllib.request.urlopen(request,timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read().decode()
            try:
                data = json.loads(raw)
            except ValueError:
                data = raw
            return {"http_status":response.status,"body":data}

    def login(self, name, password):
        response = self.request("/user/login",body={"loginName":name,"password":hashlib.md5(password.encode()).hexdigest()})
        data = response["body"]["data"]
        if data.get("msgTip") != "user can login" or not data.get("token"):
            raise ValueError(f"虚构账号登录失败：{name}")
        self.token = data["token"]
        user = data["user"]
        if user["loginName"] != name or user["id"] not in (9001,9002,9003,9004,9101,9999):
            raise ValueError("响应不属于虚构夹具；停止捕获。")


def capture(base, credentials, number=None):
    samples = {"captured_at":datetime.now(timezone.utc).isoformat(),"contract":"talk2stock-e72aae4-v1","users":{}}
    for name,password in credentials.items():
        client = FixtureClient(base)
        client.login(name,password)
        session = client.request("/user/getUserSession")
        user = session["body"]["data"]["user"]
        session["body"]["data"]["user"] = {k:user.get(k) for k in ("id","tenantId","loginName","ismanager","status")}
        record = {"session":session}
        for key,path in {"buttons":"/user/getUserBtnByCurrentUser","price":"/user/getCurrentPriceLimit",
                         "role":"/user/getRoleTypeByCurrentUser","depots":"/depot/findDepotByCurrentUser"}.items():
            record[key] = client.request(path)
        if number:
            record["order_detail_visibility_probe"] = client.request("/depotHead/getDetailByNumber",{"number":number})
        record["orders"] = client.request("/depotHead/list",{"search":json.dumps({"type":"其它","subType":"采购订单"}),"currentPage":1,"pageSize":100})
        if name in ("fixture-buyer","fixture-masked","fixture-other"):
            depot = 6101 if name == "fixture-other" else 6001
            record["materials"] = client.request("/material/findBySelect",{"q":"虚构可乐","page":1,"rows":100,"depotId":depot})
            record["supplier"] = client.request("/supplier/findBySelect_sup",body={"key":"虚构","limit":20})
        if name in ("fixture-buyer", "fixture-masked"):
            record["priced_material"] = client.request("/material/findByIdWithBarCode",{"meId":20011,"mpList":""})
        if name == "fixture-buyer":
            record["material_base"] = client.request("/material/findById",{"id":3001})
            record["material_extend"] = client.request("/material/findByIdWithBarCode",{"meId":20011,"mpList":""})
            record["units"] = client.request("/unit/list",{"currentPage":1,"pageSize":100})
            record["stock_bottle"] = client.request("/depotItem/findStockByDepotAndBarCode",{"barCode":"FIX900110","depotId":6001})
            record["stock_case"] = client.request("/depotItem/findStockByDepotAndBarCode",{"barCode":"FIX900111","depotId":6001})
            record["stock_missing"] = client.request("/depotItem/findStockByDepotAndBarCode",{"barCode":"FIX-NOT-FOUND","depotId":6001})
            record["supplier_missing"] = client.request("/supplier/findBySelect_sup",body={"key":"FIX-NOT-FOUND","limit":20})
            record["supplier_invalid_id"] = client.request("/supplier/findBySelect_sup",body={"organId":"INVALID-NUMBER","limit":20})
        samples["users"][name] = record
    invalid = FixtureClient(base)
    invalid.token = "fixture-expired-token"
    samples["invalid_session"] = invalid.request("/user/getUserSession")
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--number",help="正常页面创建后要核对可见性的虚构订单号")
    parser.add_argument("--base",default="http://127.0.0.1:19999/jshERP-boot")
    parser.add_argument("--output",type=Path,default=HERE/".runtime/read-samples.json")
    args = parser.parse_args()
    credentials = json.loads((HERE/".runtime/credentials.json").read_text())
    samples = capture(args.base,credentials,args.number)
    args.output.write_text(json.dumps(samples,ensure_ascii=False,indent=2)+"\n")
    print("已捕获虚构数据响应；未包含登录响应、密码或令牌。")
    for name,record in samples["users"].items():
        print(name, {key:value["http_status"] for key,value in record.items()})


if __name__ == "__main__":
    main()
