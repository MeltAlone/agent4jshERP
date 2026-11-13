import asyncio
from decimal import Decimal

import pytest
from helpers import configuration
from pydantic import ValidationError
from test_reads import service

from app.errors import APIError
from app.purchases.contracts import DraftInput, LineInput, money
from app.purchases.drafts import prepare


def data(**patch):
    return DraftInput.model_validate(
        {
            "lines": [
                {"extend_id": 20011, "quantity": "2.5", "unit": "箱", "unit_price": "30.125"}
            ],
            "supplier": {"id": 4001, "name": "虚构饮品供应商"},
            "order_date": "2026-11-12",
            **patch,
        }
    )


def test_decimal_amount_unit_conversion_and_missing_or_unsupported_conditions():
    snapshot = asyncio.run(prepare(data(), service(), configuration()))
    assert snapshot["total"] == "75.31" and Decimal(snapshot["lines"][0]["basic_quantity"]) == 30
    assert snapshot["missing_fields"] == [] and snapshot["status_label"] == "未审核"
    assert money("1.005") == "1.01" and money("1.004") == "1.00"
    draft = data(
        lines=[{"extend_id": 20011}],
        supplier=None,
        order_date=None,
        unsupported_conditions=["支付订金", "立即入库"],
    )
    snapshot = asyncio.run(prepare(draft, service(), configuration()))
    assert "供应商" in snapshot["missing_fields"] and "明细1单价" in snapshot["missing_fields"]
    assert snapshot["unsupported_conditions"] == ["支付订金", "立即入库"]
    assert "仓库" not in str(snapshot["missing_fields"])


def test_invalid_quantities_prices_precision_units_and_masked_prices_are_blocked():
    for number in ["NaN", "1e6", "1.0000001", "-1", 3.4, True]:
        with pytest.raises(ValidationError):
            LineInput(extend_id=20011, quantity=number)
    for patch, code in [
        ({"quantity": "0"}, "invalid_quantity"),
        ({"unit_price": "0"}, "invalid_price"),
        ({"unit": "瓶"}, "unit_mismatch"),
    ]:
        values = {"extend_id": 20011, "quantity": "1", "unit": "箱", "unit_price": "30", **patch}
        with pytest.raises(APIError) as failure:
            asyncio.run(prepare(data(lines=[values]), service(), configuration()))
        assert failure.value.code == code
    from app.erp.client import ERPError

    with pytest.raises(ERPError, match="permission_denied"):
        asyncio.run(prepare(data(), service(name="fixture-masked"), configuration()))


def test_multiple_lines_round_each_amount_and_preserve_all_conditions():
    import copy

    reader = service()
    original = reader.material

    async def resolved(query):
        result = copy.deepcopy(await original(type(query)(extend_id=20011)))
        result["data"]["extend_id"] = query.extend_id
        result["data"]["barcode"] = "synthetic-" + str(query.extend_id)
        return result

    reader.material = resolved
    values = data(
        lines=[
            {"extend_id": identity, "quantity": "0.005", "unit": "箱", "unit_price": "1"}
            for identity in (20011, 20031)
        ],
        unsupported_conditions=["支付订金", "立即入库", "含税"],
    )
    snapshot = asyncio.run(prepare(values, reader, configuration()))
    assert [line["amount"] for line in snapshot["lines"]] == ["0.01", "0.01"]
    assert snapshot["total"] == "0.02" and len(snapshot["unsupported_conditions"]) == 3
    with pytest.raises(APIError) as error:
        asyncio.run(
            prepare(data(supplier={"id": 999, "name": "其他供应商"}), service(), configuration())
        )
    assert error.value.code == "supplier_changed"


def test_exact_verified_unit_label_is_an_alias_and_input_remains_unchanged():
    reader = service()
    values = data(
        lines=[{"extend_id": 20011, "quantity": "5", "unit": "箱[12瓶]"}],
        supplier=None,
        order_date=None,
    )
    original = values.model_dump()
    snapshot = asyncio.run(prepare(values, reader, configuration()))
    line = snapshot["lines"][0]
    assert line["extend_id"] == 20011 and line["unit"] == "箱"
    assert line["quantity"] == "5" and Decimal(line["basic_quantity"]) == 60
    assert snapshot["missing_fields"] == ["供应商", "订单日期", "明细1单价"]
    assert values.model_dump() == original


@pytest.mark.parametrize(
    "label", ["箱[24瓶]", "瓶[基本]", "箱[12瓶] extra", "箱[999999瓶]", "箱[12瓶] "]
)
def test_other_barcode_or_forged_unit_labels_are_not_normalized(label):
    values = data(lines=[{"extend_id": 20011, "quantity": "5", "unit": label}])
    with pytest.raises(APIError) as failure:
        asyncio.run(prepare(values, service(), configuration()))
    assert failure.value.code == "unit_mismatch"


def test_unit_alias_still_requires_the_verified_configured_conversion():
    reader = service()
    original = reader.material

    async def without_unit(query):
        fact = await original(query)
        fact["data"]["configured_units"] = {"瓶": "1"}
        return fact

    reader.material = without_unit
    with pytest.raises(APIError) as failure:
        asyncio.run(
            prepare(data(lines=[{"extend_id": 20011, "unit": "箱[12瓶]"}]), reader, configuration())
        )
    assert failure.value.code == "unit_mismatch"


@pytest.mark.parametrize("unit, accepted", [("瓶[基本]", True), ("箱[12瓶]", False), ("箱", False)])
def test_base_barcode_only_accepts_its_own_verified_unit_alias(unit, accepted):
    from test_reads import FixtureClient

    client = FixtureClient()
    detail = client.rows["material_extend"]["body"]["data"]
    detail["mBarCode"], detail["commodityUnit"] = "FIX900110", "瓶"
    values = data(lines=[{"extend_id": 20010, "quantity": "5", "unit": unit}])
    if accepted:
        snapshot = asyncio.run(prepare(values, service(client), configuration()))
        assert snapshot["lines"][0]["unit"] == "瓶"
        assert Decimal(snapshot["lines"][0]["basic_quantity"]) == 5
    else:
        with pytest.raises(APIError) as failure:
            asyncio.run(prepare(values, service(client), configuration()))
        assert failure.value.code == "unit_mismatch"
