from fastapi import Request
from sqlmodel import Session

from app.auth import authorize
from app.repository import Principal


def database_session(request: Request):
    with Session(request.app.state.engine) as session:
        yield session


async def current_principal(request: Request) -> Principal:
    return (await authorize(request)).principal
