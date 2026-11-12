import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tracking.bytetrack import TrackerParams
from tracking.files import write_text_atomic

SUPPORTED_PRECISIONS = ("fp16", "fp32")
_FLOAT_KEYS = ("detection_threshold", "match_threshold")
_INT_KEYS = ("track_buffer",)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RunMetadata:
    tracker: TrackerParams
    weights_id: str
    weights_sha256: str
    precision: str

    def __post_init__(self) -> None:
        if self.precision not in SUPPORTED_PRECISIONS:
            raise ValueError(
                f"unsupported precision {self.precision!r}, expected one of {SUPPORTED_PRECISIONS}"
            )


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"config file not found: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error


def _checked_value(key: str, value: object, path: Path) -> float | int:
    is_number = isinstance(value, int | float) and not isinstance(value, bool)
    if key in _FLOAT_KEYS and is_number:
        return float(value)
    if key in _INT_KEYS and isinstance(value, int) and not isinstance(value, bool):
        return value
    expected = "a number" if key in _FLOAT_KEYS else "an integer"
    raise ConfigError(f"{path}: [tracker] {key} must be {expected}, got {value!r}")


def load_tracker_params(path: Path | None) -> TrackerParams:
    if path is None:
        return TrackerParams()
    document = _read_toml(path)
    unknown_sections = sorted(set(document) - {"tracker"})
    if unknown_sections:
        raise ConfigError(f"{path}: unknown section(s) {unknown_sections}, expected [tracker]")
    section = document.get("tracker", {})
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: [tracker] must be a table")
    unknown_keys = sorted(set(section) - set(_FLOAT_KEYS) - set(_INT_KEYS))
    if unknown_keys:
        raise ConfigError(f"{path}: unknown key(s) {unknown_keys} in [tracker]")
    values = {key: _checked_value(key, value, path) for key, value in section.items()}
    try:
        return TrackerParams(**values)
    except ValueError as error:
        raise ConfigError(f"{path}: {error}") from error


def metadata_path_for(result_path: Path) -> Path:
    return result_path.with_suffix(".meta.json")


def _metadata_document(metadata: RunMetadata) -> dict[str, object]:
    return {
        "tracker": {
            "detection_threshold": metadata.tracker.detection_threshold,
            "match_threshold": metadata.tracker.match_threshold,
            "track_buffer": metadata.tracker.track_buffer,
        },
        "weights": {"id": metadata.weights_id, "sha256": metadata.weights_sha256},
        "precision": metadata.precision,
    }


def write_metadata(path: Path, metadata: RunMetadata) -> None:
    write_text_atomic(path, json.dumps(_metadata_document(metadata), indent=2, sort_keys=True) + "\n")
