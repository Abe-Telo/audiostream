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
        buttons = [child.cget("text") for child in _of_class(app.list_frame, "Button")]
        assert "Living room" in labels
        assert "Edit" in buttons
        assert "192.168.1.198:45123    Added by you" in labels
    finally:
        app.close()


def test_pc2_lists_computers_that_are_receiving():
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    probe.destroy()

    from audiostream.pc2_app import Pc2App

    app = Pc2App(start_audio=False, start_presence=False, roster=Roster(None))
    try:
        app.roster.upsert("192.168.1.198", 45123, "Living room", "network")
        app.refresh_devices()
        app.root.update_idletasks()
        assert app.visible_names == ["Living room"]
        buttons = [child.cget("text") for child in _of_class(app.list_frame, "Button")]
        assert "Edit" in buttons
    finally:
        app.quit()


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
    player = Pc2App(start_audio=False, start_presence=False)
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


def test_receiver_volume_and_rename_controls():
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    probe.destroy()

    from audiostream.pc1_app import Pc1App

    app = Pc1App(roster=Roster(None), start_network=False)
    try:
        assert app.receiver_button.cget("text") == "Receiver"
        assert app.roster.master_volume_value() == 100
        app.roster.upsert("192.168.1.198", 45123, "Living room", "manual")
        app.roster.upsert("192.168.1.50", 45123, "Kitchen", "manual")
        app.refresh_devices()
        app.root.update_idletasks()
        scales = _scales(app.list_frame)
        assert len(scales) == 2
        assert app.master_scale.cget("command")
        app.root.tk.call(app.master_scale.cget("command"), 40)
        assert app.roster.master_volume_value() == 40
        assert app.master_percent.cget("text") == "40%"
        app.root.tk.call(scales[0].cget("command"), 25)
        volumes = {row["name"]: row["volume"] for row in app.roster.snapshot()}
        assert volumes["Kitchen"] == 25
        assert volumes["Living room"] == 100
        app.roster.rename("192.168.1.50", 45123, "Den")
        app.refresh_devices()
        labels = [child.cget("text") for child in _labels(app.list_frame)]
        assert "Den" in labels
        app._toggle_receiver()
        assert app.receiver_button.cget("text") == "Stop receiving"
        app._toggle_receiver()
        assert app.receiver_button.cget("text") == "Receiver"
    finally:
        app.close()


def test_add_computer_shows_a_scan_list_and_the_tray_menu():
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    probe.destroy()

    from audiostream.pc1_app import Pc1App
    from audiostream.tray import TrayIcon

    app = Pc1App(roster=Roster(None), start_network=False)
    try:
        labels = []
        kinds = []
        for item in app._tray_items():
            if item is None:
                kinds.append("separator")
            elif item[0] in ("check", "scale"):
                kinds.append(item[0])
                labels.append(item[1])
            else:
                kinds.append("command")
                labels.append(item[0])
        assert "Open" in labels
        assert "Add to startup" in labels
        assert "Volume" in labels
        assert "Quit" in labels
        assert "check" in kinds
        assert "scale" in kinds
        app._add_dialog()
        app.root.update()
        dialogs = [child for child in app.root.winfo_children() if child.winfo_class() == "Toplevel"]
        assert dialogs
        assert _of_class(dialogs[0], "Listbox")
        texts = [child.cget("text") for child in _of_class(dialogs[0], "TLabel") if hasattr(child, "cget")]
        assert any("Audiostream" in text for text in texts)
        icon = TrayIcon(app.root, "Audiostream PC1", app._tray_items)
        icon._popup()
        app.root.update()
        assert icon._panel is not None
        assert _of_class(icon._panel, "Checkbutton")
        assert _of_class(icon._panel, "Scale")
        icon._panel.destroy()
        dialogs[0].destroy()
    finally:
        app.close()


def _of_class(widget, kind):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == kind:
            found.append(child)
        found.extend(_of_class(child, kind))
    return found


def _scales(widget):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Scale":
            found.append(child)
        found.extend(_scales(child))
    return found


def _labels(widget):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Label":
            found.append(child)
        found.extend(_labels(child))
    return found
