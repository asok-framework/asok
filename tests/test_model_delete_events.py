import pytest

from asok.events import events
from asok.orm import Field, Model


class EventModel(Model):
    _db_path = ":memory:"
    __tablename__ = "event_models"
    name = Field.String()


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_events.db")
    EventModel.close_connections()
    monkeypatch.setattr(EventModel, "_db_path", db_path)
    EventModel.create_table()
    yield db_path
    EventModel.close_connections()


def test_delete_emits_events():
    # Arrange
    obj = EventModel.create(name="Test Item")
    emitted = []

    def on_deleted(instance):
        emitted.append(("model:deleted", instance))

    def on_specific_deleted(instance):
        emitted.append(("model:EventModel:deleted", instance))

    events.on("model:deleted", on_deleted)
    events.on("model:EventModel:deleted", on_specific_deleted)

    # Act
    obj.delete()

    # Assert
    assert len(emitted) == 2
    assert emitted[0] == ("model:EventModel:deleted", obj)
    assert emitted[1] == ("model:deleted", obj)

    # Cleanup listeners
    events._listeners["model:deleted"].remove(on_deleted)
    events._listeners["model:EventModel:deleted"].remove(on_specific_deleted)


def test_force_delete_emits_events():
    # Arrange
    obj = EventModel.create(name="Test Item 2")
    emitted = []

    def on_deleted(instance):
        emitted.append(("model:deleted", instance))

    def on_specific_deleted(instance):
        emitted.append(("model:EventModel:deleted", instance))

    events.on("model:deleted", on_deleted)
    events.on("model:EventModel:deleted", on_specific_deleted)

    # Act
    obj.force_delete()

    # Assert
    assert len(emitted) == 2
    assert emitted[0] == ("model:EventModel:deleted", obj)
    assert emitted[1] == ("model:deleted", obj)

    # Cleanup listeners
    events._listeners["model:deleted"].remove(on_deleted)
    events._listeners["model:EventModel:deleted"].remove(on_specific_deleted)
