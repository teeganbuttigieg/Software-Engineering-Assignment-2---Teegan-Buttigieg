"""Pre-refactoring checks based on FUNCTIONAL_SPEC.md; known defects stay failing."""

import tempfile
import unittest
from pathlib import Path

from supplydesk.service import OrderService


class SafetyNetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.json"
        self.service = OrderService(self.path)

    def items(self, quantity=1, product="notebook"):
        return [{"product_id": product, "quantity": quantity}]

    # Normal behavior
    def test_valid_delivery_order_updates_stock_and_history(self):
        order = self.service.place_order("regular", self.items(2), "delivery")
        self.assertEqual(order["order_id"], "ORD-0001")
        self.assertEqual(order["status"], "placed")
        self.assertEqual(order["subtotal_cents"], 1000)
        self.assertEqual(order["delivery_cents"], 600)
        self.assertEqual(order["total_cents"], 1600)
        self.assertEqual(self.service.catalog()[0]["stock"], 18)
        self.assertEqual(self.service.list_orders(), [order])

    # Boundaries: exercise quoting and placement independently.
    def test_student_discount_starts_at_exactly_5000_cents(self):
        for operation in ("quote", "place_order"):
            with self.subTest(operation=operation):
                result = getattr(self.service, operation)("student", self.items(10))
                self.assertEqual(result["subtotal_cents"], 5000)
                self.assertEqual(result["discount_cents"], 500)
                self.assertEqual(result["total_cents"], 4500)




    # Invalid inputs: preserve previously saved state, including the next ID.
    def test_invalid_requests_raise_and_leave_saved_state_unchanged(self):
        self.service.place_order("regular", self.items())
        before = self.path.read_bytes()
        cases = [
            ("regular", [] , "pickup"),
            ("regular", self.items(0), "pickup"),
            ("regular", self.items(100), "pickup"),
            ("regular", self.items(True), "pickup"),
            ("regular", self.items("2"), "pickup"),
            ("regular", self.items(1.5), "pickup"),
            ("regular", self.items(1, "missing"), "pickup"),
            ("visitor", self.items(), "pickup"),
            ("regular", self.items(), "overnight"),
            ("regular", self.items(20), "pickup"),
        ]
        for operation in ("quote", "place_order"):
            for request in cases:
                with self.subTest(operation=operation, request=request):
                    with self.assertRaises(ValueError):
                        getattr(self.service, operation)(*request)
                    self.assertEqual(self.path.read_bytes(), before)



    # Regression protection for a future extraction of shared pricing logic.
    def test_quote_does_not_save_or_reserve_and_matches_placement(self):
        request = self.items(2, "hoodie")
        quote = self.service.quote("student", request, "delivery")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.service.catalog()[2]["stock"], 5)
        order = self.service.place_order("student", request, "delivery")
        for key, value in quote.items():
            self.assertEqual(order[key], value, key)
        before = self.path.read_bytes()
        self.service.quote("regular", self.items())
        self.assertEqual(self.path.read_bytes(), before)




if __name__ == "__main__":
    unittest.main()
