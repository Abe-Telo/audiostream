"""System tray icon for the Windows notification area."""

from __future__ import annotations

import sys
from pathlib import Path


def set_window_icon(root) -> None:
    try:
        root.iconbitmap(default=str(icon_path()))
    except Exception:
        pass


def icon_path() -> Path:
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(__file__).resolve().parent.parent)
    roots.append(Path(__file__).resolve().parent)
    for root in roots:
        for candidate in (root / "audiostream" / "icon.ico", root / "icon.ico"):
            if candidate.exists():
                return candidate
    return Path(__file__).resolve().parent / "icon.ico"


class TrayIcon:
    """Notification-area icon. On Windows it is the tray by the clock."""

    def __init__(self, root, tooltip: str, items) -> None:
        self.root = root
        self.tooltip = tooltip
        self.items = items
        self.installed = False
        self._wndproc = None
        self._old_proc = None
        self._icon = None
        self._nid = None

    def install(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            self._install_windows()
        except Exception:
            self.installed = False
            return False
        self.installed = True
        return True

    def balloon(self, title: str, message: str) -> None:
        if sys.platform != "win32" or not self.installed or self._nid is None:
            return
        self._notify(title, message)

    def set_tooltip(self, text: str) -> None:
        self.tooltip = text
        if sys.platform != "win32" or not self.installed or self._nid is None:
            return
        self._set_tip(text)

    def remove(self) -> None:
        if sys.platform == "win32" and self._nid is not None:
            try:
                import ctypes

                ctypes.windll.shell32.Shell_NotifyIconW(2, ctypes.byref(self._nid))
            except Exception:
                pass
        self.installed = False
        self._nid = None

    def _install_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        hwnd = wintypes.HWND(int(self.root.winfo_id()))
        self._icon = _load_icon(icon_path())

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wintypes.HICON),
            ]

        nif_message = 0x1
        nif_icon = 0x2
        nif_tip = 0x4
        nif_info = 0x10
        wm_app = 0x8000
        nim_add = 0
        nim_modify = 1
        nim_setversion = 4

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = nif_message | nif_icon | nif_tip
        nid.uCallbackMessage = wm_app
        nid.hIcon = self._icon
        nid.szTip = self.tooltip[:127]
        if not shell32.Shell_NotifyIconW(nim_add, ctypes.byref(nid)):
            raise OSError("Shell_NotifyIcon add failed")
        nid.uTimeoutOrVersion = 4
        shell32.Shell_NotifyIconW(nim_setversion, ctypes.byref(nid))
        self._nid = nid
        self._nim_modify = nim_modify
        self._nif_tip = nif_tip
        self._nif_info = nif_info
        self._wm_app = wm_app

        pointer = ctypes.c_ssize_t
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
        else:
            get_long = user32.GetWindowLongW
            set_long = user32.SetWindowLongW
        get_long.restype = ctypes.c_void_p
        set_long.restype = ctypes.c_void_p
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.CallWindowProcW.restype = pointer
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        gwlp_wndproc = -4
        self._old_proc = get_long(hwnd, gwlp_wndproc)
        cmpfunc = ctypes.WINFUNCTYPE(pointer, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        def wndproc(window, msg, wparam, lparam):
            if msg == wm_app:
                event = int(lparam) & 0xFFFF
                # Left click opens the window. Right click opens the menu.
                if event in (0x0201, 0x0202, 0x0203, 0x0400):
                    self.root.after(0, self._open)
                elif event in (0x0205, 0x007B):
                    self.root.after(0, self._popup)
                return 0
            return user32.CallWindowProcW(self._old_proc, window, msg, wparam, lparam)

        self._wndproc = cmpfunc(wndproc)
        set_long(hwnd, gwlp_wndproc, ctypes.cast(self._wndproc, ctypes.c_void_p).value)

    def _notify(self, title: str, message: str) -> None:
        import ctypes

        nid = self._nid
        nid.uFlags = self._nif_info | self._nif_tip
        nid.szInfoTitle = title[:63]
        nid.szInfo = message[:255]
        nid.dwInfoFlags = 1
        nid.szTip = self.tooltip[:127]
        ctypes.windll.shell32.Shell_NotifyIconW(self._nim_modify, ctypes.byref(nid))

    def _set_tip(self, text: str) -> None:
        import ctypes

        nid = self._nid
        nid.uFlags = self._nif_tip
        nid.szTip = text[:127]
        ctypes.windll.shell32.Shell_NotifyIconW(self._nim_modify, ctypes.byref(nid))

    def _open(self) -> None:
        for label, command in self.items():
            if label == "Open":
                command()
                return

    def _popup(self) -> None:
        import tkinter as tk

        menu = tk.Menu(self.root, tearoff=0)
        for item in self.items():
            if item is None:
                menu.add_separator()
            else:
                label, command = item
                menu.add_command(label=label, command=command)
        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()


def _load_icon(path: Path):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    image_icon = 1
    lr_loadfromfile = 0x0010
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    if path.exists():
        handle = user32.LoadImageW(None, str(path), image_icon, 0, 0, lr_loadfromfile)
        if handle:
            return wintypes.HICON(handle)
    return user32.LoadIconW(None, 32512)
