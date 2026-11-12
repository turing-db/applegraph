"""Apple Mail envelope index: who emails whom, aggregated per correspondent and day."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile

from .model import Builder, UNIX_TO_APPLE, apple_time, snapshot, system_day

SKIP_MAILBOXES = ('spam', 'junk', 'bin', 'trash', 'draft', 'deleted')
RECIPIENT_TYPES = {0: 'to', 1: 'cc', 2: 'bcc'}


def add_mail(builder: Builder, path: Path, my_addresses: set[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix='apple-graph-mail-') as tmp:
        copy = Path(tmp) / 'Envelope Index'
        snapshot(path, copy)
        with closing(sqlite3.connect(copy)) as db:
            addresses = {row[0]: (row[1] or '', row[2] or '') for row in db.execute('SELECT ROWID, address, comment FROM addresses')}
            mailboxes = {row[0]: (row[1] or '') for row in db.execute('SELECT ROWID, url FROM mailboxes')}
            messages = db.execute('SELECT ROWID, sender, date_received, date_sent, mailbox, conversation_id FROM messages WHERE deleted = 0').fetchall()
            recipients = defaultdict(list)
            for message, address, kind in db.execute('SELECT message, address, type FROM recipients ORDER BY position'):
                recipients[message].append((address, RECIPIENT_TYPES.get(kind, 'other')))
    mine = {a.lower() for a in my_addresses}
    for _, sender, _, _, mailbox, _ in messages:
        if 'sent' in mailboxes.get(mailbox, '').lower() and sender in addresses:
            mine.add(addresses[sender][0].lower())

    def node_for(address_id: int) -> int | None:
        address, comment = addresses.get(address_id, ('', ''))
        if not address:
            return None
        if address.lower() in mine:
            return builder.me
        return builder.person(address, name=comment, source='Mail', labels=('AppleEntity', 'Correspondent'))

    pairs = defaultdict(lambda: dict(messages=0, to=0, cc=0, bcc=0, other=0, first=None, last=None, conversations=set()))
    per_day = defaultdict(lambda: dict(messages=0, recipients=set()))
    counted = skipped = 0
    for rowid, sender, received, sent, mailbox, conversation in messages:
        if any(word in mailboxes.get(mailbox, '').lower() for word in SKIP_MAILBOXES):
            skipped += 1
            continue
        when = received or sent
        if not isinstance(when, (int, float)) or when <= 0:
            continue
        apple_seconds = when - UNIX_TO_APPLE
        day = system_day(apple_seconds)
        sender_node = node_for(sender)
        if sender_node is None or day is None:
            continue
        counted += 1
        per_day[sender_node, day]['messages'] += 1
        for address_id, kind in recipients.get(rowid, []):
            recipient_node = node_for(address_id)
            if recipient_node is None or recipient_node == sender_node:
                continue
            per_day[sender_node, day]['recipients'].add(recipient_node)
            pair = pairs[sender_node, recipient_node]
            pair['messages'] += 1
            pair[kind] += 1
            pair['first'] = apple_seconds if pair['first'] is None else min(pair['first'], apple_seconds)
            pair['last'] = apple_seconds if pair['last'] is None else max(pair['last'], apple_seconds)
            pair['conversations'].add(conversation)
    for (sender_node, recipient_node), pair in pairs.items():
        builder.edge(sender_node, recipient_node, 'EMAILED',
                     dict(messages=pair['messages'], to=pair['to'], cc=pair['cc'], bcc=pair['bcc'],
                          conversations=len(pair['conversations']), first=apple_time(pair['first']) or '',
                          last=apple_time(pair['last']) or '', source='Mail'))
    for (sender_node, day), bucket in sorted(per_day.items(), key=lambda item: item[0][1]):
        builder.edge(sender_node, builder.day(day), 'EMAILED_ON',
                     dict(messages=bucket['messages'], recipients=len(bucket['recipients']), source='Mail'))
    return dict(messages=counted, skipped=skipped, correspondents=len({n for pair in pairs for n in pair}),
                emailed_edges=len(pairs), emailed_on_edges=len(per_day), my_addresses=len(mine))
