import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.graph_store import GraphStore  # noqa: E402


@pytest.fixture(scope="module")
def store():
    return GraphStore()
