from selector_backend.ipoker_worker import build_register_client_request, validate_auth_message


def sample_auth():
    return {
        "classId": 60108,
        "tempToken": "ephemeral",
        "usernameOrNickname": "player",
        "usernameLogin": True,
        "realMode": True,
        "casinoName": "championpoker",
        "clientPlatform": 26,
        "clientVersion": "26.7.1.26",
        "languageCode": "en",
        "supportedPokerServices": [],
        "deviceFamily": "android",
        "deviceName": None,
        "deviceType": "smartphone",
        "hardSerial": None,
        "softSerial": None,
        "osVersion": "Android 16",
        "platformType": 5,
        "browser": None,
        "userAgent": "test",
        "loginType": 0,
    }


def test_auth_payload_contract():
    validate_auth_message(sample_auth())


def test_auth_payload_rejects_wrong_message():
    payload = sample_auth()
    payload["classId"] = 50001
    try:
        validate_auth_message(payload)
    except ValueError:
        return
    raise AssertionError("invalid auth payload accepted")


def test_register_client_request_matches_native_client_contract():
    auth = sample_auth()
    message = build_register_client_request(auth)
    assert message["classId"] == 50001
    assert message["clientType"] == 26
    assert message["clientPlatform"] == 26
    assert message["clientVersion"] == "26.7.1.26"
    assert message["casinoName"] == "championpoker"
    assert message["casinoSkin"] == "championpoker"
    assert message["subscribeClientServices"] is True
