"""과금이 생기는 개발자용 API가 공개 배포에서 등록되지 않는지 검증한다."""

import os
import sys
from pathlib import Path

os.environ["ENABLE_DEBUG_ENDPOINTS"] = "0"
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import main  # noqa: E402


def test_paid_debug_routes_are_disabled_by_default():
    paths = {route.path for route in main.app.routes}

    assert "/query" in paths
    assert "/search/keyword" in paths
    assert not {"/search/vector", "/search/hybrid", "/answer"} & paths
