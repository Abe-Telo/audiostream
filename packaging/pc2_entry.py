"""Windows entry for Audiostream PC2. PyInstaller builds AudiostreamPC2.exe from this file."""

import pyaudiowpatch  # noqa: F401  (kept so the exe bundles WASAPI)

from audiostream.pc2_app import run_pc2

if __name__ == "__main__":
    run_pc2()
