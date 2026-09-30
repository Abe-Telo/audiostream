"""Volume, mute, disconnect, and pairing for speakers on this Windows PC.

Playback devices include Bluetooth headphones once Windows is using them.
Other computers in the room send a command here; this module does the change.
"""

from __future__ import annotations

import sys


class BtError(RuntimeError):
    pass


def list_playback() -> list[dict]:
    if sys.platform != "win32":
        return []
    try:
        return _endpoint_list()
    except Exception as exc:
        raise BtError(f"Could not list speakers: {exc}") from exc


def set_volume(device_id: str, level: float) -> None:
    _with_volume(device_id, lambda volume: volume.SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(level))), None))


def set_mute(device_id: str, muted: bool) -> None:
    _with_volume(device_id, lambda volume: volume.SetMute(1 if muted else 0, None))


def disconnect(name: str) -> None:
    """Unpair a Bluetooth device so it stops playing on this computer."""
    if sys.platform != "win32":
        raise BtError("Bluetooth disconnect runs on Windows.")
    address = _address_for_name(name, inquiry=False)
    if address is None:
        raise BtError(f"No paired Bluetooth device named {name!r}.")
    _remove_address(address)


def nearby_bluetooth() -> list[dict]:
    """Devices that are nearby and not necessarily paired. Takes a few seconds."""
    if sys.platform != "win32":
        return []
    return _search(inquiry=True)


def pair(address: int) -> None:
    if sys.platform != "win32":
        raise BtError("Bluetooth pairing runs on Windows.")
    _authenticate(int(address))


def _endpoint_list() -> list[dict]:
    import comtypes
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    paired = {item["name"].casefold() for item in _search(inquiry=False)}
    devices = []
    for device in AudioUtilities.GetAllDevices():
        name = getattr(device, "FriendlyName", None) or getattr(device, "name", None) or ""
        if not name:
            continue
        state = str(getattr(device, "state", "") or "")
        if state and "Active" not in state and state not in {"1", "DeviceState.Active"}:
            continue
        volume = _volume_of(device)
        if volume is None:
            continue
        try:
            level = float(volume.GetMasterVolumeLevelScalar())
            muted = bool(volume.GetMute())
        except Exception:
            continue
        bluetooth = name.casefold() in paired or "bluetooth" in name.casefold()
        devices.append(
            {
                "id": str(getattr(device, "id", name)),
                "name": name,
                "volume": max(0.0, min(1.0, level)),
                "muted": muted,
                "bluetooth": bluetooth,
            }
        )
    return devices


def _volume_of(device):
    volume = getattr(device, "EndpointVolume", None)
    if volume is not None:
        return volume
    try:
        from comtypes import CLSCTX_ALL, POINTER, cast
        from pycaw.pycaw import IAudioEndpointVolume

        interface = device._dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        return None


def _with_volume(device_id: str, action) -> None:
    if sys.platform != "win32":
        raise BtError("Volume control runs on Windows.")
    for device in _endpoint_list():
        if device["id"] != device_id and device["name"] != device_id:
            continue
        import comtypes
        from pycaw.pycaw import AudioUtilities

        comtypes.CoInitialize()
        for raw in AudioUtilities.GetAllDevices():
            if str(getattr(raw, "id", "")) == device["id"] or (getattr(raw, "FriendlyName", "") == device["name"]):
                volume = _volume_of(raw)
                if volume is None:
                    break
                action(volume)
                return
    raise BtError("That speaker is no longer on this computer.")


def _search(inquiry: bool) -> list[dict]:
    if sys.platform != "win32":
        return []
    try:
        return _bluetooth_search(inquiry)
    except Exception:
        return []


def _bluetooth_search(inquiry: bool) -> list[dict]:
    import ctypes
    from ctypes import wintypes

    bth = ctypes.WinDLL("BluetoothApis")

    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(name, wintypes.WORD) for name in (
            "wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds"
        )]

    class BLUETOOTH_ADDRESS(ctypes.Structure):
        _fields_ = [("ullLong", ctypes.c_ulonglong)]

    class BLUETOOTH_DEVICE_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("Address", BLUETOOTH_ADDRESS),
            ("ulClassofDevice", wintypes.ULONG),
            ("fConnected", wintypes.BOOL),
            ("fRemembered", wintypes.BOOL),
            ("fAuthenticated", wintypes.BOOL),
            ("stLastSeen", SYSTEMTIME),
            ("stLastUsed", SYSTEMTIME),
            ("szName", wintypes.WCHAR * 248),
        ]

    class BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("fReturnAuthenticated", wintypes.BOOL),
            ("fReturnRemembered", wintypes.BOOL),
            ("fReturnUnknown", wintypes.BOOL),
            ("fReturnConnected", wintypes.BOOL),
            ("fIssueInquiry", wintypes.BOOL),
            ("cTimeoutMultiplier", ctypes.c_ubyte),
            ("hRadio", wintypes.HANDLE),
        ]

    radio = _first_radio(bth)
    params = BLUETOOTH_DEVICE_SEARCH_PARAMS()
    params.dwSize = ctypes.sizeof(params)
    params.fReturnAuthenticated = True
    params.fReturnRemembered = True
    params.fReturnUnknown = bool(inquiry)
    params.fReturnConnected = True
    params.fIssueInquiry = bool(inquiry)
    params.cTimeoutMultiplier = 4 if inquiry else 1
    params.hRadio = radio
    info = BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(info)
    found = []
    handle = bth.BluetoothFindFirstDevice(ctypes.byref(params), ctypes.byref(info))
    if not handle:
        return []
    try:
        while True:
            name = info.szName.strip()
            if name:
                found.append({"name": name, "address": int(info.Address.ullLong), "connected": bool(info.fConnected)})
            info = BLUETOOTH_DEVICE_INFO()
            info.dwSize = ctypes.sizeof(info)
            if not bth.BluetoothFindNextDevice(handle, ctypes.byref(info)):
                break
    finally:
        bth.BluetoothFindDeviceClose(handle)
    return found


def _first_radio(bth):
    import ctypes
    from ctypes import wintypes

    class PARAMS(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD)]

    params = PARAMS(dwSize=ctypes.sizeof(PARAMS))
    radio = wintypes.HANDLE()
    find = bth.BluetoothFindFirstRadio(ctypes.byref(params), ctypes.byref(radio))
    if not find:
        return None
    bth.BluetoothFindRadioClose(find)
    return radio


def _address_for_name(name: str, inquiry: bool) -> int | None:
    folded = name.casefold()
    for device in _search(inquiry):
        if device["name"].casefold() == folded:
            return int(device["address"])
    return None


def _remove_address(address: int) -> None:
    import ctypes
    from ctypes import wintypes

    class BLUETOOTH_ADDRESS(ctypes.Structure):
        _fields_ = [("ullLong", ctypes.c_ulonglong)]

    bth = ctypes.WinDLL("BluetoothApis")
    addr = BLUETOOTH_ADDRESS(int(address))
    bth.BluetoothRemoveDevice.argtypes = [ctypes.POINTER(BLUETOOTH_ADDRESS)]
    bth.BluetoothRemoveDevice.restype = wintypes.DWORD
    code = bth.BluetoothRemoveDevice(ctypes.byref(addr))
    if code not in (0,):
        raise BtError(f"Windows could not disconnect that device (error {code}).")


def _authenticate(address: int) -> None:
    import ctypes
    from ctypes import wintypes

    devices = [item for item in _search(inquiry=True) if int(item["address"]) == int(address)]
    if not devices:
        raise BtError("That device is no longer nearby.")
    # Re-read the full record by searching remembered/unknown and matching the address.
    bth = ctypes.WinDLL("BluetoothApis")

    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(name, wintypes.WORD) for name in (
            "wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds"
        )]

    class BLUETOOTH_ADDRESS(ctypes.Structure):
        _fields_ = [("ullLong", ctypes.c_ulonglong)]

    class BLUETOOTH_DEVICE_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("Address", BLUETOOTH_ADDRESS),
            ("ulClassofDevice", wintypes.ULONG),
            ("fConnected", wintypes.BOOL),
            ("fRemembered", wintypes.BOOL),
            ("fAuthenticated", wintypes.BOOL),
            ("stLastSeen", SYSTEMTIME),
            ("stLastUsed", SYSTEMTIME),
            ("szName", wintypes.WCHAR * 248),
        ]

    info = BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(info)
    info.Address.ullLong = int(address)
    info.szName = devices[0]["name"]
    # MITMProtectionNotDefined = 0. Windows shows its own pairing confirmation.
    bth.BluetoothAuthenticateDeviceEx.argtypes = [
        wintypes.HWND,
        wintypes.HANDLE,
        ctypes.POINTER(BLUETOOTH_DEVICE_INFO),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    bth.BluetoothAuthenticateDeviceEx.restype = wintypes.DWORD
    code = bth.BluetoothAuthenticateDeviceEx(None, _first_radio(bth), ctypes.byref(info), None, 0)
    if code not in (0,):
        raise BtError(f"Windows did not pair that device (error {code}).")
