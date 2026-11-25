from selector_backend.ipoker_worker import build_lobby_init, validate_auth_message

def sample_auth():
    return {"classId":60108,"tempToken":"ephemeral","usernameOrNickname":"player","usernameLogin":True,"loginType":1,"realMode":True,"casinoName":"championpoker","clientPlatform":26,"clientVersion":"26.7.1.26","deviceFamily":"desktop","deviceName":"browser","deviceType":"desktop","hardSerial":"x","softSerial":"y","languageCode":"EN","osVersion":"unknown","platformType":1,"supportedPokerServices":[],"browser":"Chrome","userAgent":"test"}

def test_auth_payload_contract():
    validate_auth_message(sample_auth())

def test_auth_payload_rejects_wrong_message():
    payload=sample_auth(); payload["classId"]=50001
    try: validate_auth_message(payload)
    except ValueError: return
    raise AssertionError("invalid auth payload accepted")

def test_lobby_init_uses_fresh_session_id():
    msg=build_lobby_init("runtime-session")
    assert msg["classId"]==50001 and msg["sessionId"]=="runtime-session"
