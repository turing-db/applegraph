"""macOS AddressBook contacts: enrich people and register their handles."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import tempfile

from .model import Builder, apple_time, is_placeholder, snapshot

RECORD_FIELDS = ['ZUNIQUEID', 'ZFIRSTNAME', 'ZLASTNAME', 'ZMIDDLENAME', 'ZNICKNAME', 'ZORGANIZATION', 'ZJOBTITLE',
                 'ZDEPARTMENT', 'ZBIRTHDAY', 'ZCONTAINERWHERECONTACTISME']


def clean_label(label) -> str:
    match = re.fullmatch(r'_\$!<(.+)>!\$_', label or '')
    return match.group(1) if match else (label or '')


def address_books(path: Path) -> list[Path]:
    """The main AddressBook database plus every per-account source database."""
    books = [path] if path.is_file() else []
    sources = path.parent / 'Sources'
    if sources.is_dir():
        books.extend(sorted(sources.glob('*/AddressBook-v22.abcddb')))
    return books


def add_contacts(builder: Builder, path: Path) -> dict:
    stats = dict(books=0, contacts=0, matched=0, created=0, phones=0, emails=0, me_found=False)
    for book in address_books(path):
        stats['books'] += 1
        with tempfile.TemporaryDirectory(prefix='apple-graph-contacts-') as tmp:
            copy = Path(tmp) / 'AddressBook.abcddb'
            snapshot(book, copy)
            with closing(sqlite3.connect(copy)) as db:
                db.row_factory = sqlite3.Row
                columns = {r[1] for r in db.execute('PRAGMA table_info(ZABCDRECORD)')}
                selected = ', '.join(f'{c}' if c in columns else f'NULL AS {c}' for c in RECORD_FIELDS)
                records = [dict(r) for r in db.execute(f"SELECT Z_PK, {selected} FROM ZABCDRECORD WHERE ZUNIQUEID LIKE '%:ABPerson'")]
                phones, emails, postal = defaultdict(list), defaultdict(list), defaultdict(list)
                for r in db.execute('SELECT ZOWNER, ZFULLNUMBER, ZLABEL FROM ZABCDPHONENUMBER'):
                    phones[r[0]].append((clean_label(r[2]), r[1] or ''))
                for r in db.execute('SELECT ZOWNER, ZADDRESS, ZLABEL FROM ZABCDEMAILADDRESS'):
                    emails[r[0]].append((clean_label(r[2]), r[1] or ''))
                for r in db.execute('SELECT ZOWNER, ZSTREET, ZCITY, ZZIPCODE, ZCOUNTRYNAME, ZLABEL FROM ZABCDPOSTALADDRESS'):
                    postal[r[0]].append((clean_label(r[5]), ', '.join(p for p in r[1:5] if p)))
        for record in records:
            stats['contacts'] += 1
            key = builder.identifier_index.get(('cncontact', record['ZUNIQUEID']))
            name = ' '.join(p for p in (record['ZFIRSTNAME'], record['ZMIDDLENAME'], record['ZLASTNAME']) if p).strip()
            if key is None:
                key = ('contact', 'AddressBook', record['ZUNIQUEID'])
                builder.node(key, ['AppleEntity', 'Person'], name=name or record['ZNICKNAME'] or record['ZORGANIZATION'] or 'Unnamed contact',
                             apple_id=f"AddressBook:{record['ZUNIQUEID']}", entity_type='person', source='AddressBook')
                stats['created'] += 1
            else:
                stats['matched'] += 1
            props = builder.props(key)
            if name and is_placeholder(props.get('name')):
                props['name'] = name
            for column, prop in (('ZFIRSTNAME', 'first_name'), ('ZLASTNAME', 'family_name'), ('ZNICKNAME', 'nickname'),
                                 ('ZORGANIZATION', 'organization'), ('ZJOBTITLE', 'job_title'), ('ZDEPARTMENT', 'department')):
                if record[column] and not props.get(prop):
                    props[prop] = record[column]
            if record['ZBIRTHDAY'] is not None and not props.get('birthday'):
                props['birthday'] = (apple_time(record['ZBIRTHDAY']) or '')[:10]
            owner = record['Z_PK']
            if phones[owner]:
                props['phones'] = ' | '.join(f'{label}: {number}' if label else number for label, number in phones[owner])
                stats['phones'] += len(phones[owner])
                for _, number in phones[owner]:
                    builder.register_handle(number, key)
            if emails[owner]:
                props['emails'] = ' | '.join(f'{label}: {address}' if label else address for label, address in emails[owner])
                stats['emails'] += len(emails[owner])
                for _, address in emails[owner]:
                    builder.register_handle(address, key)
            if postal[owner]:
                props['postal_addresses'] = ' | '.join(f'{label}: {address}' if label else address for label, address in postal[owner])
            props['contact_id'] = record['ZUNIQUEID']
            if record['ZCONTAINERWHERECONTACTISME'] is not None:
                builder.set_me(key)
                stats['me_found'] = True
    return stats
