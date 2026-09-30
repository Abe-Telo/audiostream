import sys

from audiostream.listen import Listener


def test_listener_on_this_machine_does_not_hang():
    listener = Listener()
    listener.start(None)
    thread = listener._thread
    assert thread is not None
    thread.join(2.0)
    if sys.platform != "win32":
        with listener.stats.lock:
            assert "Windows" in listener.stats.error
    listener.stop()
