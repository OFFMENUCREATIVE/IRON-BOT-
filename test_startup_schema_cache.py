import unittest
from netsuite_card_push import _StartupSchemaCache, _drop_deleted_boards, TABLE_NOT_FOUND


class CountingClient:
    def __init__(self, codes=None):
        self.codes = codes or {}
        self.calls = []

    def _get(self, path, params=None):
        self.calls.append(path)
        table = path.split('/tables/')[1].split('/')[0]
        return {'code': self.codes.get(table, 0), 'data': {'items': [], 'has_more': False}}


class StartupSchemaCacheTest(unittest.TestCase):
    def boards(self):
        return {f'b{i}': {'base': 'base', 'table': f'tbl{i}'} for i in range(10)}

    def test_each_board_schema_is_read_once(self):
        client = CountingClient()
        cache = _StartupSchemaCache(client)
        boards = self.boards()
        cache.prefetch(boards)
        _drop_deleted_boards(cache, boards)
        for _ in range(3):  # the three reconcile passes
            for b in boards.values():
                cache._get(f"/open-apis/bitable/v1/apps/base/tables/{b['table']}/fields", params={'page_size': 100})
        self.assertEqual(len(client.calls), 10)

    def test_deleted_table_still_dropped_and_rate_limits_not_cached(self):
        client = CountingClient({'tbl1': TABLE_NOT_FOUND, 'tbl2': 1254290})
        cache = _StartupSchemaCache(client)
        boards = self.boards()
        cache.prefetch(boards)
        _drop_deleted_boards(cache, boards)
        self.assertNotIn('b1', boards)
        self.assertIn('b2', boards)
        self.assertEqual(client.calls.count('/open-apis/bitable/v1/apps/base/tables/tbl2/fields'), 2)

    def test_other_reads_pass_through(self):
        client = CountingClient()
        cache = _StartupSchemaCache(client)
        cache._get('/open-apis/bitable/v1/apps/base/tables/tbl1/records')
        cache._get('/open-apis/bitable/v1/apps/base/tables/tbl1/records')
        self.assertEqual(len(client.calls), 2)


if __name__ == '__main__':
    unittest.main()
