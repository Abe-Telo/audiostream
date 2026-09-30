from audiostream.startup import launch_command, set_startup, startup_enabled


def test_launch_command_starts_the_pc1_window():
    command = launch_command()
    assert "audiostream" in command
    assert "pc1" in command


def test_startup_checkbox_is_off_when_windows_is_not_running():
    import sys

    if sys.platform == "win32":
        return
    assert startup_enabled() is False
    set_startup(True)
    assert startup_enabled() is False
