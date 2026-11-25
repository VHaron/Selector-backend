from selector_backend.ipoker_catalog import normalize_tournament


def test_native_championpoker_tournament_shape():
    raw = {
        "containerId": 123,
        "tournamentGrid": {
            "name": "€12.5 CAIPIRINHA CLASH",
            "cur": "EUR",
            "buyIn": 5.68,
            "fee": 1.12,
            "startDate": 1795651201000,
            "knockOutFee": 5.7,
            "gameType": 1,
            "status": 1,
            "guaranteedPrize": 1000,
            "tableSize": 8,
        },
    }
    item = normalize_tournament(raw)
    assert item["id"] == "ipoker_123"
    assert item["buyin"] == 5.68
    assert item["fee"] == 1.12
    assert item["total_buyin"] == 6.8
    assert item["currency"] == "EUR"
    assert item["is_pko"] is True
    assert item["time_start"].startswith("2026-11-")
    assert item["raw"] == raw
