from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "docs" / "service.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SECTION = SERVICE.split("### S3 物件儲存", 1)[-1] if "### S3 物件儲存" in SERVICE else ""


def test_the_service_doc_has_an_s3_section() -> None:
    assert SECTION


def test_the_s3_section_documents_every_setting_and_the_start_command() -> None:
    for name in (
        "OPENMOT_STORAGE_BACKEND",
        "OPENMOT_S3_BUCKET",
        "OPENMOT_S3_ENDPOINT_URL",
        "OPENMOT_S3_REGION",
        "AWS_ACCESS_KEY_ID",
        "S3_ACCESS_KEY",
    ):
        assert name in SECTION, name
    assert "docker-compose.s3.yml" in SECTION and "create_bucket" in SECTION


def test_the_s3_section_lists_the_limits_honestly() -> None:
    for phrase in ("rustfs/rustfs:1.0.6", "沒有", "Kubernetes", "保存期限", "孤兒", "逐張", "映像大小"):
        assert phrase in SECTION, phrase


def test_the_readme_roadmap_ticks_s3_and_points_to_the_doc() -> None:
    assert (
        "- [x] S3-compatible storage" in README
        and "docs/service.md" in README.split("S3-compatible storage", 1)[1][:300]
    )
    assert "S3-compatible storage later" not in README
