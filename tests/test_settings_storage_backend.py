import pytest

from jobs.settings import SettingsError, load_settings

MINIMAL = {"OPENMOT_STORAGE_DIR": "/srv/openmot"}
S3 = {**MINIMAL, "OPENMOT_STORAGE_BACKEND": "s3", "OPENMOT_S3_BUCKET": "tracking"}
SECRET = "do-not-print-this-secret"


def test_the_default_backend_is_the_filesystem_and_needs_no_s3_setting() -> None:
    settings = load_settings(MINIMAL)
    assert settings.storage_backend == "filesystem"
    assert (settings.s3_bucket, settings.s3_endpoint_url) == (None, None)


def test_s3_settings_are_ignored_by_the_filesystem_backend() -> None:
    settings = load_settings({**MINIMAL, "OPENMOT_S3_BUCKET": "tracking"})
    assert settings.storage_backend == "filesystem"


def test_s3_needs_only_a_bucket_and_defaults_the_rest() -> None:
    settings = load_settings(S3)
    assert settings.storage_backend == "s3"
    assert settings.s3_bucket == "tracking"
    assert settings.s3_endpoint_url is None
    assert settings.s3_region == "us-east-1"


def test_the_endpoint_and_the_region_can_be_set() -> None:
    settings = load_settings({**S3, "OPENMOT_S3_ENDPOINT_URL": "http://minio:9000", "OPENMOT_S3_REGION": "eu-west-1"})
    assert (settings.s3_endpoint_url, settings.s3_region) == ("http://minio:9000", "eu-west-1")


def test_s3_without_a_bucket_is_rejected_by_name() -> None:
    with pytest.raises(SettingsError, match="OPENMOT_S3_BUCKET"):
        load_settings({**MINIMAL, "OPENMOT_STORAGE_BACKEND": "s3"})


def test_a_blank_bucket_is_rejected() -> None:
    with pytest.raises(SettingsError, match="OPENMOT_S3_BUCKET"):
        load_settings({**S3, "OPENMOT_S3_BUCKET": "   "})


@pytest.mark.parametrize("endpoint", ["minio:9000", "ftp://minio", "//minio"])
def test_an_endpoint_without_an_http_scheme_is_rejected(endpoint: str) -> None:
    with pytest.raises(SettingsError, match="OPENMOT_S3_ENDPOINT_URL"):
        load_settings({**S3, "OPENMOT_S3_ENDPOINT_URL": endpoint})


def test_an_unsupported_backend_names_the_setting_and_the_choices() -> None:
    with pytest.raises(SettingsError) as error:
        load_settings({**MINIMAL, "OPENMOT_STORAGE_BACKEND": "gcs"})
    assert "OPENMOT_STORAGE_BACKEND" in str(error.value)
    assert "filesystem" in str(error.value) and "s3" in str(error.value)


def test_credentials_never_appear_in_the_settings_or_in_errors() -> None:
    environment = {**S3, "AWS_ACCESS_KEY_ID": "AKIDEXAMPLE", "AWS_SECRET_ACCESS_KEY": SECRET}
    assert SECRET not in repr(load_settings(environment))
    with pytest.raises(SettingsError) as error:
        load_settings({**environment, "OPENMOT_S3_ENDPOINT_URL": "minio:9000"})
    assert SECRET not in str(error.value) and "AKIDEXAMPLE" not in str(error.value)


def test_the_storage_directory_is_still_required_for_the_s3_backend() -> None:
    with pytest.raises(SettingsError, match="OPENMOT_STORAGE_DIR"):
        load_settings({"OPENMOT_STORAGE_BACKEND": "s3", "OPENMOT_S3_BUCKET": "tracking"})
