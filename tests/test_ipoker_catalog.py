from selector_backend.ipoker_catalog import normalize_tournament


def test_native_championpoker_tournament_shape():
    raw = {
        "containerId": 123,
        "tournamentGrid": {
            "name": "€12.5 CAIPIRINHA CLASH",
            "cur": "EUR",
            "buyIn": 5.68,
            "fee": 1.12,
            "buyInFeeJackpotFee": 6.8,
            "startDate": 1795651201000,
            "knockOutFee": 5.7,
            "knockOutProgressionFactor": 0.5,
            "gameType": 1,
            "status": 1,
            "guaranteedPrize": 1000,
            "tableSize": 8,
            "speedMode": False,
        },
    }
    item = normalize_tournament(raw)
    assert item["id"] == "ipoker_123"
    assert item["buyin"] == 5.68
    assert item["fee"] == 1.12
    assert item["total_buyin"] == 6.8
    assert item["currency"] == "EUR"
    assert item["is_knockout"] is True
    assert item["is_pko"] is True  # Positive progression factor identifies PKO.
    assert item["time_start"].startswith("2026-11-")
    assert item["raw"] == raw


def test_incremental_batches_merge_instead_of_replacing_catalog(tmp_path, monkeypatch):
    from selector_backend import ipoker_catalog

    monkeypatch.setattr(ipoker_catalog, "CATALOG_FILE", str(tmp_path / "catalog.json"))
    first = {"containerId": 1, "tournamentGrid": {"name": "First", "startDate": 1795651201000}}
    second = {"containerId": 2, "tournamentGrid": {"name": "Second", "startDate": 1795651301000}}
    ipoker_catalog._merge_tournaments([first], None, "test")
    result = ipoker_catalog._merge_tournaments([second], None, "test")
    stored = ipoker_catalog._read_store()

    assert result["count"] == 2
    assert {item["upstream_id"] for item in stored["tournaments"]} == {"1", "2"}


def test_incremental_batch_updates_existing_tournament(tmp_path, monkeypatch):
    from selector_backend import ipoker_catalog

    monkeypatch.setattr(ipoker_catalog, "CATALOG_FILE", str(tmp_path / "catalog.json"))
    original = {"containerId": 7, "tournamentGrid": {"name": "Old name", "startDate": 1795651201000}}
    updated = {"containerId": 7, "tournamentGrid": {"name": "Updated name", "startDate": 1795651201000}}
    ipoker_catalog._merge_tournaments([original], None, "test")
    ipoker_catalog._merge_tournaments([updated], None, "test")
    stored = ipoker_catalog._read_store()

    assert len(stored["tournaments"]) == 1
    assert stored["tournaments"][0]["name"] == "Updated name"



def test_live_40001_partial_tournament_maps_original_date_and_translation():
    from selector_backend.ipoker_catalog import normalize_tournament

    raw = {
        "containerId": 1185794881,
        "tournamentGrid": {
            "nameTranslationsList": [{"lang": "EN", "value": "€0.50 MYSTERY MINI SAT"}],
            "originalStartDate": 1795644661000,
            "knockOutFee": 0,
            "activePlayers": 8,
            "tagList": ["Guaranteed Tournaments", "Schedule"],
        },
    }
    item = normalize_tournament(raw)
    assert item["name"] == "€0.50 MYSTERY MINI SAT"
    assert item["start_date_ms"] == 1795644661000
    assert item["time_start"].startswith("2026-11-")
    assert item["promo_tags"] == ["Guaranteed Tournaments", "Schedule"]
    assert item["raw"] == raw


def test_mystery_bounty_is_not_classified_as_pko():
    raw = {
        "containerId": 321,
        "tournamentGrid": {
            "nameTranslationsList": [{"lang": "EN", "value": "Mystery Bounty"}],
            "originalStartDate": 1795644661000,
            "knockOutFee": 4.55,
            "knockOutProgressionFactor": 0,
            "mysteryBountyTournament": True,
        },
    }
    item = normalize_tournament(raw)
    assert item["is_knockout"] is True
    assert item["is_mystery_bounty"] is True
    assert item["is_pko"] is False
