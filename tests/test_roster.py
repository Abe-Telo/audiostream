import pytest

from audiostream.roster import Roster


def test_add_several_computers_and_keep_them(tmp_path):
    path = tmp_path / "devices.json"
    roster = Roster(path)
    roster.upsert("192.168.1.198", 45123, "Living room", "manual")
    roster.upsert("192.168.1.50", 45123, "Kitchen", "manual")
    roster.upsert("192.168.1.198", 45123, "Living room", "manual")
    assert len(roster.snapshot()) == 2

    again = Roster(path)
    again.load()
    names = [row["name"] for row in again.snapshot()]
    assert names == ["Kitchen", "Living room"]


def test_manual_name_is_not_replaced_by_the_network():
    roster = Roster(None)
    roster.upsert("192.168.1.198", 45123, "Living room", "manual")
    roster.upsert("192.168.1.198", 45123, "other-name", "network")
    row = roster.snapshot()[0]
    assert row["name"] == "Living room"
    assert row["source"] == "manual"


def test_remove_computer():
    roster = Roster(None)
    roster.upsert("192.168.1.198", 45123, "Living room", "manual")
    roster.remove("192.168.1.198", 45123)
    assert roster.snapshot() == []


def test_bad_address_is_rejected():
    roster = Roster(None)
    with pytest.raises(ValueError):
        roster.upsert("", 45123, "x", "manual")
    with pytest.raises(ValueError):
        roster.upsert("255.255.255.255", 45123, "x", "manual")
    with pytest.raises(Exception):
        roster.upsert("192.168.1.198", 0, "x", "manual")
