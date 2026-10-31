# nearclinic のテスト。
# ネットワークアクセスはすべてモックに置き換え、実ネットワークには接続しない。

import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as app_module

NOMINATIM_JSON = [
    {
        "lat": "35.655833",
        "lon": "139.338889",
        "display_name": "八王子駅, 八王子市, 東京都, 日本",
    }
]

OVERPASS_JSON = {
    "elements": [
        {
            "type": "node",
            "id": 1,
            "lat": 35.656833,
            "lon": 139.338889,
            "tags": {
                "amenity": "clinic",
                "name": "テストクリニック",
                "addr:full": "東京都八王子市旭町1-1",
                "opening_hours": "Mo-Fr 09:00-18:00",
                "phone": "042-000-0000",
            },
        },
        {
            "type": "way",
            "id": 2,
            "center": {"lat": 35.665833, "lon": 139.338889},
            "tags": {
                "amenity": "hospital",
                "name": "サンプル病院",
                "addr:province": "東京都",
                "addr:city": "八王子市",
                "addr:quarter": "明神町",
                "addr:housenumber": "2-2",
            },
        },
        {
            "type": "node",
            "id": 3,
            "lat": 35.657833,
            "lon": 139.338889,
            "tags": {"amenity": "doctors"},
        },
        {
            "type": "node",
            "id": 4,
            "lat": 35.656833,
            "lon": 139.338889,
            "tags": {"amenity": "pharmacy", "name": "対象外の薬局"},
        },
    ]
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def fake_urlopen_factory(nominatim=NOMINATIM_JSON, overpass=OVERPASS_JSON):
    """URL を見て Nominatim / Overpass それぞれの応答を返すフェイク。"""

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "nominatim" in url:
            payload = nominatim
        else:
            payload = overpass
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    return fake_urlopen


class TestUtils(unittest.TestCase):
    def test_haversine_same_point(self):
        self.assertAlmostEqual(app_module.haversine_m(35.0, 139.0, 35.0, 139.0), 0.0)

    def test_haversine_one_degree_latitude(self):
        d = app_module.haversine_m(35.0, 139.0, 36.0, 139.0)
        self.assertTrue(110000 < d < 112500)

    def test_format_distance(self):
        self.assertEqual(app_module.format_distance(500), "約500m")
        self.assertEqual(app_module.format_distance(1500), "約1.5km")

    def test_build_address_full(self):
        self.assertEqual(
            app_module.build_address({"addr:full": "東京都八王子市旭町1-1"}),
            "東京都八王子市旭町1-1",
        )

    def test_build_address_parts(self):
        tags = {
            "addr:province": "東京都",
            "addr:city": "八王子市",
            "addr:quarter": "明神町",
            "addr:housenumber": "2-2",
        }
        self.assertEqual(app_module.build_address(tags), "東京都八王子市明神町2-2")

    def test_build_address_empty(self):
        self.assertEqual(app_module.build_address({}), "")


class TestParsePlaces(unittest.TestCase):
    def test_parse_and_sort(self):
        places = app_module.parse_places(OVERPASS_JSON, 35.655833, 139.338889)
        # 薬局（amenity=pharmacy）は対象外なので3件
        self.assertEqual(len(places), 3)
        # 距離順：クリニック（約111m）→ 診療所（約222m）→ 病院（約1.1km）
        self.assertEqual(places[0]["name"], "テストクリニック")
        self.assertEqual(places[0]["kind"], "クリニック")
        self.assertEqual(places[0]["opening_hours"], "Mo-Fr 09:00-18:00")
        self.assertEqual(places[0]["phone"], "042-000-0000")
        self.assertEqual(places[1]["kind"], "診療所")
        self.assertEqual(places[1]["name"], "診療所（名称未登録）")
        self.assertEqual(places[2]["name"], "サンプル病院")
        self.assertEqual(places[2]["address"], "東京都八王子市明神町2-2")
        dists = [p["distance_m"] for p in places]
        self.assertEqual(dists, sorted(dists))

    def test_parse_empty(self):
        self.assertEqual(app_module.parse_places({"elements": []}, 35.0, 139.0), [])

    def test_overpass_query_includes_radius_and_amenities(self):
        q = app_module.build_overpass_query(35.6, 139.3, 3000)
        self.assertIn("around:3000,35.6,139.3", q)
        self.assertIn("hospital", q)
        self.assertIn("clinic", q)
        self.assertIn("doctors", q)


class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_healthz(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "ok"})

    def test_top_without_query_does_not_search(self):
        with mock.patch.object(app_module.urllib.request, "urlopen") as m:
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        m.assert_not_called()
        self.assertIn("nearclinic", resp.get_data(as_text=True))

    def test_search_flow(self):
        with mock.patch.object(
            app_module.urllib.request, "urlopen", side_effect=fake_urlopen_factory()
        ):
            resp = self.client.get("/?q=八王子駅&radius=3")
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("テストクリニック", body)
        self.assertIn("サンプル病院", body)
        self.assertIn("Mo-Fr 09:00-18:00", body)
        self.assertIn("042-000-0000", body)
        self.assertIn("3件", body)
        self.assertIn("119", body)  # 注意書き

    def test_search_no_facilities(self):
        factory = fake_urlopen_factory(overpass={"elements": []})
        with mock.patch.object(app_module.urllib.request, "urlopen", side_effect=factory):
            resp = self.client.get("/?q=八王子駅&radius=1")
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("見つかりませんでした", body)

    def test_geocode_not_found(self):
        factory = fake_urlopen_factory(nominatim=[])
        with mock.patch.object(app_module.urllib.request, "urlopen", side_effect=factory):
            resp = self.client.get("/?q=存在しない場所xyz")
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)  # 500 にしない
        self.assertIn("見つかりませんでした", body)

    def test_api_failure_shows_friendly_error(self):
        with mock.patch.object(
            app_module.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("boom"),
        ):
            resp = self.client.get("/?q=八王子駅")
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)  # 500 にしない
        self.assertIn("接続できませんでした", body)

    def test_overpass_fallback_to_second_endpoint(self):
        """最初の Overpass が失敗しても、次のエンドポイントで検索できる。"""
        base = fake_urlopen_factory()

        def flaky(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "openstreetmap.fr" in url:
                raise urllib.error.URLError("primary down")
            return base(req, timeout=timeout)

        with mock.patch.object(app_module.urllib.request, "urlopen", side_effect=flaky):
            resp = self.client.get("/?q=八王子駅&radius=2")
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("テストクリニック", body)
        self.assertIn("3件", body)

    def test_invalid_radius_falls_back(self):
        with mock.patch.object(
            app_module.urllib.request, "urlopen", side_effect=fake_urlopen_factory()
        ):
            resp = self.client.get("/?q=八王子駅&radius=99")
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("半径2km", body)  # 既定の2kmにフォールバック
        self.assertIn("3件", body)


if __name__ == "__main__":
    unittest.main()
