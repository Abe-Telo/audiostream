"""Audio backend errors stay clear when this machine has no sound server."""

from audiostream.audio import AudioError, list_loopback_devices, list_output_devices
from audiostream.cli import main


def test_listing_devices_works_or_explains_why_not():
    try:
        outputs = list_output_devices()
        loops = list_loopback_devices()
    except AudioError as exc:
        text = str(exc)
        assert text
        assert any(word in text for word in ("Pulse", "PipeWire", "WASAPI", "macOS", "audio"))
        return
    assert isinstance(outputs, list)
    assert isinstance(loops, list)


def test_cli_list_devices_does_not_traceback(capsys):
    code = main(["receiver", "--list-devices"])
    captured = capsys.readouterr()
    if code == 0:
        assert "Output" in captured.out or "[" in captured.out
        return
    assert code == 1
    assert captured.err.startswith("error:")
    assert "Traceback" not in captured.err
