"""Small Kakao Local and Kakao Mobility client used by the real-data pipeline."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class KakaoApiError(RuntimeError):
    pass


class KakaoApiClient:
    def __init__(self, rest_api_key: str, *, timeout_seconds: float = 30.0):
        if not rest_api_key.strip():
            raise ValueError("Kakao Developers REST API key is empty")
        self._key = rest_api_key.strip()
        self.timeout_seconds = timeout_seconds

    def _get_json(self, base_url: str, parameters: dict[str, Any]) -> dict[str, Any]:
        url = f"{base_url}?{urlencode(parameters)}"
        request = Request(url, headers={"Authorization": f"KakaoAK {self._key}", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise KakaoApiError(f"Kakao API HTTP {exc.code}: {body[:500]}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise KakaoApiError(f"Kakao API request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise KakaoApiError("Kakao API returned a non-object response")
        return payload

    def geocode_address(self, address: str) -> dict[str, Any]:
        """Return the best address match with WGS84 longitude/latitude."""
        if not address.strip():
            raise ValueError("address is empty")
        payload = self._get_json(
            "https://dapi.kakao.com/v2/local/search/address.json",
            {"query": address.strip(), "analyze_type": "similar"},
        )
        documents = payload.get("documents")
        if not isinstance(documents, list) or not documents:
            raise KakaoApiError(f"no Kakao address result for {address!r}")
        result = documents[0]
        return {
            "query": address,
            "matched_address": result.get("address_name", ""),
            "longitude": float(result["x"]),
            "latitude": float(result["y"]),
        }

    def search_keyword(self, query: str) -> dict[str, Any]:
        """Return the best Kakao Local place match."""
        if not query.strip():
            raise ValueError("query is empty")
        payload = self._get_json(
            "https://dapi.kakao.com/v2/local/search/keyword.json",
            {"query": query.strip(), "sort": "accuracy"},
        )
        documents = payload.get("documents")
        if not isinstance(documents, list) or not documents:
            raise KakaoApiError(f"no Kakao keyword result for {query!r}")
        result = documents[0]
        return {
            "query": query,
            "place_id": result.get("id", ""),
            "place_name": result.get("place_name", ""),
            "matched_address": result.get("road_address_name") or result.get("address_name", ""),
            "longitude": float(result["x"]),
            "latitude": float(result["y"]),
        }

    def region_codes(self, longitude: float, latitude: float) -> list[dict[str, Any]]:
        """Return the Kakao region documents (H = 행정동, B = 법정동) covering a WGS84 coordinate."""
        payload = self._get_json(
            "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json",
            {"x": longitude, "y": latitude},
        )
        documents = payload.get("documents")
        if not isinstance(documents, list) or not documents:
            raise KakaoApiError(f"no Kakao region code for ({longitude}, {latitude})")
        return documents

    def driving_time(
        self,
        origin_longitude: float,
        origin_latitude: float,
        destination_longitude: float,
        destination_latitude: float,
        *,
        priority: str = "RECOMMEND",
    ) -> dict[str, Any]:
        """Return Kakao Mobility's current directional car-route summary."""
        payload = self._get_json(
            "https://apis-navi.kakaomobility.com/v1/directions",
            {
                "origin": f"{origin_longitude},{origin_latitude}",
                "destination": f"{destination_longitude},{destination_latitude}",
                "priority": priority,
                "summary": "true",
            },
        )
        routes = payload.get("routes")
        if not isinstance(routes, list) or not routes:
            raise KakaoApiError("Kakao Mobility returned no route")
        route = routes[0]
        if route.get("result_code") not in (None, 0):
            raise KakaoApiError(f"Kakao Mobility route failed: {route.get('result_msg', route.get('result_code'))}")
        summary = route.get("summary")
        if not isinstance(summary, dict) or "duration" not in summary or "distance" not in summary:
            raise KakaoApiError("Kakao Mobility response has no duration/distance summary")
        return {
            "duration_seconds": int(summary["duration"]),
            "duration_minutes": float(summary["duration"]) / 60.0,
            "distance_meters": int(summary["distance"]),
            "priority": priority,
        }


__all__ = ["KakaoApiClient", "KakaoApiError"]
