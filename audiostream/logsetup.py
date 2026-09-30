"""Keep print() working when the Windows exe has no console."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_stdio() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    folder = Path(os.environ.get("APPDATA") or Path.home()) / "audiostream"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handle = open(folder / "audiostream.log", "a", encoding="utf-8")
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle
