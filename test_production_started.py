import unittest

from off_menu_units_sync import UnitsSync, PRODUCTION_STARTED

FIELDS = [
    {'field_name': 'Off Menu Qty', 'type': 2},
    {'field_name': 'Status', 'type': 3},
    {'field_name': PRODUCTION_STARTED, 'type': 5},
]


class FakeLark:
    def __init__(self, cards, fields=FIELDS):
        self.cards, self.fields, self.batches = cards, fields, []

    def _ok(self, data):
        return {'code': 0, 'data': data}

    def _get(self, path, params=None):
        if path.endswith('/tables'):
            return self._ok({'items': [{'table_id': 'tblBoard', 'name': 'Workshop Bulk'}]})
        if path.endswith('/fields'):
            return self._ok({'items': self.fields})
        if path.endswith('tblMaster/records'):
            return self._ok({'items': []})
        raise AssertionError(path)

    def _post(self, path, body=None, params=None):
        if path.endswith('/records/search'):
            conds = body['filter']['conditions']
            if any(c['field_name'] == 'Status' for c in conds):
                rows = [(k, v) for k, v in self.cards.items()
                        if v.get('Status') == 'Production' and not v.get(PRODUCTION_STARTED)]
            else:
                rows = []
            return self._ok({'items': [{'record_id': k, 'fields': dict(v)} for k, v in rows]})
        if path.endswith('/records/batch_update'):
            self.batches.append(body)
            for rec in body['records']:
                self.cards[rec['record_id']].update(rec['fields'])
            return self._ok({})
        raise AssertionError(path)


class ProductionStartedTest(unittest.TestCase):
    def test_stamps_new_production_cards_only(self):
        lark = FakeLark({
            'recNew': {'Status': 'Production'},
            'recOld': {'Status': 'Production', PRODUCTION_STARTED: 1700000000000},
            'recArt': {'Status': 'Pending Artwork'},
        })
        stats = UnitsSync(lark, master='tblMaster').run_once()
        self.assertEqual(stats['production'], 1)
        self.assertIsInstance(lark.cards['recNew'][PRODUCTION_STARTED], int)
        self.assertEqual(lark.cards['recOld'][PRODUCTION_STARTED], 1700000000000)
        self.assertNotIn(PRODUCTION_STARTED, lark.cards['recArt'])

    def test_second_pass_changes_nothing(self):
        lark = FakeLark({'recNew': {'Status': 'Production'}})
        sync = UnitsSync(lark, master='tblMaster')
        sync.run_once()
        stamped = lark.cards['recNew'][PRODUCTION_STARTED]
        self.assertEqual(sync.run_once()['production'], 0)
        self.assertEqual(lark.cards['recNew'][PRODUCTION_STARTED], stamped)

    def test_board_without_field_is_skipped(self):
        lark = FakeLark({'recNew': {'Status': 'Production'}}, fields=FIELDS[:2])
        self.assertEqual(UnitsSync(lark, master='tblMaster').run_once()['production'], 0)
        self.assertEqual(lark.batches, [])

    def test_today_is_midnight(self):
        ms = UnitsSync.today_ms()
        self.assertEqual(ms % 1000, 0)


if __name__ == '__main__':
    unittest.main()
