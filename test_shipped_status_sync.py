import unittest

from shipped_status_sync import ShippedSync

STATUS = {'field_name': 'Status', 'type': 3, 'property': {'options': [
    {'name': n} for n in ('Pending Artwork', 'Production', 'Part Shipped', 'Shipped', 'On Hold', 'Cancelled')]}}
BOARD_FIELDS = [
    STATUS,
    {'field_name': 'Ordered Quantity', 'type': 2},
    {'field_name': 'Shipments', 'type': 21, 'property': {'table_id': 'tblShip'}},
    {'field_name': 'Tracking Number', 'type': 1},
    {'field_name': 'Carrier', 'type': 3, 'property': {'options': [{'name': n} for n in ('FEDEX', 'UPS', 'DHL', 'OTHERS')]}},
    {'field_name': 'Date Shipped', 'type': 5},
    {'field_name': 'Shipment Method', 'type': 3, 'property': {'options': [{'name': 'AIR DDP (8-10 days)'}]}},
]
ISSUES_FIELDS = [  # Order Issues style table: has Shipments but no "Shipped" status
    {'field_name': 'Status', 'type': 3, 'property': {'options': [{'name': 'Open'}, {'name': 'Resolved'}]}},
    {'field_name': 'Ordered Quantity', 'type': 2},
    {'field_name': 'Shipments', 'type': 21, 'property': {'table_id': 'tblShip2'}},
]


class FakeLark:
    def __init__(self, cards, shipments):
        self.cards, self.shipments, self.updates = cards, shipments, []

    def _ok(self, data):
        return {'code': 0, 'data': data}

    def _get(self, path, params=None):
        if path.endswith('/tables'):
            return self._ok({'items': [{'table_id': 'tblBoard', 'name': 'TPD Design House'},
                                       {'table_id': 'tblIssues', 'name': 'Order Issues Sheet'},
                                       {'table_id': 'tblCopy', 'name': 'TPD Copy'}]})
        if path.endswith('tblBoard/fields') or path.endswith('tblCopy/fields'):
            return self._ok({'items': BOARD_FIELDS})
        if path.endswith('tblIssues/fields'):
            return self._ok({'items': ISSUES_FIELDS})
        raise AssertionError(path)

    def _post(self, path, body=None, params=None):
        if path.endswith('tblBoard/records/search'):
            return self._ok({'items': [{'record_id': k, 'fields': v} for k, v in self.cards.items()]})
        if path.endswith('/records/batch_get'):
            return self._ok({'records': [{'record_id': i, 'fields': self.shipments[i]}
                                         for i in body['record_ids'] if i in self.shipments]})
        if path.endswith('tblBoard/records/batch_update'):
            self.updates.extend(body['records'])
            return self._ok({})
        raise AssertionError(path)


def link(*ids):
    return [{'record_ids': list(ids), 'table_id': 'tblShip'}]


def ship(qty, tracking='1Z999AA10123456784', status='Shipped', carrier='UPS', when=1794000000000, **kw):
    f = {'Quantity': qty, 'Shipment Status': status, 'Tracking Number': tracking,
         'Carrier': carrier, 'Actual Dispatch Date': when}
    f.update(kw)
    return f


def run(cards, shipments):
    lark = FakeLark(cards, shipments)
    stats = ShippedSync(lark, base='base').run_once()
    return lark, stats, {u['record_id']: u['fields'] for u in lark.updates}


class ShippedSyncTest(unittest.TestCase):
    def test_fully_shipped_card_moves_to_shipped_and_shows_the_record(self):
        lark, stats, up = run({'recA': {'Status': 'Production', 'Ordered Quantity': 100, 'Shipments': link('recS1', 'recS2')}},
                              {'recS1': ship(60, when=1, tracking='111111111111'),
                               'recS2': ship(40, carrier='FedEx', when=2, tracking='222222222222',
                                          **{'Shipment Method': 'AIR DDP (8-10 days)'})})
        self.assertEqual(stats['boards'], 1)  # issues sheet and copy table ignored
        self.assertEqual(up['recA']['Status'], 'Shipped')
        self.assertEqual(up['recA']['Tracking Number'], '222222222222, 111111111111')  # newest first
        self.assertEqual(up['recA']['Carrier'], 'FEDEX')
        self.assertEqual(up['recA']['Date Shipped'], 2)
        self.assertEqual(up['recA']['Shipment Method'], 'AIR DDP (8-10 days)')

    def test_partial_shipment_is_part_shipped(self):
        _, _, up = run({'recA': {'Status': 'Production', 'Ordered Quantity': 100, 'Shipments': link('recS1')}},
                       {'recS1': ship(30)})
        self.assertEqual(up['recA']['Status'], 'Part Shipped')

    def test_no_real_tracking_means_no_change(self):
        _, _, up = run({'recA': {'Status': 'Production', 'Ordered Quantity': 10, 'Shipments': link('recS1', 'recS2')}},
                       {'recS1': ship(10, tracking='12345tfw'), 'recS2': ship(10, tracking='')})
        self.assertEqual(up, {})

    def test_unshipped_rows_do_not_count(self):
        _, _, up = run({'recA': {'Status': 'Production', 'Ordered Quantity': 10, 'Shipments': link('recS1')}},
                       {'recS1': ship(10, status='Packed')})
        self.assertEqual(up, {})

    def test_never_moves_backwards_or_touches_cancelled(self):
        _, _, up = run({'recA': {'Status': 'Shipped', 'Ordered Quantity': 100, 'Shipments': link('recS1')},
                        'recB': {'Status': 'Cancelled', 'Ordered Quantity': 10, 'Shipments': link('recS2')}},
                       {'recS1': ship(10), 'recS2': ship(10)})
        self.assertNotIn('Status', up.get('recA', {}))
        self.assertNotIn('recB', up)

    def test_keeps_tracking_typed_on_card_and_is_idempotent(self):
        cards = {'recA': {'Status': 'Production', 'Ordered Quantity': 5, 'Shipments': link('recS1'),
                          'Tracking Number': 'MANUAL123456'}}
        lark, _, up = run(cards, {'recS1': ship(5, tracking='1Z999AA10123456784')})
        self.assertEqual(up['recA']['Tracking Number'], '1Z999AA10123456784, MANUAL123456')
        cards['recA'].update(up['recA'])
        _, _, again = run(cards, {'recS1': ship(5, tracking='1Z999AA10123456784')})
        self.assertEqual(again, {})

    def test_blank_ordered_quantity_fills_record_but_not_status(self):
        _, _, up = run({'recA': {'Status': 'Production', 'Shipments': link('recS1')}}, {'recS1': ship(5)})
        self.assertNotIn('Status', up['recA'])
        self.assertIn('Tracking Number', up['recA'])

    def test_dry_run_writes_nothing(self):
        lark = FakeLark({'recA': {'Status': 'Production', 'Ordered Quantity': 5, 'Shipments': link('recS1')}},
                        {'recS1': ship(5)})
        stats = ShippedSync(lark, base='base', dry_run=True).run_once()
        self.assertEqual(stats['shipped'], 1)
        self.assertEqual(lark.updates, [])


if __name__ == '__main__':
    unittest.main()
