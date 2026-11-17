"""Model choice and token trimming for Iron Bot's AI calls."""
import ast, os, re, unittest
from unittest import mock
import anthropic
import ai_models


def _bot_function(name, extra=None):
    """Load one top-level function (and constants it needs) from bot_server
    without importing the whole server."""
    src = open('bot_server.py').read()
    tree = ast.parse(src)
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in (name, *(extra or ())))
            or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in ('CONTEXT_FIELDS', 'CONTEXT_VALUE_CHARS', 'ORDER_WORDS') for t in n.targets))]
    ns = {'re': re}
    from datetime import datetime, timezone
    ns.update(datetime=datetime, timezone=timezone, field_to_text=lambda v: v if isinstance(v, str) else str(v))
    exec(compile(ast.Module(body=keep, type_ignores=[]), 'bot_server.py', 'exec'), ns)
    return ns


class ModelTests(unittest.TestCase):
    def test_default_is_cheapest_and_env_overrides(self):
        with mock.patch.dict(os.environ, {'BOT_MODEL': ''}):
            self.assertEqual(ai_models.model(), ai_models.CHEAPEST)
        with mock.patch.dict(os.environ, {'BOT_MODEL': 'claude-sonnet-5-5'}):
            self.assertEqual(ai_models.model(), 'claude-sonnet-5-5')

    def test_hard_coded_model_is_ignored(self):
        client = mock.Mock()
        with mock.patch.dict(os.environ, {'BOT_MODEL': 'm1'}):
            ai_models.create(client, model='old', max_tokens=5, messages=[])
        self.assertEqual(client.messages.create.call_args.kwargs['model'], 'm1')

    def test_unknown_model_falls_back_once(self):
        client = mock.Mock()
        err = anthropic.NotFoundError('nope', response=mock.Mock(status_code=404), body=None)
        client.messages.create.side_effect = [err, 'ok']
        with mock.patch.dict(os.environ, {'BOT_MODEL': 'typo', 'BOT_MODEL_FALLBACK': ''}):
            self.assertEqual(ai_models.create(client, messages=[]), 'ok')
        self.assertEqual(client.messages.create.call_args.kwargs['model'], ai_models.FALLBACK)

    def test_no_call_site_hard_codes_a_model(self):
        for path in ('bot_server.py', 'google_client.py'):
            src = open(path).read()
            self.assertNotIn('messages.create(', src, path)
            self.assertFalse(re.search(r'model="claude', src), path)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.ns = _bot_function('needs_records', ['build_context'])

    def test_general_question_gets_no_records(self):
        self.assertFalse(self.ns['needs_records']("what's 2+2", [], [{'__table_name__': 'Woof'}]))
        self.assertFalse(self.ns['needs_records']('write me a haiku about rain'))

    def test_order_questions_get_records(self):
        nr = self.ns['needs_records']
        for q in ('where is PO7020', 'what is overdue', 'status of the hats', 'any samples shipped?'):
            self.assertTrue(nr(q), q)
        self.assertTrue(nr('how is woof doing', [], [{'__table_name__': 'Woof Production'}]))
        self.assertTrue(nr('and the other one?', [{'role': 'user', 'content': 'whats due this week'}]))

    def test_only_key_fields_and_clipped(self):
        row = {'__table_name__': 'Woof', 'Product Name': 'Hat', 'Status': 'Production',
               'Ship items': 'https://x', 'Packing Split': 'long formula', 'Description': 'x' * 500}
        text = self.ns['build_context']([row])
        self.assertIn('Product Name: Hat', text); self.assertIn('Status: Production', text)
        self.assertNotIn('Ship items', text); self.assertNotIn('Packing Split', text)
        self.assertLess(len(text), 400)


if __name__ == '__main__':
    unittest.main()
