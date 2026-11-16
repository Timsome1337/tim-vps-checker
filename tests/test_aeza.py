from datetime import date
import unittest
from unittest.mock import patch
from app.aeza import AezaClient, decode_service, get_services, parse_date


class TestAezaAdapter(unittest.TestCase):
    def test_date_formats(self):
        self.assertEqual(parse_date('2026-12-08T12:11:00+03:00'), date(2026, 12, 8))
        self.assertEqual(parse_date('2026-12-08'), date(2026, 12, 8))
        self.assertIsNone(parse_date('not-a-date'))
        self.assertIsNone(parse_date(None))

    def test_service_fields(self):
        info = decode_service({
            'id': 123, 'name': 'Sweden', 'paidUntil': '2026-12-25T23:59:59Z',
            'product': {'name': 'STO-1'}, 'status': 'active', 'autoProlong': False,
            'price': {'value': 7, 'currency': 'EUR'},
        })
        self.assertEqual(info.service_id, '123')
        self.assertEqual(info.expires_at, date(2026, 12, 25))
        self.assertEqual(info.tariff, 'STO-1')
        self.assertEqual(info.price, '7 EUR')
        self.assertIs(info.auto_prolong, False)

    def test_no_unsafe_price_guess(self):
        self.assertIsNone(decode_service({'id': 1, 'price': 6500}).price)

    def test_list_envelopes(self):
        self.assertEqual(get_services({'data': {'items': [{'id': 7}]}}), [{'id': 7}])
        self.assertEqual(get_services({'items': [{'id': 8}]}), [{'id': 8}])

    def test_service_selection(self):
        client = AezaClient('test-secret', service_id='22')
        def fake_get(route):
            if route == 'services':
                return {'data': {'items': [{'id': 1}, {'id': 22, 'product': {'name': 'STO-2'}}]}}
            if route == 'services/22':
                return {'data': {'id': 22, 'expiresAt': '2026-11-29'}}
            raise AssertionError(route)
        with patch.object(client, '_get', side_effect=fake_get):
            info = client.read_service()
        self.assertEqual(info.expires_at, date(2026, 11, 29))
        self.assertEqual(info.tariff, 'STO-2')

    def test_no_implicit_choice_between_services(self):
        client = AezaClient('test-secret')
        with patch.object(client, '_get', return_value={'items': [{'id': 1}, {'id': 2}]}):
            with self.assertRaisesRegex(RuntimeError, 'AEZA_SERVICE_ID'):
                client.read_service()

    def test_safe_api_base(self):
        with self.assertRaises(ValueError):
            AezaClient('test-secret', base_url='https://external.example/api/v2')
        with self.assertRaises(ValueError):
            AezaClient('test-secret', base_url='http://my.aeza.net/api/v2')

if __name__ == '__main__':
    unittest.main()
