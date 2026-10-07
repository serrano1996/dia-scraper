import pytest

from app.models.product import MAX_PAGE, MAX_PAGE_SIZE
from app.services.pagination import DIA_MIN_PAGE_SIZE, DiaWindow, dia_window


@pytest.mark.parametrize(
    ("page", "page_size", "expected"),
    [
        (1, 5, DiaWindow(page=1, page_size=30, offset=0)),
        (2, 5, DiaWindow(page=1, page_size=30, offset=5)),
        (6, 5, DiaWindow(page=1, page_size=30, offset=25)),
        (7, 5, DiaWindow(page=2, page_size=30, offset=0)),
        (3, 7, DiaWindow(page=1, page_size=35, offset=14)),
        (2, 29, DiaWindow(page=1, page_size=58, offset=29)),
        (3, 29, DiaWindow(page=2, page_size=58, offset=0)),
        (1, 30, DiaWindow(page=1, page_size=30, offset=0)),
        (3, 50, DiaWindow(page=3, page_size=50, offset=0)),
        (20, 100, DiaWindow(page=20, page_size=100, offset=0)),
    ],
)
def test_dia_window(page: int, page_size: int, expected: DiaWindow) -> None:
    assert dia_window(page, page_size) == expected


@pytest.mark.parametrize("page_size", range(1, MAX_PAGE_SIZE + 1))
def test_the_requested_page_always_fits_in_one_dia_page(page_size: int) -> None:
    for page in range(1, MAX_PAGE + 1):
        window = dia_window(page, page_size)

        assert window.page_size >= DIA_MIN_PAGE_SIZE
        assert window.page_size % page_size == 0
        assert window.offset + page_size <= window.page_size
        assert (window.page - 1) * window.page_size + window.offset == (page - 1) * page_size
        assert window.page <= MAX_PAGE  # far from page 51, where Dia answers 404
