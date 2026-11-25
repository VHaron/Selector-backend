from selector_backend.ipoker_login import _client_hints, _find_temp_token


def test_extracts_nested_championpoker_session_token():
    payload = {
        "errorCode": 0,
        "username": "test-player",
        "sessionToken": {
            "sessionToken": "ephemeral-session-token",
            "issuerSystemId": "5",
        },
    }
    assert _find_temp_token(payload) == "ephemeral-session-token"


def test_extracts_legacy_temp_token():
    assert _find_temp_token({"tempToken": "legacy-token"}) == "legacy-token"


def test_client_hints_is_json_object():
    import json

    hints = json.loads(_client_hints())
    assert hints["Sec-CH-UA-Platform"] == '"Android"'
    assert '"Google Chrome";v="157.0.8103.79"' in hints["Sec-CH-UA-Full-Version-List"]
