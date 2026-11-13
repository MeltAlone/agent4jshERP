import httpx

from app.config import Settings


async def check_erp(settings: Settings) -> None:
    try:
        async with httpx.AsyncClient(
            timeout=settings.dependency_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(settings.erp_base_url + "/user/getUserSession")
        if response.status_code != 500 or response.text.strip() != "loginOut":
            raise ValueError
    except Exception:
        raise RuntimeError("ERP 地址不可用或契约不匹配，请检查 erp_base_url") from None
