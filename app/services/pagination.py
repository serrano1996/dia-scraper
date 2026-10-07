"""Maps the API's pages onto Dia's, which never hold fewer than 30 products (plan-D17)."""

import math
from dataclasses import dataclass

# With a smaller `page_size`, Dia still answers 30 products (Fase 0 §1).
DIA_MIN_PAGE_SIZE = 30


@dataclass(frozen=True)
class DiaWindow:
    """Which Dia page to ask for, and where the requested page starts inside it."""

    page: int
    page_size: int
    offset: int


def dia_window(page: int, page_size: int) -> DiaWindow:
    """The single Dia page that holds the requested one (spec 001 RF-3, spec-D9).

    Dia's page size is the smallest multiple of `page_size` that Dia honours, so
    the requested page never straddles two Dia pages: still one request per page.
    """
    dia_page_size = page_size * math.ceil(DIA_MIN_PAGE_SIZE / page_size)
    start = (page - 1) * page_size
    return DiaWindow(
        page=start // dia_page_size + 1,
        page_size=dia_page_size,
        offset=start % dia_page_size,
    )
