import importlib
import sys
import types
import unittest
from unittest.mock import patch


if "pydantic_settings" not in sys.modules:
    fake_module = types.ModuleType("pydantic_settings")

    class BaseSettings:  # pragma: no cover - minimal import stub for tests
        pass

    fake_module.BaseSettings = BaseSettings
    sys.modules["pydantic_settings"] = fake_module


route_utils = importlib.import_module("app.services.route_utils")


class RouteUtilsResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        route_utils._trackasia_textsearch_results_cached.cache_clear()
        route_utils._trackasia_reverse_hits_cached.cache_clear()
        route_utils._trackasia_geocode_cached.cache_clear()

    def test_address_top_fallback_prefers_specific_map_hit(self) -> None:
        place = {
            "name": "Công viên Châu Á",
            "address": "01 Phan Đăng Lưu, Hòa Cường, Quận Hải Châu, Thành phố Đà Nẵng",
            "city": "Đà Nẵng",
            "district": "Hải Châu",
        }

        def fake_textsearch(query: str, limit: int) -> tuple[dict, ...]:
            del limit
            if query.startswith("01 Phan Đăng Lưu"):
                return (
                    {
                        "name": "Helio Center",
                        "address": "Helio Center, 01 Đường 2 Tháng 9, Hòa Cường, Quận Hải Châu, Thành phố Đà Nẵng",
                        "lat": 16.041234,
                        "lon": 108.224567,
                        "source": "trackasia_textsearch",
                    },
                )
            return ()

        with patch.object(route_utils, "_trackasia_textsearch_results_cached", side_effect=fake_textsearch):
            with patch.object(route_utils, "_trackasia_geocode_cached", return_value=None):
                with patch.object(route_utils, "_nominatim_map_resolve", return_value=None):
                    resolved = route_utils.resolve_location_for_map(
                        place,
                        allow_approximate_fallback=False,
                    )

        self.assertIsNotNone(resolved)
        self.assertEqual("Helio Center", resolved.label)
        self.assertEqual(16.041234, resolved.lat)
        self.assertEqual(108.224567, resolved.lon)
        self.assertTrue(resolved.source.endswith(":address_top"))

    def test_place_map_url_refuses_centroid_only_fallback(self) -> None:
        place = {
            "name": "Bánh mì Phượng",
            "address": "2B Phan Chu Trinh, Quảng Nam",
            "city": "Đà Nẵng",
        }

        with patch.object(route_utils, "_trackasia_textsearch_results_cached", return_value=()):
            with patch.object(route_utils, "_trackasia_geocode_cached", return_value=None):
                with patch.object(route_utils, "_nominatim_map_resolve", return_value=None):
                    url = route_utils.place_map_url(place)

        self.assertEqual("", url)

    def test_place_map_url_accepts_specific_nearby_fallback(self) -> None:
        place = {
            "name": "Công viên suối khoáng nóng núi Thần tài",
            "address": "Thôn Phú Túc, Huyện Hòa Vang, Thành phố Đà Nẵng",
            "city": "Đà Nẵng",
            "district": "Hòa Vang",
        }
        nearby = route_utils.ResolvedMapLocation(
            lat=15.999001,
            lon=107.995001,
            label="Khu tắm khoáng Núi Thần Tài",
            address="QL14G, Hòa Phú, Hòa Vang, Thành phố Đà Nẵng, Việt Nam",
            source="trackasia_reverse_geocode:nearby_anchor",
        )

        with patch.object(route_utils, "resolve_location_for_map", side_effect=[None, nearby]):
            url = route_utils.place_map_url(place)

        self.assertIn("latlon:15.999001:107.995001", url)
        self.assertIn("Khu%20t%E1%BA%AFm%20kho%C3%A1ng%20N%C3%BAi%20Th%E1%BA%A7n%20T%C3%A0i", url)

    def test_segment_map_url_keeps_specific_address_fallback_even_without_name_overlap(self) -> None:
        origin = {
            "name": "Công viên Châu Á",
            "address": "01 Phan Đăng Lưu, Hòa Cường, Quận Hải Châu, Thành phố Đà Nẵng",
        }
        destination = {
            "name": "Citron Restaurant – Nhà hàng Đà Nẵng view đẹp",
            "address": "Bãi Bắc, Sơn Trà Peninsula, Thành phố Đà Nẵng",
        }
        origin_loc = route_utils.ResolvedMapLocation(
            lat=16.041234,
            lon=108.224567,
            label="Helio Center",
            address="Helio Center, 01 Đường 2 Tháng 9, Hòa Cường, Quận Hải Châu, Thành phố Đà Nẵng",
            source="trackasia_textsearch:address_top",
        )
        destination_loc = route_utils.ResolvedMapLocation(
            lat=16.118765,
            lon=108.242222,
            label="Citron Restaurant",
            address="Bãi Bắc, Sơn Trà Peninsula, Thành phố Đà Nẵng",
            source="trackasia_textsearch:name_match",
        )

        with patch.object(route_utils, "_best_location_for_map_link", side_effect=[origin_loc, destination_loc]):
            with patch.object(route_utils, "_trackasia_route_url", return_value="safe-route-url") as route_url_mock:
                url = route_utils.segment_map_url(origin, destination)

        self.assertEqual("safe-route-url", url)
        route_url_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
