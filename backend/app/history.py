from app.erp.permissions import Permissions
from app.models import Message
from app.repository import Repository


def provenance(
    permissions: Permissions, *, capabilities: tuple[str, ...], depot_ids: tuple[int, ...] = ()
) -> dict:
    return {
        "source": "erp",
        "fingerprint": permissions.fingerprint,
        "capabilities": list(capabilities),
        "depot_ids": list(depot_ids),
    }


def readable(tags: dict, permissions: Permissions | None) -> bool:
    if tags == {"source": "user"} or tags == {"source": "state"}:
        return True
    if not isinstance(tags, dict) or tags.get("source") != "erp" or permissions is None:
        return False
    if not permissions.supported or tags.get("fingerprint") != permissions.fingerprint:
        return False
    capabilities, depots = tags.get("capabilities"), tags.get("depot_ids")
    if not isinstance(capabilities, list) or not isinstance(depots, list):
        return False
    for capability in capabilities:
        if capability not in {
            "material_read",
            "supplier_read",
            "stock_read",
            "purchase_read",
            "purchase_create",
            "purchase_prices",
            "stock_prices",
        } or not getattr(permissions, capability):
            return False
    return all(type(depot) is int and depot in permissions.depot_ids for depot in depots)


def message_document(message: Message, permissions: Permissions | None) -> dict:
    permitted = message.role == "user" or readable(message.provenance, permissions)
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content if permitted else "此消息的数据权限已变化，内容已隐藏",
        "cards": message.cards if permitted else [],
        "task_id": str(message.task_id) if message.task_id else None,
        "created_at": message.created_at.isoformat(),
        "redacted": not permitted,
    }


def model_history(
    repository: Repository, conversation_id, permissions: Permissions, *, task_id=...
) -> list[dict]:
    # A single permission filter is shared by UI snapshots and future model context reconstruction.
    messages = repository.session.exec(
        repository.query(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    ).all()
    return [
        {"role": item["role"], "content": item["content"]}
        for message in messages
        if message.provenance.get("source") != "expired"
        and (task_id is ... or message.task_id == task_id)
        and not (item := message_document(message, permissions))["redacted"]
    ]
