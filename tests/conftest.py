import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("TUF_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set TUF_LIVE_TESTS=1 and real credentials")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
