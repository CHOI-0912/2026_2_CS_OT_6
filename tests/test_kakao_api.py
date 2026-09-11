from __future__ import annotations

import json
from unittest.mock import patch

from ambulance_sim.kakao_api import KakaoApiClient


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_geocode_uses_rest_api_key_and_returns_coordinates() -> None:
    response = _Response({"documents": [{"address_name": "경기도 수원시", "x": "127.1", "y": "37.2"}]})
    with patch("ambulance_sim.kakao_api.urlopen", return_value=response) as mocked:
        result = KakaoApiClient("secret").geocode_address("경기도 수원시")
    request = mocked.call_args.args[0]
    assert request.headers["Authorization"] == "KakaoAK secret"
    assert "dapi.kakao.com/v2/local/search/address.json" in request.full_url
    assert result["longitude"] == 127.1
    assert result["latitude"] == 37.2


def test_driving_time_converts_seconds_to_minutes() -> None:
    response = _Response({"routes": [{"result_code": 0, "summary": {"duration": 750, "distance": 9200}}]})
    with patch("ambulance_sim.kakao_api.urlopen", return_value=response) as mocked:
        result = KakaoApiClient("secret").driving_time(127.0, 37.0, 127.2, 37.2)
    assert "apis-navi.kakaomobility.com/v1/directions" in mocked.call_args.args[0].full_url
    assert result["duration_minutes"] == 12.5
    assert result["distance_meters"] == 9200


def test_keyword_search_returns_place_identity_and_coordinates() -> None:
    response = _Response({"documents": [{
        "id": "123", "place_name": "원천119안전센터", "road_address_name": "경기도 수원시 영통구 중부대로 349",
        "address_name": "", "x": "127.05", "y": "37.27",
    }]})
    with patch("ambulance_sim.kakao_api.urlopen", return_value=response):
        result = KakaoApiClient("secret").search_keyword("원천119안전센터")
    assert result["place_id"] == "123"
    assert result["longitude"] == 127.05
