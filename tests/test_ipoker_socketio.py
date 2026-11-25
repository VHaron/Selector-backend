import json

from selector_backend.ipoker_socketio import decode_socketio_frame, extract_tournaments


def _frame(item):
    payload = json.dumps({"tournamentsList": [item]})
    return "42" + json.dumps(["message", payload])


def test_socketio_lobby_frame_decodes():
    item = {"containerId": 123, "tournamentGrid": {"name": "Example"}}
    decoded = decode_socketio_frame(_frame(item))
    assert decoded["tournamentsList"][0]["containerId"] == 123


def test_unrelated_socketio_frames_are_ignored():
    assert decode_socketio_frame("2") is None
    assert decode_socketio_frame('42["other",{}]') is None


def test_duplicate_container_id_latest_observation_wins():
    old = {"containerId": 123, "tournamentGrid": {"name": "Old"}}
    new = {"containerId": 123, "tournamentGrid": {"name": "New"}}
    records, report = extract_tournaments([_frame(old), _frame(new)])
    assert len(records) == 1
    assert records[0]["tournamentGrid"]["name"] == "New"
    assert report["tournament_occurrences"] == 2
