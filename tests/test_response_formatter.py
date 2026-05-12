import unittest

from app.services import response_formatter


class ResponseFormatterStayTests(unittest.TestCase):
    def test_planned_hotel_is_shown_before_alternates(self) -> None:
        lines = response_formatter._format_stay_lines(
            recommended_hotel={
                "name": "Khách sạn Minh Toàn SAFI Ocean",
                "address": "224 Võ Nguyên Giáp, Đà Nẵng",
            },
            stay_plan=None,
            stay_recommendations=[
                {
                    "segment": "Bình dân",
                    "name": "Khách sạn Hoàng Đại II",
                    "price_note": "250.000 - 2.500.000",
                    "address": "97 Võ Văn Kiệt, Đà Nẵng",
                    "why_fit": "gần biển",
                },
                {
                    "segment": "Cao cấp",
                    "name": "Khách sạn Luxury",
                    "price_note": "700.000 - 1.400.000",
                    "address": "205 Trần Phú, Đà Nẵng",
                    "why_fit": "trung tâm",
                },
            ],
            fallback_lines=[],
        )

        self.assertGreaterEqual(len(lines), 3)
        self.assertEqual("• Khách sạn dùng trong lịch trình: Khách sạn Minh Toàn SAFI Ocean", lines[0])
        self.assertIn("• Tham khảo thêm - Bình dân: Khách sạn Hoàng Đại II", lines)
        self.assertIn("• Tham khảo thêm - Cao cấp: Khách sạn Luxury", lines)

    def test_duplicate_alternate_stay_is_filtered_out(self) -> None:
        lines = response_formatter._format_stay_lines(
            recommended_hotel={
                "name": "Khách sạn Luxury",
                "address": "205 Trần Phú, Đà Nẵng",
            },
            stay_plan=None,
            stay_recommendations=[
                {
                    "segment": "Cao cấp",
                    "name": "Khách sạn Luxury",
                    "price_note": "700.000 - 1.400.000",
                    "address": "205 Trần Phú, Đà Nẵng",
                    "why_fit": "trung tâm",
                },
                {
                    "segment": "Bình dân",
                    "name": "Khách sạn Hoàng Đại II",
                    "price_note": "250.000 - 2.500.000",
                    "address": "97 Võ Văn Kiệt, Đà Nẵng",
                    "why_fit": "gần biển",
                },
            ],
            fallback_lines=[],
        )

        self.assertIn("• Khách sạn dùng trong lịch trình: Khách sạn Luxury", lines)
        self.assertEqual(0, sum(1 for line in lines if "• Tham khảo thêm - Cao cấp: Khách sạn Luxury" == line))
        self.assertIn("• Tham khảo thêm - Bình dân: Khách sạn Hoàng Đại II", lines)


if __name__ == "__main__":
    unittest.main()
