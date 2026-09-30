"""Start Audiostream when Windows signs in. The tray checkbox turns this on."""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE = "Audiostream"


def launch_command() -> str:
    """Command Windows should run at sign-in."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.exists():
        executable = pythonw
    return f'"{executable}" -m audiostream pc1'


def startup_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, STARTUP_VALUE)
    except OSError:
        return False
    return bool(str(value).strip())


def set_startup(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE, 0, winreg.REG_SZ, launch_command())
            return
        try:
            winreg.DeleteValue(key, STARTUP_VALUE)
        except OSError:
            pass
