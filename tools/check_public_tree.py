from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TRACKED = {
    ".env",
    "site/.openai/hosting.json",
    "public/feed.json",
}
FORBIDDEN_SUFFIXES = {".sqlite", ".sqlite-shm", ".sqlite-wal"}
PATTERNS = {
    "absolute macOS home path": re.compile(r"/Users/[^/\s]+/"),
    "absolute Linux user path": re.compile(r"/home/(?!example(?:/|\b))[^/\s]+/"),
    "live Sites project identifier": re.compile(r"appgprj_[a-z0-9]+"),
    "live Sites hostname": re.compile(r"[a-z0-9-]+\.chatgpt\.site"),
    "Tailscale IPv4 address": re.compile(r"\b100(?:\.\d{1,3}){3}\b"),
}


def tracked_files() -> list[str]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [item.decode() for item in output.split(b"\0") if item]


def main() -> int:
    failures: list[str] = []
    for relative in tracked_files():
        if relative == "tools/check_public_tree.py":
            continue
        if relative in FORBIDDEN_TRACKED or any(relative.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            failures.append(f"forbidden tracked runtime file: {relative}")
            continue
        path = ROOT / relative
        try:
            text = path.read_text()
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{label}: {relative}")
    if failures:
        print("Public-tree audit failed:")
        for failure in sorted(set(failures)):
            print(f"- {failure}")
        return 1
    print("Public-tree audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
