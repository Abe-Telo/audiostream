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


def _labels(widget):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Label":
            found.append(child)
        found.extend(_labels(child))
    return found
