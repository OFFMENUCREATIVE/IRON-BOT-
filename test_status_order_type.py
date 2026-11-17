import copy, unittest
from netsuite_card_push import LarkCards, PushError, push_options, process
from test_netsuite_card_push import LINE, Ledger

FIELDS = [{'field_name': 'Status', 'property': {'options': [{'name': n} for n in
           ('Pending Artwork', 'Part Confirmed', 'Production', 'Shipped', 'On Hold')]}},
          {'field_name': 'Order Type', 'property': {'options': [{'name': 'Samples'}, {'name': 'Bulk'}]}},
          {'field_name': 'Products'}]
HEADER = {'NetSuite PO ID': '10001', 'PO': 'PO-EXAMPLE-1', 'NetSuite Supplier ID': '126',
          'Sales Order': 'SO-EXAMPLE-1', 'Customer': 'Customer'}


class Client:
    def __init__(self, fields=FIELDS):
        self.fields, self.created, self.updated = fields, [], []
    def _post(self, *a, **k): return {'code': 0, 'data': {'items': [{'record_id': 'recMASTER', 'fields': HEADER}]}}
    def list_fields(self, table, app_token=None): return self.fields
    def create_record(self, table, fields, app_token=None): self.created.append(fields); return {'record_id': 'recNEW'}
    def update_record_fields(self, table, rid, fields, app_token=None): self.updated.append(fields)


def cards(client):
    return LarkCards(client, {'test': {'base': 'base', 'table': 'table', 'fields': {
        'identity': 'Key', 'product': 'Products', 'description': 'Description', 'quantity': 'Ordered Quantity',
        'po_relation': 'Select PO', 'units': 'Unit'}}}, {'base': 'base', 'table': 'master', 'po_id_field': 'NetSuite PO ID'})


def line(**push):
    return dict(copy.deepcopy(LINE), push=dict({'address': 'none', 'sizes': False, 'description': True, 'units': True}, **push))


class StatusOrderTypeTests(unittest.TestCase):
    def test_default_status_is_pending_artwork_and_no_order_type(self):
        c = Client(); result = cards(c).create('test', line())
        self.assertEqual(c.created[0]['Status'], 'Pending Artwork')
        self.assertNotIn('Order Type', c.created[0])
        self.assertEqual(c.updated, [])
        self.assertEqual((result['cardStatus'], result['orderType'], result['notes']), ('Pending Artwork', '', []))

    def test_picked_status_and_order_type_go_on_the_card(self):
        c = Client(); cards(c).create('test', line(status='Production', orderType='Bulk'))
        self.assertEqual((c.created[0]['Status'], c.created[0]['Order Type']), ('Production', 'Bulk'))

    def test_option_missing_on_board_is_skipped_not_created(self):
        c = Client(); result = cards(c).create('test', line(status='Confirmed', orderType='Rush'))
        self.assertNotIn('Status', c.created[0]); self.assertNotIn('Order Type', c.created[0])
        self.assertEqual(c.updated, [])
        self.assertEqual(len(result['notes']), 2)

    def test_board_without_order_type_column(self):
        c = Client([FIELDS[0]]); result = cards(c).create('test', line(orderType='Samples'))
        self.assertNotIn('Order Type', c.created[0]); self.assertIn('Order Type', result['notes'][0])

    def test_picked_status_beats_discovered_board_default(self):
        c = Client(); k = cards(c); k.boards['test']['initial_fields'] = {'Status': 'Pending Artwork'}
        k.create('test', line(status='On Hold'))
        self.assertEqual(c.created[0]['Status'], 'On Hold')

    def test_bad_values_rejected(self):
        for bad in ({'status': 5}, {'orderType': 'x' * 61}, {'status': '<script>'}, {'colour': 'red'}):
            with self.assertRaises(PushError): push_options(line(**bad))
        self.assertEqual(push_options(line(status=''))['status'], 'Pending Artwork')

    def test_result_reaches_send_page(self):
        class Cards:
            def find(self, board, key): return []
            def create(self, board, l): return {'record_id': 'recA', 'cardStatus': 'Production', 'orderType': 'Bulk', 'notes': []}
            def url(self, board, rid): return 'u'
        out = process(line(status='Production', orderType='Bulk'), Ledger(), Cards())
        self.assertEqual((out['cardStatus'], out['orderType']), ('Production', 'Bulk'))


if __name__ == '__main__':
    unittest.main()


class NoStatusTests(unittest.TestCase):
    def test_no_status_leaves_card_blank(self):
        c = Client(); k = cards(c); k.boards['test']['initial_fields'] = {'Status': 'Pending Artwork'}
        result = k.create('test', line(status='none', orderType='none'))
        self.assertNotIn('Status', c.created[0]); self.assertNotIn('Order Type', c.created[0])
        self.assertEqual(c.updated, []); self.assertEqual(result['notes'], [])

    def test_no_status_when_board_unreadable_is_not_set_later(self):
        class Blind(Client):
            def list_fields(self, *a, **k): raise RuntimeError
        c = Blind(); cards(c).create('test', line(status='none'))
        self.assertEqual(c.updated, [])


class BoardOrderTests(unittest.TestCase):
    def test_listing_follows_lark_sidebar(self):
        from netsuite_board_discovery import BoardRegistry
        reg = BoardRegistry.__new__(BoardRegistry)
        import re as _re
        reg.exclude = _re.compile(r'(?!)')
        reg.boards = {'wb': {'table': 'tblB', 'label': 'Workshop Bulk'}, 'tblA': {'table': 'tblA', 'label': 'Workshop Samples', 'discovered': True},
                      'z': {'table': 'tblZ', 'label': 'Alpha'}, 'new': {'table': 'tblN', 'label': 'Brand New'}}
        reg.review_config = {k: {} for k in reg.boards}
        reg.order = {'tblA': 0, 'tblB': 1, 'tblZ': 2}
        self.assertEqual([r['label'] for r in reg.listing()], ['Workshop Samples', 'Workshop Bulk', 'Alpha', 'Brand New'])
