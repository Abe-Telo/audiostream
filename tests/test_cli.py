"""CLI help and device selection that does not need a sound card."""

import pytest

from audiostream.audio import DeviceInfo, select_device
from audiostream.cli import main


def _device(index: int, name: str) -> DeviceInfo:
    return DeviceInfo(index=index, name=name, device_id=name, channels=2, kind="output")


def test_help_exits_cleanly():
    for argv in (["--help"], ["sender", "--help"], ["receiver", "--help"], ["pc1", "--help"]):
        with pytest.raises(SystemExit) as caught:
            main(argv)
        assert caught.value.code == 0


def test_sender_requires_a_destination(capsys):
    assert main(["sender"]) == 1
    assert "host" in capsys.readouterr().err.lower()


def test_invalid_chunk_is_rejected(capsys):
    assert main(["sender", "--host", "127.0.0.1", "--chunk-ms", "0"]) == 1
    assert "chunk" in capsys.readouterr().err.lower()


def test_select_device_by_index_and_name():
    devices = [_device(1, "Speakers"), _device(2, "Headphones")]
    assert select_device(devices, "1", interactive=False).name == "Speakers"
    assert select_device(devices, "2", interactive=False).name == "Headphones"
    assert select_device(devices, "head", interactive=False).name == "Headphones"


def test_select_device_prompts_when_interactive():
    devices = [_device(1, "Speakers"), _device(2, "Headphones")]
    chosen = select_device(devices, None, interactive=True, input_fn=lambda _prompt: "1")
    assert chosen.name == "Speakers"


def test_select_device_errors_are_explicit():
    devices = [_device(1, "Speakers"), _device(2, "Headphones")]
    with pytest.raises(Exception) as missing:
        select_device([], "1", interactive=False)
    assert "No" in str(missing.value)
    with pytest.raises(Exception) as out_of_range:
        select_device(devices, "3", interactive=False)
    assert "not in the list" in str(out_of_range.value)
    with pytest.raises(Exception) as zero:
        select_device(devices, "0", interactive=False)
    assert "start at 1" in str(zero.value)
    with pytest.raises(Exception) as ambiguous:
        select_device(
            [_device(1, "Room Speaker"), _device(2, "Desk Speaker")],
            "speaker",
            interactive=False,
        )
    assert "more than one" in str(ambiguous.value)
    with pytest.raises(Exception) as noninteractive:
        select_device(devices, None, interactive=False)
    assert "--device" in str(noninteractive.value)


def test_pcm_silence_and_full_scale():
    import numpy as np

    from audiostream.audio import float_to_s16le, s16le_to_float

    silence = float_to_s16le(np.zeros((4, 2), dtype=np.float32))
    assert silence == b"\x00" * 16
    full = float_to_s16le(np.ones((1, 1), dtype=np.float32))
    assert full == (32767).to_bytes(2, "little", signed=True)
    restored = s16le_to_float(silence, 2)
    assert restored.shape == (4, 2)
    assert np.all(restored == 0)
