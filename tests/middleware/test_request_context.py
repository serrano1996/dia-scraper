import logging
import re

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from app.core.logging import install_request_id_factory
from app.middleware.request_context import REQUEST_ID_HEADER, RequestContextMiddleware

LOGGER = "app.middleware.request_context"


def make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ok")
    async def ok(term: str = Query()) -> dict[str, str]:
        logging.getLogger("somewhere.else").info("inside the request")
        return {"term": term}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("secret internal detail")

    app.add_middleware(RequestContextMiddleware)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(make_app(), raise_server_exceptions=False)


def messages(caplog: pytest.LogCaptureFixture, logger: str = LOGGER) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == logger]


def test_start_and_end_lines_share_the_request_id_of_the_header(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = client.get("/ok", params={"term": "leche"})

    request_id = response.headers[REQUEST_ID_HEADER]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    start, end = messages(caplog)
    assert start.getMessage().startswith("request started method=GET path='/ok'")
    assert "status=200" in end.getMessage()
    assert "duration_ms=" in end.getMessage()
    assert start.request_id == end.request_id == request_id


def test_lines_of_any_module_during_the_request_carry_its_id(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = client.get("/ok", params={"term": "leche"})

    [inner] = messages(caplog, "somewhere.else")
    assert inner.request_id == response.headers[REQUEST_ID_HEADER]


@pytest.mark.parametrize(("path", "status"), [("/ok?term=x", 200), ("/ok", 422), ("/boom", 500)])
def test_every_response_carries_the_header(client: TestClient, path: str, status: int) -> None:
    response = client.get(path)

    assert response.status_code == status
    assert REQUEST_ID_HEADER in response.headers


def test_a_client_supplied_request_id_is_ignored(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = client.get("/ok", params={"term": "x"}, headers={REQUEST_ID_HEADER: "forged"})

    assert response.headers[REQUEST_ID_HEADER] != "forged"
    assert all(r.request_id != "forged" for r in caplog.records)


def test_an_unhandled_error_is_a_logged_500_without_its_message(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret internal detail" not in response.text
    [error] = [r for r in messages(caplog) if r.levelno == logging.ERROR]
    assert "type=RuntimeError" in error.getMessage()
    assert "frames=" in error.getMessage()  # the traceback, without the message (T13)
    assert "status=500" in messages(caplog)[-1].getMessage()


def test_client_values_are_escaped_so_they_cannot_forge_lines(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        client.get("/ok", params={"term": "leche\nERROR fake line"})

    start = messages(caplog)[0].getMessage()
    assert "\n" not in start
    assert repr("leche\nERROR fake line") in start  # the newline appears as \n, escaped


def test_outside_a_request_the_id_is_a_dash(caplog: pytest.LogCaptureFixture) -> None:
    install_request_id_factory()  # what the middleware (or configure_logging) does

    with caplog.at_level(logging.INFO):
        logging.getLogger("x").info("startup")

    assert caplog.records[-1].request_id == "-"


# --- Fixes from the fresh review (T13) ---


def test_an_unhandled_error_logs_its_type_and_frames_but_not_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()

    @app.get("/leak")
    async def leak() -> dict[str, str]:
        # Built at run time, as a Dia body fragment would be: the frames show the
        # source line, never the values (the source does not hold the secret).
        secret = "-".join(["session_id=abc", "from", "a", "Dia", "body"])
        raise ValueError(secret)

    app.add_middleware(RequestContextMiddleware)

    with caplog.at_level(logging.INFO):
        TestClient(app, raise_server_exceptions=False).get("/leak")

    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    text = caplog.text
    assert "type=ValueError" in error.getMessage()
    assert "def leak" in text or "in leak" in text  # the frames are there
    assert "session_id=abc-from" not in text


def test_long_query_strings_are_truncated(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        client.get("/ok", params={"term": "x" * 5000})

    start = messages(caplog)[0].getMessage()
    assert len(start) < 700
    assert "truncated" in start


def test_repeated_params_are_all_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        client.get("/ok?term=first&term=second")

    start = messages(caplog)[0].getMessage()
    assert "first" in start
    assert "second" in start
