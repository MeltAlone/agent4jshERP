"""Durable conversation and purchase worker entry point."""

import argparse
import logging
import time

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.config import load_settings
from app.database import check_database, create_database_engine
from app.erp.client import ERPError
from app.orchestration.handler import handle
from app.queue import LeaseLost, claim, complete

logger = logging.getLogger("assistant.worker")


def run_once(engine, settings=None) -> bool:
    lease = claim(engine)
    if lease is None:
        if settings is not None:
            from app.purchases.worker import run_once as execute_once

            return execute_once(engine, settings)
        return False
    error = None if lease.kind == "test_probe" else "handler_unavailable"
    try:
        if lease.exhausted:
            error = "retry_exhausted"
        elif lease.kind == "conversation" and settings is not None:
            try:
                error = handle(engine, settings, lease)
            except ERPError as failure:
                error = failure.code
            except Exception:
                # 保留租约和检查点，有限重领恢复；禁止记录异常对象/请求正文。
                logger.error("conversation_interrupted job_id=%s", lease.job_id)
                return True
        complete(engine, lease, error_code=error)
    except LeaseLost:
        logger.info("lease_lost job_id=%s", lease.job_id)
    return True


def run_loop(engine, settings, *, once=False, wait=time.sleep):
    retry_delay, database_unavailable = 1, False
    while True:
        try:
            handled = run_once(engine, settings)
        except OperationalError, InterfaceError, PoolTimeout:
            # 不打印驱动异常，其中可能包含连接信息或业务参数。
            if not database_unavailable:
                logger.warning("database_unavailable; waiting to resume durable jobs")
            database_unavailable = True
            if once:
                raise RuntimeError("worker 数据库暂不可用，请恢复后重试") from None
            wait(retry_delay)
            retry_delay = min(30, retry_delay * 2)
            continue
        if database_unavailable:
            logger.info("database_recovered; durable jobs resumed")
        retry_delay, database_unavailable = 1, False
        if once:
            return
        if not handled:
            wait(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    engine = create_database_engine(settings)
    try:
        check_database(engine)
        run_loop(engine, settings, once=args.once)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
