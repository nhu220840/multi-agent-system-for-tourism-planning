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


if __name__ == "__main__":
    unittest.main()
