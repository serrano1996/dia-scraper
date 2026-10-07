"""Test double for `OutboundGate`, shared by the scraper tests."""


class FakeGate:
    """Counts admissions and blocks; can refuse admission."""

    def __init__(self, refuse: Exception | None = None) -> None:
        self.admitted = 0
        self.blocks = 0
        self.refuse = refuse

    async def admit(self) -> None:
        if self.refuse is not None:
            raise self.refuse
        self.admitted += 1

    async def blocked(self) -> None:
        self.blocks += 1
