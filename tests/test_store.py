"""Store unit tests: real SQLite, temp file."""
import os
import tempfile

import pytest

from steward.store import Store


@pytest.fixture()
def store():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    s = Store(tmp.name)
    yield s
    s.close()
    os.unlink(tmp.name)


def test_calendar_roundtrip(store):
    ev = store.calendar_add("Dentist", "2026-10-10", "10:00", "bring card")
    assert ev["id"] > 0
    day = store.calendar_list("2026-10-10")
    assert len(day) == 1 and day[0]["title"] == "Dentist"
    assert store.calendar_delete(ev["id"]) is True
    assert store.calendar_list("2026-10-10") == []
    assert store.calendar_delete(999999) is False


def test_reminder_lifecycle(store):
    r = store.reminder_add("Call mom", "Saturday")
    assert r["done"] == 0
    open_rs = store.reminder_list()
    assert any(x["id"] == r["id"] for x in open_rs)
    assert store.reminder_done(r["id"]) is True
    assert all(x["id"] != r["id"] for x in store.reminder_list())
    assert any(x["id"] == r["id"]
               for x in store.reminder_list(include_done=True))


def test_notes_search(store):
    store.note_save("Groceries", "eggs, milk, sourdough bread", "weekly")
    store.note_save("Ideas", "build a treehouse", "fun")
    hits = store.note_search("sourdough")
    assert len(hits) == 1 and hits[0]["title"] == "Groceries"
    assert "sourdough" in hits[0]["snippet"]
    assert store.note_search("no-such-thing-xyz") == []
    full = store.note_get(hits[0]["id"])
    assert full["body"] == "eggs, milk, sourdough bread"


def test_prefs_memory(store):
    assert store.prefs_get("appointment_time")["value"] == ""
    store.prefs_set("appointment_time", "mornings")
    assert store.prefs_get("appointment_time")["value"] == "mornings"
    store.prefs_set("appointment_time", "afternoons")
    assert store.prefs_get("appointment_time")["value"] == "afternoons"
    keys = {p["key"] for p in store.prefs_all()}
    assert "appointment_time" in keys
