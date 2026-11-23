import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
HAS_SECTION = "\n## Quick start" in README
SECTION = README.split("\n## Quick start", 1)[1].split("\n## ", 1)[0] if HAS_SECTION else ""
COMPOSE = "docker compose -f deploy/docker/docker-compose.yml"


def test_the_quick_start_comes_before_the_api_section() -> None:
    assert HAS_SECTION
    assert README.index("\n## Quick start") < README.index("\n## API")


def test_it_shows_how_to_start_and_check_the_services() -> None:
    assert f"{COMPOSE} up -d --build" in SECTION
    assert f"{COMPOSE} ps" in SECTION
    assert "/health" in SECTION


def test_it_says_the_api_is_loopback_only_and_shows_the_ssh_tunnel() -> None:
    assert "127.0.0.1" in SECTION
    assert "ssh -N -L 8000:127.0.0.1:8000" in SECTION


def test_it_links_to_the_service_doc_for_the_details() -> None:
    assert "docs/service.md" in SECTION


def test_every_file_it_names_exists() -> None:
    names = re.findall(r"(?:deploy|docs|models)/[\w./-]+\.(?:yml|md|pth)", SECTION)
    assert names
    for name in names:
        if name.startswith("models/"):
            continue
        assert (ROOT / name).is_file(), name


def test_it_stays_short_instead_of_copying_the_service_doc() -> None:
    assert 0 < len(SECTION.strip().splitlines()) <= 30
