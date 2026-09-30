import pytest

from audiostream.roster import Roster


def test_pc1_window_lists_a_computer():
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    probe.destroy()

    from audiostream.pc1_app import Pc1App

    app = Pc1App(roster=Roster(None), start_network=False)
    try:
        app.roster.upsert("192.168.1.198", 45123, "Living room", "manual")
        app.roster.upsert("192.168.1.50", 45123, "Kitchen", "manual")
        app.refresh_devices()
        app.root.update_idletasks()
        assert app.visible_names == ["Kitchen", "Living room"]
        labels = [child.cget("text") for child in _labels(app.list_frame)]
        assert "Living room" in labels
        assert "192.168.1.198:45123    Added by you" in labels
    finally:
        app.close()


def test_closing_the_window_hides_to_the_tray():
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    probe.destroy()

    from audiostream.pc1_app import Pc1App
    from audiostream.pc2_app import Pc2App

    class DummyTray:
        installed = True

        def __init__(self):
            self.messages = []

        def balloon(self, title, message):
            self.messages.append((title, message))

        def remove(self):
            self.installed = False

        def set_tooltip(self, text):
            self.tip = text

    sender = Pc1App(roster=Roster(None), start_network=False)
    player = Pc2App(start_audio=False)
    try:
        sender._tray = DummyTray()
        player._tray = DummyTray()
        sender.on_close_button()
        player.on_close_button()
        assert sender.root.state() == "withdrawn"
        assert player.root.state() == "withdrawn"
        assert sender.root.winfo_exists()
        assert player.root.winfo_exists()
        assert "tray" in sender._tray.messages[0][1].lower()
        assert "tray" in player._tray.messages[0][1].lower()
        sender.show()
        assert sender.root.state() == "normal"
    finally:
        sender.close()
        player.quit()


def _labels(widget):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Label":
            found.append(child)
        found.extend(_labels(child))
    return found
