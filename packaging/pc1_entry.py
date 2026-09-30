"""Windows entry for Audiostream PC1. PyInstaller builds AudiostreamPC1.exe from this file."""

import pyaudiowpatch  # noqa: F401  (kept so the exe bundles WASAPI)

from audiostream.pc1_app import run_pc1

if __name__ == "__main__":
    run_pc1()
