import re
from pathlib import Path

from app.api import create_app
from app.upload import UploadLimits
from jobs.layout import StorageLayout
from jobs.memory_store import InMemoryJobStore

README = Path(__file__).resolve().parents[1] / "README.md"
FRAMEWORK_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
ROW = re.compile(r"^\| `(GET|POST)` \| `([^`]+)` \|(.*)\|$")


def _readme_endpoints() -> dict[tuple[str, str], str]:
    section = README.read_text().split("## API")[1].split("\n## ")[0]
    rows = (ROW.match(line) for line in section.splitlines())
    return {(row[1], row[2]): row[3] for row in rows if row}


def _served_endpoints() -> set[tuple[str, str]]:
    app = create_app(
        InMemoryJobStore(), StorageLayout(Path("/unused")), UploadLimits(1, 1), lambda job_id: None, lambda: None
    )
    return {(method, route.path) for route in app.routes if hasattr(route, "methods") and route.path not in FRAMEWORK_PATHS for method in route.methods - {"HEAD"}}  # type: ignore[attr-defined]


def test_every_served_endpoint_is_in_the_readme_table() -> None:
    assert _served_endpoints() <= set(_readme_endpoints())


def test_the_readme_lists_only_served_endpoints_or_marks_them_planned() -> None:
    served = _served_endpoints()
    for endpoint, description in _readme_endpoints().items():
        assert endpoint in served or "planned" in description, endpoint
