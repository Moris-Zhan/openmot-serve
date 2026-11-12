import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
CI_GUIDE = (ROOT / "docs" / "ci.md").read_text(encoding="utf-8")
SHA256_DIGEST = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def _test_steps() -> list[dict]:
    return WORKFLOW["jobs"]["test"]["steps"]


def _step_index(name_fragment: str) -> int:
    matches = [index for index, step in enumerate(_test_steps()) if name_fragment in step.get("name", "")]
    assert len(matches) == 1, f"expected exactly one step named like {name_fragment!r}"
    return matches[0]


def _install_command() -> str:
    return _test_steps()[_step_index("Install promtool")]["run"]


def _image_version() -> str:
    dockerfile = (ROOT / "monitoring" / "prometheus.Dockerfile").read_text()
    return re.search(r"^FROM prom/prometheus:v([\d.]+)$", dockerfile, re.M).group(1)  # type: ignore[union-attr]


def test_promtool_is_installed_in_its_own_step_before_the_tests_run() -> None:
    assert _step_index("Install promtool") < _step_index("Run the default tests")
    assert "promtool" not in _test_steps()[_step_index("Install kubectl")]["run"]


def test_the_promtool_download_is_checked_against_exactly_one_sha256_digest() -> None:
    command = _install_command()
    assert "sha256sum --check" in command
    assert len(SHA256_DIGEST.findall(command)) == 1


def test_the_checksum_is_verified_before_anything_is_extracted_or_installed() -> None:
    command = _install_command()
    assert command.index("sha256sum --check") < command.index("tar ") < command.index("install ")


def test_promtool_comes_from_the_same_prometheus_version_as_the_image() -> None:
    version = _image_version()
    command = _install_command()
    assert f"/v{version}/prometheus-{version}.linux-amd64.tar.gz" in command


def test_only_the_promtool_binary_is_taken_from_the_release() -> None:
    command = _install_command()
    assert command.count("promtool") >= 2
    assert "prometheus-" in command and "/promtool" in command


def test_a_missing_promtool_fails_before_the_tests_run() -> None:
    check = _test_steps()[_step_index("Check promtool")]
    assert "promtool --version" in check["run"]
    assert _step_index("Check promtool") < _step_index("Run the default tests")


def test_the_ci_guide_names_promtool_and_its_version() -> None:
    assert "promtool" in CI_GUIDE
    assert _image_version() in CI_GUIDE
