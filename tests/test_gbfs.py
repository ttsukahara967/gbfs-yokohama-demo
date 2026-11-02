from app.gbfs import BBox, Operator, classify, discovery_feed_urls, localized, merge_stations

OP = Operator("test", "テスト", "http://example.invalid/gbfs.json", "test", "#000")
BBOX = BBox.parse("139.47,35.31,139.72,35.60")


def test_discovery_v2_and_v3():
    v2 = {"data": {"ja": {"feeds": [{"name": "station_status", "url": "u2"}]}}}
    v3 = {"data": {"feeds": [{"name": "station_status", "url": "u3"}]}}
    assert discovery_feed_urls(v2) == {"station_status": "u2"}
    assert discovery_feed_urls(v3) == {"station_status": "u3"}


def test_localized_names():
    assert localized("横浜駅") == "横浜駅"
    assert localized([{"text": "Yokohama", "language": "en"}, {"text": "横浜", "language": "ja"}]) == "横浜"


def test_classify():
    kw = dict(renting=True, returning=True, installed=True)
    assert classify(0, 5, **kw) == "empty"
    assert classify(5, 0, **kw) == "full"
    assert classify(2, 5, **kw) == "few"
    assert classify(5, 5, **kw) == "ok"
    assert classify(5, 5, renting=False, returning=False, installed=True) == "offline"


def test_merge_filters_bbox_and_handles_v2_v3_fields():
    info = {
        "data": {
            "stations": [
                # HELLO CYCLING style: capacity as a string in vehicle_capacity.
                {"station_id": "1", "name": "みなとみらい", "lat": 35.457, "lon": 139.632, "vehicle_capacity": "8"},
                {"station_id": "2", "name": "新宿", "lat": 35.690, "lon": 139.700, "capacity": 10},
                {"station_id": "3", "name": [{"text": "関内", "language": "ja"}], "lat": 35.444, "lon": 139.636, "capacity": 6},
            ]
        }
    }
    status = {
        "data": {
            "stations": [
                {"station_id": "1", "num_bikes_available": 3, "num_docks_available": 5},
                {"station_id": "2", "num_bikes_available": 1, "num_docks_available": 9},
                {"station_id": "3", "num_vehicles_available": 0, "num_docks_available": 6},
            ]
        }
    }
    result = {s["id"]: s for s in merge_stations(OP, info, status, BBOX)}
    assert set(result) == {"test:1", "test:3"}
    assert result["test:1"]["capacity"] == 8
    assert result["test:1"]["status"] == "ok"
    assert result["test:3"]["name"] == "関内"
    assert result["test:3"]["status"] == "empty"
