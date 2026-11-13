"""Opt-in recovery checks against the bundled synthetic ERP; preserve all evidence.

Creates at most two unreviewed orders for fixture-other. No model calls, stock
receipts, payments or approvals. The normal application schema is never used.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import load_settings  # noqa: E402
from app.erp.client import ERPClient  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import ExecutionJob, now  # noqa: E402
from app.purchases.worker import claim, handle  # noqa: E402

BASE = "http://127.0.0.1:18081"
PREFIX = "/assistant/api/v1"
CRASH_EXIT = 73
SCENARIOS = ("response_lost", "crash_after_post", "crash_after_mark")


class VerificationError(RuntimeError):
    """Only fixed diagnostic text may cross the CLI boundary."""


def require(value, message):
    if not value:
        raise VerificationError(message)


def configuration():
    settings = load_settings()
    manifest = json.loads((ROOT / ".runtime/erp/manifest.json").read_text())
    url = make_url(settings.database_url.get_secret_value())
    require(
        settings.environment == "development"
        and settings.public_origin == BASE
        and url.host == "127.0.0.1"
        and url.port == 15432
        and url.database == "assistant"
        and manifest.get("data") == "synthetic-only"
        and manifest.get("contract") == "jsh-38ad91e-v1",
        "Requires the bundled local synthetic development stack",
    )
    return settings.model_copy(
        update={
            "erp_base_url": BASE + "/jshERP-boot",
            "erp_contract": "jsh-38ad91e-v1",
            "connection_id": "jsh-bundled-38ad91e",
            "purchase_writes_enabled": True,
            "reconciliation_delays": (1, 1),
            "model_base_url": None,
            "model_name": None,
            "model_api_key": None,
        }
    )


def database(settings, schema=None):
    require(
        schema is None or re.fullmatch(r"a4j_fault_[0-9a-f]{32}", schema),
        "Invalid schema",
    )
    options = {"connect_timeout": 5}
    if schema:
        options["options"] = "-csearch_path=" + schema
    return create_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args=options,
    )


def mysql(sql):
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "erp-mysql",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --default-character-set=utf8mb4 '
            '--batch --raw --skip-column-names -uroot jsh_erp -e "$1"',
            "sh",
            sql,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    require(result.returncode == 0, "Independent ERP SQL read failed")
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def baseline():
    return mysql("""SELECT JSON_OBJECT(
      'heads', (SELECT COUNT(*) FROM jsh_depot_head),
      'items', (SELECT COUNT(*) FROM jsh_depot_item),
      'payments', (SELECT COUNT(*) FROM jsh_account_head),
      'payment_items', (SELECT COUNT(*) FROM jsh_account_item),
      'stocks', (SELECT JSON_ARRAYAGG(JSON_ARRAY(material_id,depot_id,current_number))
                 FROM jsh_material_current_stock));""")[0]


def actual_order(number):
    require(re.fullmatch(r"CGDD[0-9]{8,32}", number), "Invalid ERP number")
    return mysql(
        """SELECT JSON_OBJECT('id',h.id,'number',h.number,'type',h.type,
      'sub_type',h.sub_type,'status',h.status,'creator',h.creator,'tenant',h.tenant_id,
      'supplier',h.organ_id,'total',h.total_price,'paid',h.change_amount,
      'deposit',h.deposit,'discount',h.discount_money,'remark',h.remark,
      'lines',(SELECT JSON_ARRAYAGG(JSON_OBJECT('extend',i.material_extend_id,
        'quantity',i.oper_number,'basic',i.basic_number,'unit',i.material_unit,
        'price',i.unit_price,'amount',i.all_price,'tax',i.tax_money,'depot',i.depot_id))
        FROM jsh_depot_item i WHERE i.header_id=h.id))
      FROM jsh_depot_head h WHERE h.number='"""
        + number
        + "';"
    )


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, log_path, lose_response=False):
        self.log_path, self.lose_response = log_path, lose_response

    async def handle_async_request(self, request):
        writing = request.method == "POST" and request.url.path.endswith(
            "/depotHead/addDepotHeadAndDetail"
        )
        if writing:
            # Persist a counter before touching the network; never store headers/body.
            with open(self.log_path, "a", encoding="utf-8") as log:
                log.write("create_attempt\n")
                log.flush()
                os.fsync(log.fileno())
        async with httpx.AsyncHTTPTransport(retries=0) as upstream:
            response = await upstream.handle_async_request(request)
            await response.aread()
        if writing and self.lose_response:
            await response.aclose()
            raise httpx.ReadError("Injected response loss after real ERP response", request=request)
        return response


def worker_once(schema, scenario, log_path, writes_disabled):
    settings = configuration().model_copy(update={"purchase_writes_enabled": not writes_disabled})
    engine = database(settings, schema)

    def factory(config, token):
        return ERPClient(
            config,
            token,
            transport=RecordingTransport(log_path, lose_response=scenario == "response_lost"),
        )

    def boundary(stage, execution):
        if (scenario, stage) in {
            ("crash_after_post", "after_post"),
            ("crash_after_mark", "after_mark"),
        }:
            os._exit(CRASH_EXIT)

    try:
        lease = claim(engine)
        require(lease is not None, "No execution available")
        handle(engine, settings, lease, client_factory=factory, boundary_hook=boundary)
    finally:
        engine.dispose()


class Check:
    def __init__(self, settings, report_dir, schema):
        self.settings, self.directory = settings, report_dir
        self.schema = schema
        admin = database(settings)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        admin.dispose()
        self.engine = database(settings, self.schema)
        config = Config(str(ROOT / "backend/alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        app = create_app(settings)
        app.state.engine, app.state.settings = self.engine, settings
        self.client = TestClient(app, base_url=BASE)
        self.login("fixture-other", expected=("9101", "9101"))

    def request(self, method, path, expected=200, **kwargs):
        response = self.client.request(method, PREFIX + path, headers=self.headers, **kwargs)
        require(
            response.status_code == expected,
            f"{method} {path.split('/')[-1]} returned {response.status_code}",
        )
        return response.json() if response.content else None

    def login(self, name, expected):
        credentials = json.loads((ROOT / ".runtime/erp/credentials.json").read_text())
        with httpx.Client(timeout=10, trust_env=False) as http:
            login = http.post(
                BASE + "/jshERP-boot/user/login",
                json={
                    "loginName": name,
                    "password": hashlib.md5(credentials[name].encode()).hexdigest(),
                },
            ).json()["data"]
        require(login.get("msgTip") == "user can login", "Synthetic login failed")
        result = self.client.post(
            PREFIX + "/session/exchange",
            json={},
            headers={
                "Origin": BASE,
                "X-Access-Token": login["token"],
            },
        )
        require(result.status_code == 200, "Session exchange failed")
        state = self.client.get(PREFIX + "/session").json()
        principal = state["principal"]
        require(
            (principal["tenant_id"], principal["erp_user_id"]) == expected,
            "Unexpected fixture identity",
        )
        self.headers = {
            "Origin": BASE,
            "X-Assistant-Generation": state["generation"],
            "X-CSRF-Token": state["csrf_token"],
        }

    def recover(self):
        # Only accelerate the test schema's lease; the normal worker is untouched.
        with Session(self.engine) as db, db.begin():
            for job in db.exec(select(ExecutionJob).where(ExecutionJob.status != "done")):
                job.available_at = now() - timedelta(seconds=1)
                job.lease_until = now() - timedelta(seconds=1)
                db.add(job)

    def work(self, scenario, log_path, *, crash=False, writes_disabled=False):
        args = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            self.schema,
            "--scenario",
            scenario,
            "--attempt-log",
            str(log_path),
        ]
        if writes_disabled:
            args.append("--writes-disabled")
        result = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=60, check=False)
        require(
            result.returncode == (CRASH_EXIT if crash else 0),
            "Test worker exited unexpectedly",
        )

    def run(self, scenario):
        started = monotonic()
        conversation = self.request("POST", "/conversations", expected=201)["id"]
        draft = self.request(
            "POST",
            f"/conversations/{conversation}/drafts",
            expected=201,
            json={
                "command_id": str(uuid4()),
                "input": {
                    "lines": [
                        {
                            "extend_id": 20111,
                            "quantity": "1",
                            "unit": "箱",
                            "unit_price": "30.125",
                        }
                    ],
                    "supplier": {"id": 4101, "name": "隔离租户供应商"},
                    "order_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                    "remark": "synthetic recovery acceptance: " + scenario,
                },
            },
        )
        path = "/drafts/" + draft["id"]
        review = self.request("POST", path + "/confirmation", json={"expected_version": 1})
        body = {"version": 1, "challenge": review["challenge"]}

        def confirm(_=None):
            return self.request(
                "POST",
                path + "/confirm",
                expected=202,
                json={**body, "request_id": str(uuid4())},
            )

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(confirm, range(2)))
        require(results[0]["id"] == results[1]["id"], "Duplicate execution")
        execution_path = "/executions/" + results[0]["id"]
        (self.directory / (scenario + ".execution.json")).write_text(
            json.dumps(
                {
                    "schema": self.schema,
                    "draft_id": draft["id"],
                    "execution_id": results[0]["id"],
                    "scenario": scenario,
                },
                indent=2,
            )
        )
        log_path = self.directory / (scenario + ".attempts")
        self.work(scenario, log_path, crash=scenario.startswith("crash_"))
        sent = self.request("GET", execution_path)
        require(
            sent["status"] == ("reconciling" if scenario == "response_lost" else "dispatching"),
            "Unexpected post-fault state",
        )
        number = sent["erp_number"]
        after_send = actual_order(number)
        require(
            len(after_send) == (0 if scenario == "crash_after_mark" else 1),
            "Unexpected ERP order count at fault boundary",
        )
        if scenario == "crash_after_post":
            self.request("DELETE", "/session", expected=204)
            self.recover()
            self.work("normal", log_path, writes_disabled=True)
            self.login("fixture-masked", expected=("9001", "9004"))
            self.request("GET", execution_path, expected=404)
            self.request("POST", execution_path + "/reconcile", expected=404)
            self.login("fixture-other", expected=("9101", "9101"))
            require(
                self.request("GET", execution_path)["status"] == "auth_required",
                "Lost session must require original identity",
            )
            self.request("POST", execution_path + "/reconcile", expected=202)
        else:
            self.recover()
        self.work("normal", log_path, writes_disabled=True)
        if scenario == "crash_after_mark":
            self.recover()
            self.work("normal", log_path, writes_disabled=True)
        final = self.request("GET", execution_path)
        require(
            final["status"]
            == ("needs_attention" if scenario == "crash_after_mark" else "succeeded"),
            "Recovery failed",
        )
        require(confirm()["id"] == final["id"], "Replay changed execution identity")
        self.request(
            "POST",
            path + "/reopen",
            expected=409,
            json={"expected_version": 1, "command_id": str(uuid4())},
        )
        count = len(log_path.read_text().splitlines()) if log_path.exists() else 0
        require(
            count == (0 if scenario == "crash_after_mark" else 1),
            "Repeated create attempt",
        )
        rows = actual_order(number)
        require(len(rows) == count, "SQL order count differs from creation attempts")
        if count:
            row, line = rows[0], rows[0]["lines"][0]
            require(
                row["type"] == "其它"
                and row["sub_type"] == "采购订单"
                and row["status"] == "0"
                and row["creator"] == row["tenant"] == 9101
                and row["supplier"] == 4101
                and float(row["total"]) == -30.13
                and row["paid"] == 0
                and row["deposit"] in (None, 0)
                and row["discount"] == 0
                and len(row["lines"]) == 1
                and line["extend"] == 20111
                and line["unit"] == "箱"
                and line["quantity"] == 1
                and line["basic"] == 12
                and float(line["price"]) == 30.125
                and float(line["amount"]) == 30.13
                and line["tax"] == 0
                and line["depot"] is None,
                "Independent SQL contents differ from confirmed snapshot",
            )
            require(
                final["receipt"]["verified"] and final["receipt"]["id"] == row["id"],
                "Receipt differs from actual ERP head",
            )
        return {
            "scenario": scenario,
            "execution_id": final["id"],
            "erp_number": number,
            "status": final["status"],
            "create_attempts": count,
            "actual_orders": rows,
            "elapsed_ms": round((monotonic() - started) * 1000),
            "model_calls": 0,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-synthetic-orders", action="store_true")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--scenario", choices=(*SCENARIOS, "normal"), help=argparse.SUPPRESS)
    parser.add_argument("--attempt-log", help=argparse.SUPPRESS)
    parser.add_argument("--writes-disabled", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker_once(args.worker, args.scenario, args.attempt_log, args.writes_disabled)
        return
    require(
        args.allow_synthetic_orders,
        "Pass --allow-synthetic-orders to create two fixture orders",
    )
    os.chdir(ROOT)
    settings = configuration()
    directory = ROOT / ".runtime/purchase-faults" / uuid4().hex
    directory.mkdir(parents=True, mode=0o700)
    report = {
        "contract": settings.erp_contract,
        "environment": "bundled-synthetic-only",
        "started_at": now().isoformat(),
        "cases": [],
        "passed": False,
    }
    path = directory / "report.json"
    try:
        report["before"] = baseline()
        report["schema"] = "a4j_fault_" + uuid4().hex
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        check = Check(settings, directory, report["schema"])
        for scenario in SCENARIOS:
            report["cases"].append(check.run(scenario))
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        report["after"] = baseline()
        before, after = report["before"], report["after"]
        require(
            after["heads"] == before["heads"] + 2
            and after["items"] == before["items"] + 2
            and after["payments"] == before["payments"]
            and after["payment_items"] == before["payment_items"]
            and sorted(after["stocks"]) == sorted(before["stocks"]),
            "Global order, inventory or payment baseline changed unexpectedly",
        )
        report["passed"] = True
    finally:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("Evidence retained: " + str(path.relative_to(ROOT)))
    print("Three real ERP recovery scenarios passed; two synthetic orders, zero repeat sends")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print driver, HTTP or validation exceptions containing private inputs.
        diagnostic = str(error) if isinstance(error, VerificationError) else type(error).__name__
        print("Verification stopped: " + diagnostic, file=sys.stderr)
        sys.exit(1)
