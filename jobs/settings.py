from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tracking.config import SUPPORTED_PRECISIONS

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_WEIGHTS = Path("models/yolox_x.pth")
DEFAULT_MAX_UPLOAD_BYTES = 1 * 1024**3
DEFAULT_MAX_UNPACKED_BYTES = 4 * 1024**3
DEFAULT_LOCK_TIMEOUT_SECONDS = 1800
DEFAULT_TASK_TIME_LIMIT_SECONDS = 1500
DEFAULT_API_METRICS_PORT = 9101
DEFAULT_WORKER_METRICS_PORT = 9102
MAX_PORT = 65535
_REDIS_SCHEMES = ("redis://", "rediss://", "unix://")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    storage_dir: Path
    redis_url: str
    weights: Path
    precision: str
    max_upload_bytes: int
    max_unpacked_bytes: int
    lock_timeout_seconds: int
    task_time_limit_seconds: int
    api_metrics_port: int = DEFAULT_API_METRICS_PORT
    worker_metrics_port: int = DEFAULT_WORKER_METRICS_PORT


def _text(environ: Mapping[str, str], name: str, default: str | None = None) -> str:
    value = environ.get(name, default)
    if value is None:
        raise SettingsError(f"{name} is required")
    if not value.strip():
        raise SettingsError(f"{name} must not be empty")
    return value


def _positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise SettingsError(f"{name} must be an integer, got {raw!r}") from error
    if value <= 0:
        raise SettingsError(f"{name} must be positive, got {value}")
    return value


def _port(environ: Mapping[str, str], name: str, default: int) -> int:
    value = _positive_int(environ, name, default)
    if value > MAX_PORT:
        raise SettingsError(f"{name} must be at most {MAX_PORT}, got {value}")
    return value


def _storage_dir(environ: Mapping[str, str]) -> Path:
    path = Path(_text(environ, "OPENMOT_STORAGE_DIR"))
    if not path.is_absolute():
        raise SettingsError(f"OPENMOT_STORAGE_DIR must be an absolute path, got {str(path)!r}")
    return path


def _redis_url(environ: Mapping[str, str]) -> str:
    url = _text(environ, "OPENMOT_REDIS_URL", DEFAULT_REDIS_URL)
    if not url.startswith(_REDIS_SCHEMES):
        raise SettingsError(f"OPENMOT_REDIS_URL must start with one of {_REDIS_SCHEMES}, got {url!r}")
    return url


def _precision(environ: Mapping[str, str]) -> str:
    precision = _text(environ, "OPENMOT_PRECISION", "fp16")
    if precision not in SUPPORTED_PRECISIONS:
        raise SettingsError(f"OPENMOT_PRECISION must be one of {SUPPORTED_PRECISIONS}, got {precision!r}")
    return precision


def load_settings(environ: Mapping[str, str]) -> Settings:
    max_upload_bytes = _positive_int(environ, "OPENMOT_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)
    max_unpacked_bytes = _positive_int(environ, "OPENMOT_MAX_UNPACKED_BYTES", DEFAULT_MAX_UNPACKED_BYTES)
    if max_unpacked_bytes < max_upload_bytes:
        raise SettingsError(
            f"OPENMOT_MAX_UNPACKED_BYTES ({max_unpacked_bytes}) must not be below "
            f"OPENMOT_MAX_UPLOAD_BYTES ({max_upload_bytes})"
        )
    lock_timeout_seconds = _positive_int(environ, "OPENMOT_LOCK_TIMEOUT_SECONDS", DEFAULT_LOCK_TIMEOUT_SECONDS)
    task_time_limit_seconds = _positive_int(
        environ, "OPENMOT_TASK_TIME_LIMIT_SECONDS", DEFAULT_TASK_TIME_LIMIT_SECONDS
    )
    if task_time_limit_seconds >= lock_timeout_seconds:
        raise SettingsError(
            f"OPENMOT_TASK_TIME_LIMIT_SECONDS ({task_time_limit_seconds}) must be below "
            f"OPENMOT_LOCK_TIMEOUT_SECONDS ({lock_timeout_seconds})"
        )
    return Settings(
        storage_dir=_storage_dir(environ),
        redis_url=_redis_url(environ),
        weights=Path(_text(environ, "OPENMOT_WEIGHTS", str(DEFAULT_WEIGHTS))),
        precision=_precision(environ),
        max_upload_bytes=max_upload_bytes,
        max_unpacked_bytes=max_unpacked_bytes,
        lock_timeout_seconds=lock_timeout_seconds,
        task_time_limit_seconds=task_time_limit_seconds,
        api_metrics_port=_port(environ, "OPENMOT_API_METRICS_PORT", DEFAULT_API_METRICS_PORT),
        worker_metrics_port=_port(environ, "OPENMOT_WORKER_METRICS_PORT", DEFAULT_WORKER_METRICS_PORT),
    )
