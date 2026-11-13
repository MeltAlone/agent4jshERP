import logging

import pytest
from sqlalchemy.exc import OperationalError

from app import worker


class StopLoop(Exception):
    pass


def test_database_outage_backs_off_recovers_and_does_not_log_driver_secrets(monkeypatch, caplog):
    attempts, delays = [], []
    outcomes = [OperationalError("synthetic-secret", {}, Exception("password=secret"))] * 7
    outcomes += [False, OperationalError("synthetic-secret", {}, Exception("secret")), StopLoop()]

    def run_once(engine, settings):
        attempts.append((engine, settings))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(worker, "run_once", run_once)
    with caplog.at_level(logging.INFO, logger="assistant.worker"), pytest.raises(StopLoop):
        worker.run_loop("engine", "settings", wait=delays.append)
    assert delays == [1, 2, 4, 8, 16, 30, 30, 1, 1]
    assert len(attempts) == 10
    assert caplog.text.count("database_unavailable") == 2
    assert caplog.text.count("database_recovered") == 1
    assert "synthetic-secret" not in caplog.text and "password=" not in caplog.text


def test_once_mode_returns_after_one_job_and_reports_database_outage_safely(monkeypatch):
    def unavailable(*args):
        raise OperationalError("synthetic-secret", {}, Exception("secret"))

    monkeypatch.setattr(worker, "run_once", unavailable)
    with pytest.raises(RuntimeError, match="worker 数据库暂不可用") as failure:
        worker.run_loop(None, None, once=True, wait=lambda _: pytest.fail("must not wait"))
    assert "synthetic-secret" not in str(failure.value)
    monkeypatch.setattr(worker, "run_once", lambda *_: True)
    worker.run_loop(None, None, once=True, wait=lambda _: pytest.fail("must not wait"))


def test_programming_errors_are_not_hidden_by_database_recovery(monkeypatch):
    def broken(*args):
        raise ValueError("invalid workflow state")

    monkeypatch.setattr(worker, "run_once", broken)
    with pytest.raises(ValueError, match="invalid workflow state"):
        worker.run_loop(None, None, wait=lambda _: pytest.fail("must not wait"))
