"""WASAPI loopback capture on Windows.

This path does not import NumPy. Python 3.14 on Windows can install NumPy
and then fail to load its DLL, which blocked the sender entirely.
"""

from __future__ import annotations

import threading

from audiostream.audio import AudioError, DeviceInfo
from audiostream.pcm import fit_s16le


class WindowsCapture:
    def __init__(self, name: str, stream, pa, opened_channels: int, channels: int, frames: int, sample_rate: int):
        self.name = name
        self.sample_rate = sample_rate
        self.channels = channels
        self._stream = stream
        self._pa = pa
        self._opened_channels = opened_channels
        self._frames = frames
        self._close_lock = threading.Lock()

    def read(self) -> bytes:
        raw = self._stream.read(self._frames, exception_on_overflow=False)
        return fit_s16le(raw, self._opened_channels, self.channels)

    def close(self) -> None:
        with self._close_lock:
            stream = self._stream
            pa = self._pa
            if stream is None:
                return
            self._stream = None
            self._pa = None
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass


def list_loopbacks() -> list[DeviceInfo]:
    pa = _pyaudio()
    try:
        infos = _loopback_infos(pa)
        devices = []
        for number, info in enumerate(infos, start=1):
            devices.append(
                DeviceInfo(
                    index=number,
                    name=str(info["name"]),
                    device_id=str(info["index"]),
                    channels=int(info["maxInputChannels"]),
                    kind="loopback",
                )
            )
        return devices
    finally:
        pa.terminate()


def open_capture(choice: str | None, sample_rate: int, channels: int, frames: int) -> WindowsCapture:
    pa = _pyaudio()
    try:
        infos = _loopback_infos(pa)
    except AudioError:
        pa.terminate()
        raise
    if not infos:
        pa.terminate()
        raise AudioError(
            "No WASAPI loopback device found. The sender records what Windows is playing, "
            "not the microphone. Enable a playback device and try again."
        )
    info = _choose(pa, infos, choice)
    try:
        stream, opened_channels, opened_rate = _open_stream(pa, info, sample_rate, channels, frames)
    except Exception as exc:
        pa.terminate()
        raise AudioError(f"Could not open loopback {info['name']!r}: {exc}") from exc
    if opened_rate != sample_rate or opened_channels != channels:
        print(
            f"note: {info['name']} opened at {opened_rate} Hz, {opened_channels} ch. "
            f"The stream is sent as {opened_rate} Hz, {channels} ch.",
            flush=True,
        )
    return WindowsCapture(
        name=str(info["name"]),
        stream=stream,
        pa=pa,
        opened_channels=opened_channels,
        channels=channels,
        frames=frames,
        sample_rate=opened_rate,
    )


def _pyaudio():
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise AudioError(
            "The Windows audio library is missing. In the audiostream folder, run this on its own line:\n"
            "  python -m pip install -r requirements.txt"
        ) from exc
    try:
        return pyaudio.PyAudio()
    except Exception as exc:
        raise AudioError(f"Could not start Windows audio: {exc}") from exc


def _loopback_infos(pa) -> list[dict]:
    try:
        return list(pa.get_loopback_device_info_generator())
    except Exception as exc:
        raise AudioError(
            "WASAPI loopback is not available, so system audio cannot be captured. "
            f"Details: {exc}"
        ) from exc


def _choose(pa, infos: list[dict], choice: str | None) -> dict:
    if choice is None:
        return _default_loopback(pa, infos)
    if choice.isdigit():
        number = int(choice)
        if number < 1 or number > len(infos):
            hint = " Device numbers start at 1." if number == 0 else ""
            raise AudioError(
                f"Loopback device {number} is not in the list.{hint} Valid numbers are 1..{len(infos)}."
            )
        return infos[number - 1]
    matches = [info for info in infos if choice.lower() in str(info["name"]).lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise AudioError(f"No loopback device matching {choice!r}. Run: python -m audiostream sender --list-devices")
    names = ", ".join(str(info["name"]) for info in matches)
    raise AudioError(f"{choice!r} matches more than one loopback: {names}")


def _default_loopback(pa, infos: list[dict]) -> dict:
    try:
        import pyaudiowpatch as pyaudio

        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        name = str(default["name"])
    except Exception:
        return infos[0]
    for info in infos:
        loop_name = str(info["name"])
        if name == loop_name or name in loop_name or loop_name in name:
            return info
    return infos[0]


def _open_stream(pa, info: dict, sample_rate: int, channels: int, frames: int):
    import pyaudiowpatch as pyaudio

    native_channels = max(1, int(info["maxInputChannels"]))
    native_rate = int(info["defaultSampleRate"]) or sample_rate
    attempts = [(channels, sample_rate), (native_channels, sample_rate), (native_channels, native_rate)]
    last_error: Exception | None = None
    seen: set[tuple[int, int]] = set()
    for attempt_channels, attempt_rate in attempts:
        if attempt_channels < 1 or attempt_rate < 1 or (attempt_channels, attempt_rate) in seen:
            continue
        seen.add((attempt_channels, attempt_rate))
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=attempt_channels,
                rate=attempt_rate,
                frames_per_buffer=frames,
                input=True,
                input_device_index=int(info["index"]),
            )
            return stream, attempt_channels, attempt_rate
        except Exception as exc:
            last_error = exc
    raise AudioError(str(last_error) if last_error else "WASAPI rejected the capture format")


class WindowsOutput:
    """Play s16le audio to a WASAPI output. No NumPy."""

    def __init__(self, name: str, stream, pa, channels: int, sample_rate: int):
        self.name = name
        self.channels = channels
        self.sample_rate = sample_rate
        self._stream = stream
        self._pa = pa
        self._close_lock = threading.Lock()

    def write(self, pcm: bytes, src_channels: int | None = None) -> None:
        from audiostream.pcm import fit_s16le

        source = self.channels if src_channels is None else src_channels
        fitted = fit_s16le(pcm, source, self.channels)
        if fitted:
            self._stream.write(fitted)

    def close(self) -> None:
        with self._close_lock:
            stream = self._stream
            pa = self._pa
            if stream is None:
                return
            self._stream = None
            self._pa = None
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass


def list_outputs() -> list[DeviceInfo]:
    pa = _pyaudio()
    try:
        infos = _output_infos(pa)
        devices = []
        for number, info in enumerate(infos, start=1):
            devices.append(
                DeviceInfo(
                    index=number,
                    name=str(info["name"]),
                    device_id=str(info["index"]),
                    channels=int(info["maxOutputChannels"]),
                    kind="output",
                )
            )
        return devices
    finally:
        pa.terminate()


def open_output(choice: str | None, sample_rate: int, channels: int, frames: int) -> WindowsOutput:
    pa = _pyaudio()
    try:
        infos = _output_infos(pa)
    except AudioError:
        pa.terminate()
        raise
    if not infos:
        pa.terminate()
        raise AudioError("No playback device found. Connect a speaker and try again.")
    info = _choose_output(pa, infos, choice)
    try:
        stream, opened_channels, opened_rate = _open_output_stream(pa, info, sample_rate, channels, frames)
    except Exception as exc:
        pa.terminate()
        raise AudioError(f"Could not open playback {info['name']!r}: {exc}") from exc
    return WindowsOutput(
        name=str(info["name"]),
        stream=stream,
        pa=pa,
        channels=opened_channels,
        sample_rate=opened_rate,
    )


def play_beeps(choice: str | None) -> str:
    """Play two short beeps on a playback device. Returns the device name."""
    from audiostream.pcm import beeps_s16le

    player = open_output(choice, 48000, 2, 480)
    try:
        player.write(beeps_s16le(48000, player.channels))
        return player.name
    finally:
        player.close()


def _output_infos(pa) -> list[dict]:
    try:
        import pyaudiowpatch as pyaudio

        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        host_index = int(wasapi["index"])
    except Exception as exc:
        raise AudioError(f"WASAPI playback is not available. Details: {exc}") from exc
    infos = []
    for index in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(index)
        if int(info.get("hostApi", -1)) != host_index:
            continue
        if int(info.get("maxOutputChannels", 0)) < 1:
            continue
        if info.get("isLoopbackDevice"):
            continue
        infos.append(info)
    return infos


def _choose_output(pa, infos: list[dict], choice: str | None) -> dict:
    if choice is None:
        try:
            import pyaudiowpatch as pyaudio

            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_index = int(wasapi["defaultOutputDevice"])
        except Exception:
            return infos[0]
        for info in infos:
            if int(info["index"]) == default_index:
                return info
        return infos[0]
    if choice.isdigit():
        number = int(choice)
        if number < 1 or number > len(infos):
            raise AudioError(f"Playback device {number} is not in the list. Valid numbers are 1..{len(infos)}.")
        return infos[number - 1]
    matches = [info for info in infos if choice.lower() in str(info["name"]).lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise AudioError(f"No playback device matching {choice!r}.")
    names = ", ".join(str(info["name"]) for info in matches)
    raise AudioError(f"{choice!r} matches more than one playback device: {names}")


def _open_output_stream(pa, info: dict, sample_rate: int, channels: int, frames: int):
    import pyaudiowpatch as pyaudio

    native_channels = max(1, int(info["maxOutputChannels"]))
    native_rate = int(info["defaultSampleRate"]) or sample_rate
    attempts = [(channels, sample_rate), (native_channels, sample_rate), (native_channels, native_rate)]
    last_error: Exception | None = None
    seen: set[tuple[int, int]] = set()
    for attempt_channels, attempt_rate in attempts:
        if attempt_channels < 1 or attempt_rate < 1 or (attempt_channels, attempt_rate) in seen:
            continue
        seen.add((attempt_channels, attempt_rate))
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=attempt_channels,
                rate=attempt_rate,
                frames_per_buffer=frames,
                output=True,
                output_device_index=int(info["index"]),
            )
            return stream, attempt_channels, attempt_rate
        except Exception as exc:
            last_error = exc
    raise AudioError(str(last_error) if last_error else "WASAPI rejected the playback format")
