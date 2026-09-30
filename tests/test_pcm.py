from audiostream.pcm import fit_s16le, peak_s16le


def test_fit_s16le_keeps_stereo():
    pcm = b"\x01\x00\x02\x00"
    assert fit_s16le(pcm, 2, 2) == pcm


def test_fit_s16le_drops_extra_channels():
    # one frame, 4 channels: 1,2,3,4
    pcm = (1).to_bytes(2, "little", signed=True)
    pcm += (2).to_bytes(2, "little", signed=True)
    pcm += (3).to_bytes(2, "little", signed=True)
    pcm += (4).to_bytes(2, "little", signed=True)
    assert fit_s16le(pcm, 4, 2) == pcm[:4]


def test_peak_s16le():
    loud = (16384).to_bytes(2, "little", signed=True)
    assert peak_s16le(loud) == 0.5
    assert peak_s16le(b"") == 0.0


def test_windows_sender_does_not_import_numpy():
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import audiostream.sender\n"
        "import audiostream.win_audio\n"
        "assert 'numpy' not in sys.modules\n"
        "assert 'soundcard' not in sys.modules\n"
    )
    subprocess.check_call([sys.executable, "-c", code])


def test_windows_library_missing_is_explained():
    import sys

    from audiostream.audio import AudioError
    from audiostream.win_audio import list_loopbacks

    if sys.platform == "win32":
        return
    try:
        list_loopbacks()
    except AudioError as exc:
        assert "requirements.txt" in str(exc)
    else:
        raise AssertionError("expected a missing-library error on this machine")
