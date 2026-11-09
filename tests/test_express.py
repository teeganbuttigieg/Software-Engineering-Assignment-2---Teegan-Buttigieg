"""CR-01 checks across the service, command adapter, and interactive menu."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from supplydesk.cli import main
from supplydesk.menu import run_menu
from supplydesk.service import OrderService


class ExpressTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "state.json"
        self.service = OrderService(self.path)

    def item(self, product, quantity):
        return [{"product_id": product, "quantity": quantity}]

    def command(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--data", str(self.path), *args])
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_regular_express_example(self):
        result = self.service.quote("regular", self.item("notebook", 2), "express")
        self.assertEqual((result["subtotal_cents"], result["discount_cents"],
                          result["delivery_cents"], result["total_cents"]),
                         (1000, 0, 1200, 2200))
        self.assertFalse(self.path.exists())

    def test_student_example_quote_and_placement_match(self):
        items = self.item("hoodie", 2)
        quote = self.service.quote("student", items, "express")
        order = self.service.place_order("student", items, "express")
        self.assertEqual(quote["discount_cents"], 800)
        self.assertEqual(quote["delivery_cents"], 1200)
        self.assertEqual(quote["total_cents"], 8400)
        for key, value in quote.items():
            self.assertEqual(order[key], value)

    def test_express_never_free_and_standard_and_pickup_unchanged(self):
        for customer, product, quantity, discount, standard_total, express_total in (
            ("regular", "notebook", 20, 0, 10000, 11200),
            ("regular", "hoodie", 3, 0, 12000, 13200),
            ("student", "hoodie", 3, 1200, 10800, 12000),
        ):
            with self.subTest(customer=customer, product=product):
                items = self.item(product, quantity)
                standard = self.service.quote(customer, items, "delivery")
                express = self.service.quote(customer, items, "express")
                pickup = self.service.quote(customer, items, "pickup")
                self.assertEqual(standard["delivery_cents"], 0)
                self.assertEqual(standard["total_cents"], standard_total)
                self.assertEqual(pickup["total_cents"], standard_total)
                self.assertEqual(express["discount_cents"], discount)
                self.assertEqual(express["delivery_cents"], 1200)
                self.assertEqual(express["total_cents"], express_total)

    def test_student_half_cent_rounding_and_merchandise_only_discount(self):
        state = self.service.store.load()
        state["catalog"][2]["unit_price_cents"] = 5555
        self.service.store.save(state)
        result = self.service.quote("student", self.item("hoodie", 1), "express")
        self.assertEqual(result["discount_cents"], 556)
        self.assertEqual(result["delivery_cents"], 1200)
        self.assertEqual(result["total_cents"], 6199)

    def test_express_lifecycle_preserves_snapshot_stock_history_and_ids(self):
        before = self.service.catalog()
        order = self.service.place_order("student", self.item("hoodie", 2), "express")
        self.assertEqual(order["order_id"], "ORD-0001")
        reopened = OrderService(self.path)
        self.assertEqual(reopened.catalog()[2]["stock"], 3)
        self.assertEqual(reopened.get_order(order["order_id"]), order)
        self.assertEqual(reopened.list_orders(), [order])
        cancelled = reopened.cancel_order(order["order_id"])
        self.assertEqual(cancelled, {**order, "status": "cancelled"})
        self.assertEqual(OrderService(self.path).get_order(order["order_id"]), cancelled)
        self.assertEqual(reopened.catalog(), before)
        next_order = reopened.place_order("regular", self.item("notebook", 1))
        self.assertEqual(next_order["order_id"], "ORD-0002")
        self.assertEqual(reopened.list_orders(), [cancelled, next_order])

    def test_express_invalid_requests_preserve_saved_state(self):
        self.service.place_order("regular", self.item("notebook", 1))
        before = self.path.read_bytes()
        for customer, items, fulfillment in (
            ("regular", self.item("notebook", 1), "overnight"),
            ("invalid", self.item("notebook", 1), "express"),
            ("regular", [], "express"),
            ("regular", self.item("notebook", 0), "express"),
            ("regular", self.item("notebook", 20), "express"),
        ):
            for method in ("quote", "place_order"):
                with self.subTest(method=method, customer=customer, items=items, fulfillment=fulfillment):
                    with self.assertRaises(ValueError):
                        getattr(self.service, method)(customer, items, fulfillment)
                    self.assertEqual(self.path.read_bytes(), before)

    def test_cli_express_quote_and_place(self):
        quote = self.command("quote", "--customer", "student", "--fulfillment", "express", "--item", "hoodie:2")
        self.assertEqual(quote["total_cents"], 8400)
        order = self.command("place", "--customer", "student", "--fulfillment", "express", "--item", "hoodie:2")
        for key, value in quote.items():
            self.assertEqual(order[key], value)
        self.assertEqual(self.service.get_order(order["order_id"]), order)

    def test_cli_defaults_help_and_invalid_fulfillment(self):
        result = self.command("quote", "--item", "notebook:2")
        self.assertEqual((result["customer_type"], result["fulfillment"], result["total_cents"]),
                         ("regular", "pickup", 1000))
        for command in ("quote", "place"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
                main([command, "--help"])
            self.assertEqual(error.exception.code, 0)
            self.assertIn("express", output.getvalue())
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main([command, "--fulfillment", "overnight", "--item", "notebook:1"])
            self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.path.exists())

    def menu(self, answers):
        output = io.StringIO()
        prompts = []
        answers = iter(answers)
        def respond(prompt):
            prompts.append(prompt)
            return next(answers)
        with patch("builtins.input", side_effect=respond), contextlib.redirect_stdout(output):
            self.assertEqual(run_menu(self.service), 0)
        return output.getvalue(), prompts

    def test_menu_express_quote_and_confirmed_placement(self):
        output, prompts = self.menu(["2", "student", "hoodie", "2", "", "express",
                                     "3", "student", "hoodie", "2", "", "express", "y", "0"])
        self.assertIn("Total: $84.00", output)
        self.assertTrue(any("pickup/delivery/express" in p for p in prompts))
        orders = self.service.list_orders()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["fulfillment"], "express")
        self.assertEqual(orders[0]["total_cents"], 8400)

    def test_menu_defaults_and_declined_express_order(self):
        output, _ = self.menu(["2", "", "notebook", "2", "", "",
                               "3", "", "notebook", "2", "", "express", "n", "0"])
        self.assertIn("Customer: regular | Fulfillment: pickup", output)
        self.assertIn("Total: $10.00", output)
        self.assertIn("Total: $22.00", output)
        self.assertEqual(self.service.list_orders(), [])
        self.assertFalse(self.path.exists())
