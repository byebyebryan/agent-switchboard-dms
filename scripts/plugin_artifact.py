"""Deterministic DMS 0.5 plugin artifact construction and validation."""

from __future__ import annotations

import hashlib
import json
import stat
import time
import zipfile
from pathlib import Path
from typing import Final

ADAPTER_VERSION: Final = "0.5.0"
ARTIFACT_VERSION: Final = 1
PREFIX: Final = "switchboard/"
MANIFEST_NAME: Final = f"{PREFIX}artifact-manifest.json"
PLUGIN_FILES: Final = (
    "LICENSE",
    "README.md",
    "SwitchboardEntryModelV1.js",
    "SwitchboardLauncher.qml",
    "SwitchboardSettings.qml",
    "plugin.json",
    "switchboard-bridge",
    "switchboard-open",
    "switchboard_dms/__init__.py",
    "switchboard_dms/bridge.py",
    "switchboard_dms/desktop.py",
    "switchboard_dms/process.py",
    "switchboard_dms/protocol.py",
)
EXECUTABLE_FILES: Final = frozenset({"switchboard-bridge", "switchboard-open"})


class ArtifactError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        + b"\n"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_files(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for name in PLUGIN_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ArtifactError(f"plugin source is missing or unsafe: {name}")
        result[name] = path.read_bytes()
    plugin = json.loads(result["plugin.json"])
    if plugin.get("id") != "switchboard" or plugin.get("version") != ADAPTER_VERSION:
        raise ArtifactError("plugin metadata is incompatible")
    return result


def manifest(files: dict[str, bytes]) -> bytes:
    return _canonical(
        {
            "artifactVersion": ARTIFACT_VERSION,
            "adapterVersion": ADAPTER_VERSION,
            "pairedCoreVersion": "0.3.0",
            "files": {name: _sha256(files[name]) for name in sorted(files)},
        }
    )


def _zip_time(epoch: int) -> tuple[int, int, int, int, int, int]:
    value = max(epoch, 315532800)
    return time.gmtime(value)[:6]


def build(root: Path, destination: Path, *, epoch: int) -> str:
    files = source_files(root)
    payloads = {f"{PREFIX}{name}": value for name, value in files.items()}
    payloads[MANIFEST_NAME] = manifest(files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists():
        raise ArtifactError("temporary artifact already exists")
    try:
        with zipfile.ZipFile(
            temporary,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for name in sorted(payloads):
                relative = name.removeprefix(PREFIX)
                mode = 0o755 if relative in EXECUTABLE_FILES else 0o644
                info = zipfile.ZipInfo(name, _zip_time(epoch))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, payloads[name], compresslevel=9)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return _sha256(destination.read_bytes())


def read_archive(path: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size > 16 * 1024 * 1024
    ):
        raise ArtifactError("plugin artifact is missing or unsafe")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            expected = {f"{PREFIX}{name}" for name in PLUGIN_FILES} | {MANIFEST_NAME}
            if len(names) != len(set(names)) or set(names) != expected:
                raise ArtifactError("plugin artifact contents are incompatible")
            payloads = {name: archive.read(name) for name in names}
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise ArtifactError("plugin artifact is invalid") from error
    try:
        document = json.loads(payloads.pop(MANIFEST_NAME))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactError("plugin artifact manifest is invalid") from error
    expected_fields = {
        "artifactVersion",
        "adapterVersion",
        "pairedCoreVersion",
        "files",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected_fields
        or document["artifactVersion"] != ARTIFACT_VERSION
        or document["adapterVersion"] != ADAPTER_VERSION
        or document["pairedCoreVersion"] != "0.3.0"
        or not isinstance(document["files"], dict)
    ):
        raise ArtifactError("plugin artifact manifest is incompatible")
    hashes = document["files"]
    if set(hashes) != set(PLUGIN_FILES):
        raise ArtifactError("plugin artifact manifest file set is incompatible")
    for name in PLUGIN_FILES:
        if hashes[name] != _sha256(payloads[f"{PREFIX}{name}"]):
            raise ArtifactError(f"plugin artifact hash mismatch: {name}")
    return payloads, document


__all__ = [
    "ADAPTER_VERSION",
    "ArtifactError",
    "EXECUTABLE_FILES",
    "PLUGIN_FILES",
    "build",
    "read_archive",
]
