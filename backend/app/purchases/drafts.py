"""采购准备领域服务；表单和 Agent 共用，无 ERP 写入。"""

import hashlib
import json
import secrets
from datetime import timedelta
from decimal import Decimal

from app.auth import digest
from app.erp.reads import MaterialID, Search
from app.errors import APIError
from app.events import append_event
from app.history import provenance, readable
from app.models import Confirmation, Conversation, DraftRevision, PurchaseDraft, Task, now
from app.purchases.contracts import DraftInput, money


def content_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def prepare(data: DraftInput, tools, settings):
    tools.permissions.require("purchase_create")
    tools.permissions.require("purchase_prices")
    missing, rows, sources = [], [], []
    if not data.lines:
        missing.append("商品明细")
    supplier = None
    if data.supplier:
        fact = await tools.suppliers(Search(query=data.supplier.name))
        sources.append(fact["source"])
        matches = [
            row
            for row in fact["data"]
            if row["supplier_id"] == data.supplier.id and row["name"] == data.supplier.name
        ]
        if len(matches) != 1:
            raise APIError("supplier_changed", "供应商无法核实，请重新选择", 422)
        supplier = {"id": matches[0]["supplier_id"], "name": matches[0]["name"]}
    else:
        missing.append("供应商")
    if not data.order_date:
        missing.append("订单日期")
    total = Decimal(0)
    identities = set()
    for index, line in enumerate(data.lines):
        if line.extend_id in identities:
            raise APIError("duplicate_line", "相同规格请合并数量", 422)
        identities.add(line.extend_id)
        fact = await tools.material(MaterialID(extend_id=line.extend_id))
        material = fact["data"]
        sources.append(fact["source"])
        unit = line.unit
        # 展示标签仅作为当前已核实条码的精确别名，不解析括号或推断包装换算。
        if unit is not None and unit == material["unit_label"]:
            unit = material["unit"]
        if unit is None:
            missing.append(f"明细{index + 1}单位")
        elif unit != material["unit"] or unit not in material["configured_units"]:
            raise APIError("unit_mismatch", "所选单位与商品规格不符，请重新选择对应条码", 422)
        quantity = Decimal(line.quantity) if line.quantity is not None else None
        price_text = line.unit_price
        price = Decimal(price_text) if price_text is not None else None
        if quantity is None:
            missing.append(f"明细{index + 1}数量")
        elif quantity <= 0:
            raise APIError("invalid_quantity", "数量必须大于零", 422)
        if price is None:
            missing.append(f"明细{index + 1}单价")
        elif price <= 0:
            # 未验证零价格采购契约前关闭零价，不能把屏蔽价格转为零。
            raise APIError("invalid_price", "首版单价必须大于零", 422)
        factor = Decimal(material["configured_units"][unit]) if unit else None
        basic = quantity * factor if quantity is not None and factor is not None else None
        if (
            basic is not None
            and basic.as_tuple().exponent < -6
            and basic != basic.quantize(Decimal("0.000001"))
        ):
            raise APIError("quantity_precision", "换算后基本数量超过六位小数", 422)
        maximum = Decimal("999999999999999999.999999")
        if basic is not None and basic > maximum:
            raise APIError("quantity_range", "基本数量超出 ERP 支持范围", 422)
        amount = money(quantity * price) if quantity is not None and price is not None else None
        if amount:
            total += Decimal(amount)
            if total > Decimal("999999999999999999.99"):
                raise APIError("amount_range", "总额超出 ERP 支持范围", 422)
        rows.append(
            {
                **{
                    key: material[key]
                    for key in [
                        "material_id",
                        "extend_id",
                        "barcode",
                        "name",
                        "standard",
                        "model",
                        "configured_units",
                    ]
                },
                "unit": unit,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "basic_quantity": format(basic, "f") if basic is not None else None,
                "amount": amount,
            }
        )
    return {
        "currency": "CNY",
        "document_type": "采购订单",
        "erp_status": "0",
        "status_label": "未审核",
        "timezone": settings.erp_timezone,
        "supplier": supplier,
        "order_date": data.order_date,
        "remark": data.remark,
        "lines": rows,
        "total": money(total),
        "missing_fields": missing,
        "unsupported_conditions": data.unsupported_conditions,
        "sources": sources,
        "precision": {"quantity": 6, "unit_price": 6, "amount": 2, "rounding": "ROUND_HALF_UP"},
    }


def document(draft, permissions):
    allowed = readable(draft.provenance, permissions)
    return {
        "id": str(draft.id),
        "task_id": str(draft.task_id),
        "version": draft.version,
        "status": draft.status,
        "content_hash": draft.content_hash if allowed else None,
        "snapshot": draft.snapshot if allowed else {},
        "input": draft.input_data if allowed else {},
        "redacted": not allowed,
        "can_edit": allowed and draft.status in {"editing", "ready"},
        "can_prepare_confirmation": allowed and draft.status == "ready",
    }


def invalidate(repo, draft):
    for challenge in repo.session.exec(
        repo.query(Confirmation).where(
            Confirmation.draft_id == draft.id, Confirmation.invalidated_at.is_(None)
        )
    ).all():
        challenge.invalidated_at = now()
        repo.session.add(challenge)


def save(
    repo,
    conversation_id,
    command_id,
    data,
    snapshot,
    permissions,
    *,
    draft_id=None,
    expected_version=None,
    task_id=None,
):
    repo.get(Conversation, conversation_id, lock=True)
    command_hash = content_hash(data.model_dump(mode="json"))
    if draft_id:
        draft = repo.get(PurchaseDraft, draft_id, lock=True)
        task = repo.get(Task, draft.task_id)
        if task.conversation_id != conversation_id:
            raise APIError("command_conflict", "草稿不属于此对话", 409)
        revision = repo.session.exec(
            repo.query(DraftRevision).where(
                DraftRevision.draft_id == draft.id, DraftRevision.command_id == command_id
            )
        ).first()
        if revision:
            if revision.command_hash != command_hash:
                raise APIError("command_conflict", "相同命令的内容已变化", 409)
            return draft
        if draft.version != expected_version:
            raise APIError(
                "version_conflict",
                "草稿已更新，请载入最新版本后重新检查",
                409,
                next_action="reload_draft",
            )
        if draft.status not in {"editing", "ready"}:
            raise APIError("draft_locked", "此草稿已取消或进入执行，不能编辑", 409)
        invalidate(repo, draft)
        draft.version += 1
        task = repo.get(Task, draft.task_id, lock=True)
    else:
        previous = repo.session.exec(
            repo.query(PurchaseDraft).where(PurchaseDraft.creation_command == command_id)
        ).first()
        if previous:
            task = repo.get(Task, previous.task_id)
            if task.conversation_id != conversation_id or (task_id and task_id != task.id):
                raise APIError("command_conflict", "命令所属任务或对话已变化", 409)
            revision = repo.session.exec(
                repo.query(DraftRevision).where(
                    DraftRevision.draft_id == previous.id, DraftRevision.version == 1
                )
            ).one()
            if revision.command_hash != command_hash:
                raise APIError("command_conflict", "相同命令的内容已变化", 409)
            return previous
        if task_id:
            task = repo.get(Task, task_id, lock=True)
            if task.conversation_id != conversation_id or task.status in {"completed", "cancelled"}:
                raise APIError("invalid_task", "请选择可编辑任务", 409)
            if repo.session.exec(
                repo.query(PurchaseDraft).where(PurchaseDraft.task_id == task.id)
            ).first():
                raise APIError("draft_exists", "当前任务已有草稿，请修改原草稿", 409)
        else:
            task = repo.task(
                conversation_id,
                "purchase",
                {},
                provenance=provenance(
                    permissions, capabilities=("purchase_create", "purchase_prices")
                ),
            )
        draft = PurchaseDraft(
            **repo.principal.fields(), task_id=task.id, creation_command=command_id, content_hash=""
        )
    draft.snapshot = snapshot
    draft.input_data = data.model_dump(mode="json")
    draft.content_hash = content_hash(snapshot)
    draft.status = (
        "ready"
        if not snapshot["missing_fields"] and not snapshot["unsupported_conditions"]
        else "editing"
    )
    draft.provenance = provenance(permissions, capabilities=("purchase_create", "purchase_prices"))
    task.kind = "purchase"
    task.status = "waiting_confirmation" if draft.status == "ready" else "needs_input"
    task.resource_version += 1
    repo.session.add(task)
    repo.session.add(draft)
    repo.session.flush()
    repo.session.add(
        DraftRevision(
            **repo.principal.fields(),
            draft_id=draft.id,
            version=draft.version,
            command_id=command_id,
            command_hash=command_hash,
            snapshot=snapshot,
            input_data=draft.input_data,
            content_hash=draft.content_hash,
        )
    )
    append_event(
        repo,
        conversation_id,
        "draft.updated",
        {"draft_id": str(draft.id)},
        task_id=task.id,
        resource_version=draft.version,
        provenance=draft.provenance,
    )
    return draft


def prepare_confirmation(repo, draft_id, expected_version, permissions):
    permissions.require("purchase_create")
    draft = repo.get(PurchaseDraft, draft_id, lock=True)
    if not readable(draft.provenance, permissions):
        raise APIError("permissions_changed", "权限已变化，请重新核实草稿", 409)
    if draft.version != expected_version:
        raise APIError("version_conflict", "草稿已更新，请重新检查", 409)
    if (
        draft.status != "ready"
        or draft.snapshot["missing_fields"]
        or draft.snapshot["unsupported_conditions"]
    ):
        raise APIError("draft_incomplete", "草稿存在缺项或不支持的业务条件", 422)
    invalidate(repo, draft)
    challenge = secrets.token_urlsafe(32)
    row = Confirmation(
        **repo.principal.fields(),
        draft_id=draft.id,
        version=draft.version,
        content_hash=draft.content_hash,
        challenge_hash=digest(challenge),
        expires_at=now() + timedelta(minutes=5),
    )
    repo.session.add(row)
    repo.session.flush()
    return {
        **document(draft, permissions),
        "challenge": challenge,
        "expires_at": row.expires_at.isoformat(),
        "confirmation_label": "确认创建未审核采购订单",
    }


def check_confirmation(repo, draft, challenge, version):
    row = repo.session.exec(
        repo.query(Confirmation)
        .where(Confirmation.draft_id == draft.id, Confirmation.challenge_hash == digest(challenge))
        .with_for_update()
    ).first()
    if (
        not row
        or row.invalidated_at
        or row.consumed_at
        or row.expires_at <= now()
        or row.version != version
        or draft.version != version
        or row.content_hash != draft.content_hash
        or draft.status != "ready"
    ):
        raise APIError("confirmation_invalid", "确认已失效，请重新检查完整草稿", 409)
    return row
