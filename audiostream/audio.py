"""System-audio capture and playback via soundcard.

Windows uses WASAPI loopback (the desktop mix, not the microphone).
Linux uses the PulseAudio / PipeWire monitor of an output.
macOS CoreAudio cannot see the system mix; a virtual device such as
BlackHole is required and is not created by this app.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

MACOS_LIMITATION = (
    "macOS cannot capture the system mix. CoreAudio only exposes microphones, "
    "so desktop audio will not be recorded unless you install a virtual device "
    "(BlackHole or Loopback), route system output into it, and pass that input "
    "with --device. This app does not create that device."
)


class AudioError(RuntimeError):
    """The sound backend is missing, or the requested device cannot be opened."""


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    device_id: str
    channels: int
    kind: str  # "output", "loopback", or "input"


def import_numpy():
    """Import NumPy, or raise AudioError with a short Windows install hint."""
    try:
        import numpy as np
    except ImportError as exc:
        raise AudioError(numpy_failure_message(exc)) from exc
    return np


def numpy_failure_message(exc: BaseException) -> str:
    text = str(exc)
    windows_dll = sys.platform == "win32" and (
        "DLL" in text or "_multiarray" in text or "numpy" in text.lower()
    )
    if windows_dll:
        version = sys.version.split()[0]
        return (
            f"NumPy failed to load on Windows Python {version}, so audio cannot start.\n"
            "Install the Microsoft Visual C++ Redistributable (x64), then reinstall NumPy:\n"
            "  https://aka.ms/vs/17/release/vc_redist.x64.exe\n"
            "  python -m pip install --force-reinstall \"numpy>=1.24\"\n"
            "If that still fails, Python 3.14 is the problem. Install Python 3.12 or 3.13 "
            "from https://www.python.org/downloads/ and use that interpreter instead, "
            "for example:\n"
            "  py -3.12 -m pip install -r requirements.txt\n"
            "  py -3.12 -m audiostream sender --host RECEIVER_IP"
        )
    return f"NumPy failed to load: {exc}"


def float_to_s16le(samples: np.ndarray) -> bytes:
    np = import_numpy()
    arr = np.array(samples, dtype=np.float32, copy=True, order="C")
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    elif arr.ndim != 2:
        raise ValueError("audio must be a 1-D or 2-D array")
    np.clip(arr, -1.0, 1.0, out=arr)
    return (arr * 32767.0).astype("<i2").tobytes(order="C")


def s16le_to_float(pcm: bytes, channels: int) -> np.ndarray:
    np = import_numpy()
    if channels < 1:
        raise ValueError("channels must be positive")
    frame_bytes = channels * 2
    if len(pcm) % frame_bytes != 0:
        raise ValueError("pcm length is not a whole number of frames")
    data = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    data /= 32768.0
    return data.reshape(-1, channels)


def fit_channels(samples: np.ndarray, channels: int) -> np.ndarray:
    np = import_numpy()
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    src = arr.shape[1]
    if src == channels:
        return arr
    if channels == 1:
        return arr.mean(axis=1, keepdims=True).astype(np.float32)
    if src == 1:
        return np.repeat(arr, channels, axis=1)
    if src > channels:
        return np.ascontiguousarray(arr[:, :channels])
    pad = np.zeros((arr.shape[0], channels - src), dtype=np.float32)
    return np.concatenate([arr, pad], axis=1)


def sine_tone(
    seconds: float,
    sample_rate: int,
    channels: int,
    frequency: float = 440.0,
    amplitude: float = 0.2,
) -> np.ndarray:
    np = import_numpy()
    count = max(1, int(seconds * sample_rate))
    t = np.arange(count, dtype=np.float32) / float(sample_rate)
    wave = (amplitude * np.sin(2.0 * np.pi * frequency * t)).astype(np.float32)
    return np.repeat(wave.reshape(-1, 1), channels, axis=1)


def select_device(
    devices: list[DeviceInfo],
    choice: str | None,
    *,
    interactive: bool,
    input_fn=input,
    kind_label: str = "device",
) -> DeviceInfo:
    if not devices:
        raise AudioError(f"No {kind_label}s found.")
    if choice is None:
        if len(devices) == 1:
            return devices[0]
        if not interactive:
            raise AudioError(
                f"No --device given and stdin is not a terminal. "
                f"Pass --device with an index from --list-devices."
            )
        _print_devices(devices, kind_label)
        first = devices[0].index
        last = devices[-1].index
        raw = input_fn(f"Select {kind_label} [{first}-{last}] (default {first}): ").strip()
        choice = raw or str(first)
    choice = choice.strip()
    if choice.isdigit():
        number = int(choice)
        for device in devices:
            if device.index == number:
                return device
        numbers = ", ".join(str(device.index) for device in devices)
        hint = " Device numbers start at 1." if number == 0 else ""
        raise AudioError(
            f"Device {number} is not in the list.{hint} Valid numbers: {numbers}.\n"
            + format_device_list(devices)
        )
    matches = [device for device in devices if choice.lower() in device.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise AudioError(f"No {kind_label} matching {choice!r}. Run with --list-devices.")
    names = ", ".join(device.name for device in matches)
    raise AudioError(f"{choice!r} matches more than one {kind_label}: {names}")


def list_output_devices() -> list[DeviceInfo]:
    sc = load_backend()
    speakers = sc.all_speakers()
    return [_describe(number, speaker, "output") for number, speaker in enumerate(speakers, start=1)]


def list_loopback_devices() -> list[DeviceInfo]:
    if sys.platform == "win32":
        from audiostream.win_audio import list_loopbacks

        return list_loopbacks()
    sc = load_backend()
    return [_describe(number, mic, "loopback") for number, mic in enumerate(_loopback_mics(sc), start=1)]


def list_input_devices() -> list[DeviceInfo]:
    """Physical inputs. On macOS this is also where a virtual loopback shows up."""
    sc = load_backend()
    mics = sc.all_microphones(include_loopback=False)
    return [_describe(number, mic, "input") for number, mic in enumerate(mics, start=1)]


def open_output(device: DeviceInfo):
    sc = load_backend()
    try:
        return sc.get_speaker(device.device_id)
    except Exception:
        try:
            return sc.get_speaker(device.name)
        except Exception as exc:
            raise AudioError(f"Could not open output {device.name!r}: {exc}") from exc


def open_loopback(choice: str | None):
    """Return (soundcard microphone, human name) for the system mix."""
    sc = load_backend()
    if sys.platform == "darwin":
        if not choice:
            raise AudioError(MACOS_LIMITATION)
        try:
            mic = sc.get_microphone(choice if not choice.isdigit() else _input_id(sc, choice), include_loopback=True)
        except Exception as exc:
            raise AudioError(f"{MACOS_LIMITATION}\nCould not open {choice!r}: {exc}") from exc
        print(
            "warning: macOS has no system-audio loopback. "
            f"Recording {mic.name!r}. This is only the desktop mix if that device is a virtual loopback you already routed.",
            flush=True,
        )
        return mic

    mics = _loopback_mics(sc)
    if not mics:
        raise AudioError(_no_loopback_message())
    if choice is None:
        mic = _default_loopback(sc, mics)
        return mic
    if choice.isdigit():
        number = int(choice)
        if number < 1 or number > len(mics):
            hint = " Device numbers start at 1." if number == 0 else ""
            raise AudioError(
                f"Loopback device {number} is not in the list.{hint} "
                f"Valid numbers are 1..{len(mics)}."
            )
        return mics[number - 1]
    for mic in mics:
        if choice.lower() in mic.name.lower():
            return mic
    raise AudioError(f"No loopback device matching {choice!r}. Run: python -m audiostream sender --list-devices")


def play_tone(speaker, seconds: float, sample_rate: int, channels: int) -> None:
    device_channels = _channel_count(speaker)
    play_channels = channels if device_channels <= 0 else min(channels, device_channels)
    tone = fit_channels(sine_tone(seconds, sample_rate, play_channels), play_channels)
    try:
        speaker.play(tone, samplerate=sample_rate, channels=play_channels)
    except Exception as exc:
        raise AudioError(f"Could not play the test tone: {exc}") from exc


def load_backend():
    import_numpy()
    try:
        import soundcard as sc
    except ImportError as exc:
        if "numpy" in str(exc).lower() or "DLL" in str(exc) or "_multiarray" in str(exc):
            raise AudioError(numpy_failure_message(exc)) from exc
        raise AudioError(_backend_failure(exc)) from exc
    except Exception as exc:
        raise AudioError(_backend_failure(exc)) from exc
    return sc


def _backend_failure(exc: BaseException) -> str:
    if isinstance(exc, AssertionError):
        detail = "The audio server did not become ready."
    else:
        detail = f"Details: {exc!r}"
    if sys.platform.startswith("linux"):
        return (
            "The audio backend failed to start. On Linux, system audio goes through "
            "PulseAudio or PipeWire (the Pulse compatibility socket). "
            "Start that audio server and try again. " + detail
        )
    if sys.platform == "win32":
        return (
            "The audio backend failed to start. On Windows this app uses WASAPI "
            "through the soundcard package. " + detail
        )
    if sys.platform == "darwin":
        return "The audio backend failed to start. " + MACOS_LIMITATION + " " + detail
    return "The audio backend failed to start. " + detail


def _no_loopback_message() -> str:
    if sys.platform == "win32":
        return (
            "No WASAPI loopback device found. The sender records the Windows playback mix "
            "(what you hear), not the microphone. Enable a playback device and try again."
        )
    if sys.platform.startswith("linux"):
        return (
            "No monitor source found. On Linux the sender records the PulseAudio or PipeWire "
            "monitor of an output, which is the system mix, not the microphone. "
            "Check that PipeWire or PulseAudio is running and an output exists."
        )
    return MACOS_LIMITATION


def _loopback_mics(sc) -> list:
    mics = sc.all_microphones(include_loopback=True)
    loopbacks = []
    for mic in mics:
        try:
            is_loopback = bool(mic.isloopback)
        except Exception:
            is_loopback = False
        if is_loopback:
            loopbacks.append(mic)
    return loopbacks


def _default_loopback(sc, mics: list):
    try:
        speaker = sc.default_speaker()
    except Exception as exc:
        raise AudioError(f"No default output device to capture: {exc}") from exc
    speaker_id = str(getattr(speaker, "id", ""))
    speaker_name = getattr(speaker, "name", "") or ""
    for mic in mics:
        if str(getattr(mic, "id", "")) == speaker_id + ".monitor":
            return mic
    for mic in mics:
        if speaker_name and speaker_name == mic.name:
            return mic
    for mic in mics:
        if speaker_name and speaker_name in mic.name:
            return mic
    return mics[0]


def _input_id(sc, choice: str):
    inputs = list_input_devices()
    # list_input_devices loads the backend again; indexes still match all_microphones order.
    selected = select_device(inputs, choice, interactive=False, kind_label="input")
    return selected.device_id


def _describe(index: int, device, kind: str) -> DeviceInfo:
    name = getattr(device, "name", None) or f"{kind} {index}"
    device_id = str(getattr(device, "id", name))
    return DeviceInfo(
        index=index,
        name=name,
        device_id=device_id,
        channels=_channel_count(device),
        kind=kind,
    )


def _channel_count(device) -> int:
    try:
        value = device.channels
    except Exception:
        return 0
    if isinstance(value, int):
        return value
    try:
        return len(value)
    except TypeError:
        return 0


def _print_devices(devices: list[DeviceInfo], kind_label: str) -> None:
    print(f"{kind_label.capitalize()}s:")
    for device in devices:
        channels = f"{device.channels} ch" if device.channels else "channels unknown"
        print(f"  [{device.index}] {device.name}  ({channels})")


def format_device_list(devices: list[DeviceInfo]) -> str:
    lines = []
    for device in devices:
        channels = f"{device.channels} ch" if device.channels else "channels unknown"
        lines.append(f"[{device.index}] {device.name}  ({channels})")
    return "\n".join(lines)
