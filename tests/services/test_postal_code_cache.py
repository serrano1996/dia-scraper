import pytest
from fakeredis import FakeAsyncRedis

from app.services.postal_code_cache import NotServedRepository, not_served_key


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


@pytest.fixture
def repository(redis: FakeAsyncRedis) -> NotServedRepository:
    return NotServedRepository(redis)


def test_key_names_the_postal_code() -> None:
    assert not_served_key("35001") == "postal_code:not_served:35001"


async def test_an_unknown_postal_code_is_not_marked(repository: NotServedRepository) -> None:
    assert await repository.is_marked("35001") is False


async def test_mark_then_is_marked(repository: NotServedRepository, redis: FakeAsyncRedis) -> None:
    await repository.mark("35001", ttl_seconds=600)

    assert await repository.is_marked("35001") is True
    assert 0 < await redis.ttl("postal_code:not_served:35001") <= 600


async def test_marks_are_per_postal_code(repository: NotServedRepository) -> None:
    await repository.mark("35001", ttl_seconds=600)

    assert await repository.is_marked("08001") is False
