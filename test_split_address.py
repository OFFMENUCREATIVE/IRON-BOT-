import unittest

from off_menu_units_sync import UnitsSync, SPLIT_MARK, SHIP_TO, split_address, base_address

NS = 'Lisa Sahakian\nIan Charms\n6907 Bonita Terrace\nLos Angeles CA 90068'
FIELDS = [
    {'field_name': 'Off Menu Qty', 'type': 2},
    {'field_name': 'Shipping Address', 'type': 1},
]


class FakeLark:
    def __init__(self, cards):
        self.cards, self.master, self.puts = cards, {}, []

    def _ok(self, data):
        return {'code': 0, 'data': data}

    def _get(self, path, params=None):
        if path.endswith('/tables'):
            return self._ok({'items': [{'table_id': 'tblBoard', 'name': 'Workshop Bulk'},
                                       {'table_id': 'tblMaster', 'name': 'Off Menu Extras'}]})
        if path.endswith('/fields'):
            return self._ok({'items': FIELDS})
        if path.endswith('tblMaster/records'):
            return self._ok({'items': [{'record_id': k, 'fields': v} for k, v in self.master.items()]})
        raise AssertionError(path)

    def _post(self, path, body=None, params=None):
        if path.endswith('tblMaster/records'):
            self.master['rec%d' % len(self.master)] = body['fields']
            return self._ok({})
        if path.endswith('/records/search'):
            cond = body['filter']['conditions'][0]
            if cond['operator'] == 'contains':
                rows = [(k, v) for k, v in self.cards.items() if SPLIT_MARK in (v.get('Shipping Address') or '')]
            else:
                rows = [(k, v) for k, v in self.cards.items() if (v.get('Off Menu Qty') or 0) > 0]
            return self._ok({'items': [{'record_id': k, 'fields': dict(v)} for k, v in rows]})
        raise AssertionError(path)

    def _put(self, path, body=None, params=None):
        rid = path.rsplit('/', 1)[1]
        target = self.cards if '/tblBoard/' in path else self.master
        target[rid].update(body['fields'])
        if '/tblBoard/' in path:
            self.puts.append(rid)
        return self._ok({})

    def _delete(self, path, body=None, params=None):
        self.master.pop(path.rsplit('/', 1)[1])
        return self._ok({})


class SplitAddressTest(unittest.TestCase):
    def test_block_added_to_netsuite_address(self):
        lark = FakeLark({'recA': {'Off Menu Qty': 5, 'Client Qty': 95, 'Shipping Address': NS}})
        UnitsSync(lark, master='tblMaster').run_once()
        addr = lark.cards['recA']['Shipping Address']
        self.assertTrue(addr.startswith(NS + '\n\n' + SPLIT_MARK))
        self.assertIn('CLIENT: 95 units', addr)
        self.assertIn('OFF MENU: 5 units → ' + SHIP_TO, addr)

    def test_idempotent(self):
        lark = FakeLark({'recA': {'Off Menu Qty': 5, 'Client Qty': 95, 'Shipping Address': NS}})
        sync = UnitsSync(lark, master='tblMaster')
        sync.run_once(); sync.run_once()
        self.assertEqual(lark.puts, ['recA'])

    def test_qty_change_and_new_netsuite_address(self):
        lark = FakeLark({'recA': {'Off Menu Qty': 5, 'Shipping Address': NS}})
        sync = UnitsSync(lark, master='tblMaster')
        sync.run_once()
        lark.cards['recA']['Off Menu Qty'] = 8
        sync.run_once()
        self.assertIn('OFF MENU: 8 units', lark.cards['recA']['Shipping Address'])
        lark.cards['recA']['Shipping Address'] = 'New NS Address'  # NetSuite re-copy drops the block
        sync.run_once()
        self.assertTrue(lark.cards['recA']['Shipping Address'].startswith('New NS Address\n\n' + SPLIT_MARK))

    def test_block_removed_when_no_longer_split(self):
        lark = FakeLark({'recA': {'Off Menu Qty': 0, 'Shipping Address': split_address(NS, 5)}})
        UnitsSync(lark, master='tblMaster').run_once()
        self.assertEqual(lark.cards['recA']['Shipping Address'], NS)

    def test_cards_without_split_untouched(self):
        lark = FakeLark({'recA': {'Off Menu Qty': None, 'Shipping Address': NS}})
        UnitsSync(lark, master='tblMaster').run_once()
        self.assertEqual(lark.puts, [])

    def test_empty_address(self):
        self.assertTrue(split_address('', 3).startswith(SPLIT_MARK))
        self.assertEqual(base_address(split_address(NS, 3)), NS)


if __name__ == '__main__':
    unittest.main()
