import json

from selector_backend.ipoker_socketio import (
    build_tournament_subscription,
    build_tournament_subscription_frame,
    decode_application_message,
    decode_socketio_frame,
    encode_application_message,
    extract_tournaments,
    is_tournament_catalogue_frame,
    parse_tournament_subscription_ack,
)


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


def test_subscription_matches_observed_50017_contract():
    message = build_tournament_subscription("request-1")
    assert message == {
        "subscriptionType": 1,
        "containerType": 1,
        "filter": {
            "value": "@start_type@=1",
            "condition": None,
        },
        "fun": False,
        "requestId": "request-1",
        "subContainerType": 4,
        "classId": 50017,
    }


def test_subscription_frame_round_trips():
    frame = build_tournament_subscription_frame("request-2")
    decoded = decode_application_message(frame)
    assert decoded["classId"] == 50017
    assert decoded["requestId"] == "request-2"


def test_ack_parser_accepts_success_and_rejects_other_messages():
    success = encode_application_message({
        "requestId": "request-3", "errorCode": 0,
        "classId": 50018, "messageSeqId": 4,
    })
    assert parse_tournament_subscription_ack(success) == {
        "request_id": "request-3",
        "error_code": 0,
        "accepted": True,
        "message": None,
        "message_seq_id": 4,
    }
    failure = encode_application_message({
        "requestId": "request-3", "errorCode": 7, "classId": 50018,
    })
    assert parse_tournament_subscription_ack(failure)["accepted"] is False
    assert parse_tournament_subscription_ack("2") is None


def test_catalogue_frame_classifier():
    frame = encode_application_message({"classId": 40001, "tournamentsList": []})
    assert is_tournament_catalogue_frame(frame)
    assert not is_tournament_catalogue_frame(encode_application_message({"classId": 50018}))


def test_browser_tournament_push_40001_echoes_subscription_request():
    item = {
        "containerId": 9876,
        "tournamentGrid": {
            "nameTranslationsList": [{"lang": "EN", "value": "Example Mystery Bounty"}],
            "originalStartDate": 1795644661000,
        },
    }
    frame = encode_application_message({
        "classId": 40001,
        "requestId": "Lobby_FilterSubscription_7",
        "messageSeqId": 94,
        "tournamentsList": [item],
    })
    records, report = extract_tournaments([frame])
    assert report["messages_decoded"] == 1
    assert records == [item]


def test_subscription_default_request_id_matches_browser_capture():
    message = build_tournament_subscription()
    assert message["requestId"] == "Lobby_FilterSubscription_7"
    assert message["classId"] == 50017
