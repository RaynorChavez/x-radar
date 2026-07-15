#!/usr/bin/env python3
"""Install the pinned ONNX embedding model used by X Radar.

The installer downloads from an immutable Hugging Face revision, verifies every
file, and never replaces a valid file with an incomplete download.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import BinaryIO, Callable

import certifi


MODEL_REPOSITORY = "Xenova/bge-small-en-v1.5"
MODEL_REVISION = "ea104dacec62c0de699686887e3f920caeb4f3e3"
DEFAULT_DIRECTORY = Path("var/models/bge-small-en-v1.5-onnx")
FILES = {
    "model.onnx": ("onnx/model.onnx", "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35"),
    "tokenizer/config.json": ("config.json", "fa73f90bf92c8cace1fbcb709626306f2bdbc9ea3e5b5f94b440df9b6aa56350"),
    "tokenizer/special_tokens_map.json": ("special_tokens_map.json", "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3"),
    "tokenizer/tokenizer_config.json": ("tokenizer_config.json", "9261e7d79b44c8195c1cada2b453e55b00aeb81e907a6664974b4d7776172ab3"),
    "tokenizer/tokenizer.json": ("tokenizer.json", "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"),
    "tokenizer/vocab.txt": ("vocab.txt", "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3"),
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def problems(directory: Path) -> list[str]:
    result: list[str] = []
    for destination, (_source, expected) in FILES.items():
        path = directory / destination
        if not path.is_file():
            result.append(f"missing {destination}")
        elif digest(path) != expected:
            result.append(f"checksum mismatch for {destination}")
    return result


def source_url(source: str) -> str:
    return f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{source}"


def open_url(url: str) -> BinaryIO:
    request = urllib.request.Request(url, headers={"User-Agent": "x-radar-model-installer/1"})
    context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.urlopen(  # noqa: S310 - fixed HTTPS origin and revision
        request,
        timeout=60,
        context=context,
    )


def install(directory: Path, *, opener: Callable[[str], BinaryIO] = open_url) -> dict[str, object]:
    directory = directory.expanduser().resolve()
    existing = problems(directory)
    if not existing:
        return {"directory": str(directory), "downloaded": 0, "status": "already-installed"}

    downloaded = 0
    for destination, (source, expected) in FILES.items():
        target = directory / destination
        if target.is_file() and digest(target) == expected:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.partial")
        partial.unlink(missing_ok=True)
        print(f"Downloading {source}...", file=sys.stderr)
        try:
            with opener(source_url(source)) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            actual = digest(partial)
            if actual != expected:
                raise RuntimeError(f"checksum mismatch for {source}: expected {expected}, got {actual}")
            os.replace(partial, target)
            downloaded += 1
        finally:
            partial.unlink(missing_ok=True)

    remaining = problems(directory)
    if remaining:
        raise RuntimeError("embedding model verification failed: " + "; ".join(remaining))
    metadata = {
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "modelSha256": FILES["model.onnx"][1],
    }
    (directory / "MODEL_SOURCE.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return {"directory": str(directory), "downloaded": downloaded, "status": "installed"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install X Radar's pinned BGE ONNX embedding model.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.environ.get("XRADAR_EMBEDDING_MODEL_DIR", DEFAULT_DIRECTORY)),
        help="model directory (default: XRADAR_EMBEDDING_MODEL_DIR or var/models/bge-small-en-v1.5-onnx)",
    )
    parser.add_argument("--check", action="store_true", help="verify an existing installation without downloading")
    args = parser.parse_args(argv)
    if args.check:
        errors = problems(args.output.expanduser().resolve())
        if errors:
            print("Embedding model is not ready: " + "; ".join(errors), file=sys.stderr)
            return 1
        print(json.dumps({"directory": str(args.output.expanduser().resolve()), "status": "ok"}))
        return 0
    print(json.dumps(install(args.output)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
