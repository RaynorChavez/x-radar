#!/usr/bin/env python3
"""Build the deterministic, Pi-only X Radar application artifact."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import stat
import tarfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
COMMIT = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RUNTIME_FILES = (
    "bin/backup",
    "bin/collect-once",
    "bin/collect-pipeline",
    "bin/dispatch-queued",
    "bin/doctor",
    "bin/embed-pending",
    "bin/semantic-server",
    "bin/sync",
    "collector/firefox_collect.py",
    "config/blocklist.example.json",
    "docs/collector-protocol.md",
    "docs/pi-automation-prompt.md",
    "pyproject.toml",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def release_files(root: Path = ROOT) -> list[Path]:
    paths = [root / relative for relative in RUNTIME_FILES]
    paths.extend(sorted((root / "src/xradar").glob("*.py")))
    missing = [str(path.relative_to(root)) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"release files are missing: {', '.join(missing)}")
    if any(path.is_symlink() for path in paths):
        raise ValueError("release files must not be symlinks")
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def canonical_member(path: str, payload: bytes, mode: int) -> tuple[tarfile.TarInfo, io.BytesIO]:
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe release member: {path}")
    info = tarfile.TarInfo(relative.as_posix())
    info.size = len(payload)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    return info, io.BytesIO(payload)


def build(repository: str, commit: str, output_dir: Path, root: Path = ROOT) -> dict[str, object]:
    repository = repository.strip()
    commit = commit.strip().lower()
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("repository must use owner/name form")
    if not COMMIT.fullmatch(commit):
        raise ValueError("commit must be a full lowercase Git SHA")

    files = release_files(root)
    entries: list[tuple[str, bytes, int]] = []
    manifest_files: list[dict[str, str]] = []
    for source in files:
        relative = source.relative_to(root).as_posix()
        payload = source.read_bytes()
        executable = bool(source.stat().st_mode & stat.S_IXUSR)
        entries.append((relative, payload, 0o755 if executable else 0o644))
        manifest_files.append({"path": relative, "sha256": sha256_bytes(payload)})

    embedded = {
        "schema_version": 1,
        "application": "x-radar",
        "source_repository": repository,
        "commit": commit,
        "files": manifest_files,
    }
    embedded_bytes = (json.dumps(embedded, indent=2, sort_keys=True) + "\n").encode()
    entries.append(("release.json", embedded_bytes, 0o644))
    entries.sort(key=lambda item: item[0])

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_name = f"x-radar-app-{commit[:12]}.tar.gz"
    artifact = output_dir / artifact_name
    with artifact.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, payload, mode in entries:
                    info, stream = canonical_member(name, payload, mode)
                    archive.addfile(info, stream)

    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    checksum = output_dir / f"{artifact_name}.sha256"
    checksum.write_text(f"{digest}  {artifact_name}\n")
    metadata = {
        "schema_version": 1,
        "application": "x-radar",
        "source_repository": repository,
        "commit": commit,
        "artifact": {
            "name": artifact_name,
            "sha256": digest,
            "size": artifact.stat().st_size,
        },
    }
    (output_dir / "release-metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    metadata = build(args.repository, args.commit, args.output_dir)
    print(json.dumps(metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
