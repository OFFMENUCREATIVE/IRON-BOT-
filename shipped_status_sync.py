"""Move order cards to Shipped when their shipments go out.

Every production board has a "<Board> — Shipments" table linked to its cards.
Total Shipped and Remaining on a card are formulas over those shipment rows,
so the quantities already update by themselves. What did not update was the
card itself: its Status stayed at "Production" and the tracking number,
carrier and ship date never reached the card.

This sync closes that gap. For every card that has at least one shipment row
marked "Shipped" with a real tracking number:

  * Status -> "Shipped" when everything ordered has shipped,
              "Part Shipped" when some of it has (if the board has that option).
  * Tracking Number, Carrier, Date Shipped and Shipment Method are filled from
    the shipment rows (newest shipment first), so the card shows the record.

Rules it keeps:
  * nothing happens without a real tracking number (no test rows like "12345")
  * a card is never moved backwards (Shipped stays Shipped) and Cancelled
    cards are never touched
  * tracking numbers already typed on the card are kept, not replaced
  * no messages are sent to clients or chats -- the card is the record

Boards are discovered by schema: a single-select "Status" with a "Shipped"
option, a linked "Shipments" table and an "Ordered Quantity" number.

Opt out with SHIPPED_SYNC_ENABLED=false; dry run with SHIPPED_SYNC_DRY_RUN=true.
"""
import logging
import os
import threading
import time

from off_menu_units_sync import EXCLUDE, RATE_LIMITED, qty, record_ids, text
from shipment_tracker import is_real_tracking

log = logging.getLogger(__name__)

BASE = os.environ.get('SHIPPED_SYNC_BASE', 'VcAlbwImaab1KlsFLBVjunTNp1c')
SINGLE_SELECT, NUMBER, LINK, DUPLEX = 3, 2, 18, 21

SHIPPED, PART_SHIPPED = 'Shipped', 'Part Shipped'
NEVER_TOUCH = {'Cancelled'}
CARD_FIELDS = ['Status', 'Ordered Quantity', 'Shipments', 'Tracking Number',
               'Carrier', 'Date Shipped', 'Shipment Method']
SHIPMENT_FIELDS = ['Quantity', 'Shipment Status', 'Tracking Number', 'Carrier',
                   'Actual Dispatch Date', 'Date Shipped', 'Shipment Method']

# carrier names on shipment rows -> what a card's Carrier options may be called
CARRIER_ALIASES = {'fedex': ['FEDEX', 'FedEx'], 'ups': ['UPS'], 'dhl': ['DHL'],
                   'usps': ['USPS', 'OTHERS', 'Others', 'Other'],
                   'other': ['OTHERS', 'Others', 'Other']}


def options(field):
    return [o.get('name') for o in ((field or {}).get('property') or {}).get('options') or []]


def pick_option(field, wanted):
    """The board's option matching `wanted` (case-insensitive), or None."""
    if not wanted:
        return None
    names = options(field)
    lower = {n.lower(): n for n in names if n}
    if wanted.lower() in lower:
        return lower[wanted.lower()]
    for alias in CARRIER_ALIASES.get(wanted.lower(), []):
        if alias.lower() in lower:
            return lower[alias.lower()]
    return None


def ms(value):
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict) and isinstance(value.get('value'), list) and value['value']:
        return ms(value['value'][0])
    if isinstance(value, list) and value and isinstance(value[0], (int, float)):
        return int(value[0])
    return None


class ShippedSync:
    def __init__(self, client, base=BASE, dry_run=False):
        self.client, self.base, self.dry_run = client, base, dry_run
        self._boards, self._boards_at = [], 0

    # -- Lark access (same retry behaviour as the units sync)
    def _api(self, method, path, **kw):
        for attempt in range(4):
            response = getattr(self.client, '_' + method)(f'/open-apis/bitable/v1/apps/{self.base}{path}', **kw)
            if response.get('code') != RATE_LIMITED:
                break
            time.sleep(2 * (attempt + 1))
        if response.get('code') != 0:
            raise RuntimeError(f"Lark {method} {path} failed: {response.get('code')} {response.get('msg')}")
        return response.get('data') or {}

    def _paged(self, path, body=None):
        items, token = [], None
        for _ in range(200):
            params = {'page_size': 500 if body is not None else 100}
            if token:
                params['page_token'] = token
            data = (self._api('post', path, body=body, params=params) if body is not None
                    else self._api('get', path, params=params))
            items.extend(data.get('items') or [])
            if not data.get('has_more') or not data.get('page_token'):
                return items
            token = data['page_token']
        raise RuntimeError('pagination did not finish: ' + path)

    # -- discovery
    def boards(self, max_age=600):
        if self._boards and time.time() - self._boards_at < max_age:
            return self._boards
        found = []
        for table in self._paged('/tables'):
            tid, name = table.get('table_id'), table.get('name') or ''
            if not tid or EXCLUDE.search(name):
                continue
            try:
                fields = {f['field_name']: f for f in self._paged(f'/tables/{tid}/fields')}
            except Exception as e:
                log.warning('SHIPPED_SYNC: cannot read fields of %s: %s', name, e)
                continue
            status, ships = fields.get('Status') or {}, fields.get('Shipments') or {}
            if (status.get('type') == SINGLE_SELECT and SHIPPED in options(status)
                    and ships.get('type') in (LINK, DUPLEX)
                    and (fields.get('Ordered Quantity') or {}).get('type') == NUMBER):
                found.append({'table': tid, 'name': name, 'fields': fields,
                              'shipments': (ships.get('property') or {}).get('table_id')})
        self._boards, self._boards_at = found, time.time()
        log.info('SHIPPED_SYNC: %d boards', len(found))
        return found

    # -- reading
    def cards(self, board):
        names = [n for n in CARD_FIELDS if n in board['fields']]
        return self._paged(f"/tables/{board['table']}/records/search", body={'field_names': names})

    def shipments(self, table, ids):
        out = {}
        for i in range(0, len(ids), 100):
            data = self._api('post', f'/tables/{table}/records/batch_get', body={'record_ids': ids[i:i + 100]})
            for row in data.get('records') or []:
                out[row['record_id']] = row.get('fields') or {}
        return out

    # -- deciding
    def wanted(self, board, card, rows):
        """Field changes for one card, given its shipment rows."""
        f, fields = card.get('fields') or {}, board['fields']
        current = text(f.get('Status'))
        if current in NEVER_TOUCH:
            return {}
        shipped = [r for r in rows if text(r.get('Shipment Status')) == SHIPPED and qty(r.get('Quantity')) > 0]
        tracked = [r for r in shipped if is_real_tracking(text(r.get('Tracking Number')))]
        if not tracked:
            return {}
        when = lambda r: ms(r.get('Actual Dispatch Date')) or ms(r.get('Date Shipped')) or 0
        tracked.sort(key=when, reverse=True)
        latest = tracked[0]

        out = {}
        total, ordered = sum(qty(r.get('Quantity')) for r in shipped), qty(f.get('Ordered Quantity'))
        if not ordered:
            target = None  # without an ordered quantity "all shipped" cannot be known
        elif total >= ordered:
            target = SHIPPED
        else:
            target = PART_SHIPPED if PART_SHIPPED in options(fields.get('Status')) else None
        if target and target != current and not (current == SHIPPED and target == PART_SHIPPED):
            out['Status'] = target

        if 'Tracking Number' in fields:
            numbers = []
            for r in tracked:
                t = text(r.get('Tracking Number'))
                if t not in numbers:
                    numbers.append(t)
            for t in [x.strip() for x in text(f.get('Tracking Number')).replace('\n', ',').split(',')]:
                if t and t not in numbers:
                    numbers.append(t)  # keep anything the team typed on the card
            joined = ', '.join(numbers)
            if joined != text(f.get('Tracking Number')):
                out['Tracking Number'] = joined
        if fields.get('Carrier', {}).get('type') == SINGLE_SELECT:
            carrier = pick_option(fields['Carrier'], text(latest.get('Carrier')))
            if carrier and carrier != text(f.get('Carrier')):
                out['Carrier'] = carrier
        if 'Date Shipped' in fields and when(latest) and ms(f.get('Date Shipped')) != when(latest):
            out['Date Shipped'] = when(latest)
        if fields.get('Shipment Method', {}).get('type') == SINGLE_SELECT:
            method = pick_option(fields['Shipment Method'], text(latest.get('Shipment Method')))
            if method and method != text(f.get('Shipment Method')):
                out['Shipment Method'] = method
        return out

    # -- one pass
    def run_once(self):
        stats = {'boards': 0, 'cards': 0, 'updated': 0, 'shipped': 0, 'part_shipped': 0, 'errors': 0}
        for board in self.boards():
            stats['boards'] += 1
            try:
                cards = [c for c in self.cards(board) if record_ids((c.get('fields') or {}).get('Shipments'))]
                ids = sorted({i for c in cards for i in record_ids(c['fields'].get('Shipments'))})
                rows = self.shipments(board['shipments'], ids) if ids and board['shipments'] else {}
                updates = []
                for c in cards:
                    stats['cards'] += 1
                    change = self.wanted(board, c, [rows[i] for i in record_ids(c['fields'].get('Shipments')) if i in rows])
                    if change:
                        updates.append({'record_id': c['record_id'], 'fields': change})
                        stats['shipped'] += change.get('Status') == SHIPPED
                        stats['part_shipped'] += change.get('Status') == PART_SHIPPED
                for i in range(0, len(updates), 500):
                    batch = updates[i:i + 500]
                    if self.dry_run:
                        log.info('SHIPPED_SYNC dry run %s: %s', board['name'], batch)
                    else:
                        self._api('post', f"/tables/{board['table']}/records/batch_update", body={'records': batch})
                stats['updated'] += len(updates)
            except Exception as e:
                stats['errors'] += 1
                log.warning('SHIPPED_SYNC: %s skipped: %s', board['name'], e)
        log.info('SHIPPED_SYNC: %s', stats)
        return stats

    def start(self, interval=300):
        def loop():
            while True:
                try:
                    self.run_once()
                except Exception as e:
                    log.error('SHIPPED_SYNC: pass failed: %s', e)
                time.sleep(interval)
        threading.Thread(target=loop, name='shipped-status-sync', daemon=True).start()


def register(app=None):
    if os.environ.get('SHIPPED_SYNC_ENABLED', 'true') != 'true':
        return None
    try:
        from lark_client import LarkClient
        sync = ShippedSync(LarkClient(), dry_run=os.environ.get('SHIPPED_SYNC_DRY_RUN') == 'true')
        sync.start(interval=int(os.environ.get('SHIPPED_SYNC_INTERVAL', '900')))
        log.info('SHIPPED_SYNC: started')
        return sync
    except Exception as e:
        log.error('SHIPPED_SYNC: disabled: %s', e)
        return None
