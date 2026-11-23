import unittest
from uuid import uuid4
from common.domain import DomainError, uuid_text, pagination
from orders.service import calculate_total, DISH_ID


class CalculationTests(unittest.TestCase):
    def test_total_uses_catalog_price(self):
        total, items = calculate_total([{'dish_id': DISH_ID, 'quantity': 2}], {DISH_ID: 60000})
        self.assertEqual(total, 120000)
        self.assertEqual(items[0]['unit_price'], 60000)

    def test_client_cannot_override_price(self):
        total, _ = calculate_total([{'dish_id': DISH_ID, 'quantity': 2, 'unit_price': 1}], {DISH_ID: 60000})
        self.assertEqual(total, 120000)

    def test_quantity_boundaries(self):
        for quantity in [1, 100]:
            with self.subTest(quantity=quantity):
                total, _ = calculate_total([{'dish_id': DISH_ID, 'quantity': quantity}], {DISH_ID: 60000})
                self.assertEqual(total, quantity*60000)

    def test_invalid_quantities(self):
        for quantity in [0, -1, 1.5, True, '2', 101, None]:
            with self.subTest(quantity=quantity), self.assertRaises(DomainError) as error:
                calculate_total([{'dish_id': DISH_ID, 'quantity': quantity}], {DISH_ID: 60000})
            self.assertEqual(error.exception.code, 'invalid_quantity')

    def test_empty_items(self):
        with self.assertRaises(DomainError):
            calculate_total([], {DISH_ID: 60000})

    def test_items_must_be_list(self):
        for items in [None, {}, 'dish']:
            with self.subTest(items=items), self.assertRaises(DomainError):
                calculate_total(items, {})

    def test_item_must_be_object(self):
        with self.assertRaises(DomainError):
            calculate_total([1], {})

    def test_unknown_dish(self):
        with self.assertRaises(DomainError) as error:
            calculate_total([{'dish_id': str(uuid4()), 'quantity': 1}], {DISH_ID: 60000})
        self.assertEqual(error.exception.code, 'unknown_dish')

    def test_uuid_validation(self):
        for value in [None, '', 'not-a-uuid', 1]:
            with self.subTest(value=value), self.assertRaises(DomainError):
                uuid_text(value)
        self.assertEqual(uuid_text(DISH_ID.upper()), DISH_ID)

    def test_pagination(self):
        self.assertEqual(pagination(), (20, 0))
        self.assertEqual(pagination('100', '5'), (100, 5))
        for limit, offset in [(0, 0), (101, 0), (10, -1), ('x', 0), (True, 0), (1.5, 0)]:
            with self.subTest(limit=limit, offset=offset), self.assertRaises(DomainError):
                pagination(limit, offset)
