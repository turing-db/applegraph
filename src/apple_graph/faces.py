"""Find the photos behind a face cluster and reveal them in the Photos app."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess

from .model import apple_time

DEFAULT_LIBRARY = Path.home() / 'Pictures/Photos Library.photoslibrary'
DEFAULT_SOURCE = Path.home() / 'Library/IntelligencePlatform'


def photos_uuid_for_subject(source: Path, subject: str) -> str | None:
    """Resolve an Apple graph subject id to its Photos person UUID through its identifier facts."""
    graph, ontology = source / 'graph.db', source / 'ontology.db'
    if not graph.is_file() or not ontology.is_file():
        return None
    with closing(sqlite3.connect(ontology.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        codes = {label: code for code, label in db.execute("SELECT id, label FROM predicate WHERE label IN ('identifier type', 'identifier id')")}
    if len(codes) < 2:
        return None
    with closing(sqlite3.connect(graph.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        for table in ('stable_graph', 'event_graph', 'expired_stable_graph', 'expired_event_graph'):
            try:
                rows = db.execute(f'SELECT relationshipId, relationshipPredicate, object FROM {table} WHERE subject = ? AND relationshipPredicate IN (?, ?)',
                                  (int(subject), codes['identifier type'], codes['identifier id'])).fetchall()
            except (sqlite3.Error, ValueError):
                continue
            groups = {}
            for rel_id, predicate, value in rows:
                groups.setdefault(rel_id, {})[predicate] = str(value)
            for group in groups.values():
                if group.get(codes['identifier type'], '').lower() == 'phperson' and group.get(codes['identifier id']):
                    return group[codes['identifier id']].split('/')[0]
    return None


def find_person(library: Path, identifier: str, limit: int = 12, source: Path = DEFAULT_SOURCE) -> dict | None:
    """Assets for a face cluster, found by Apple graph id (ZMDID) or Photos person UUID.

    The library database is opened read-only without copying; this is a quick lookup, not an import.
    """
    database = library / 'database/Photos.sqlite'
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        db.row_factory = sqlite3.Row
        # Photos stores the Apple graph subject id as "md:<id>".
        mdid = identifier if identifier.startswith('md:') else f'md:{identifier}'
        person = db.execute('SELECT Z_PK, ZPERSONUUID, ZMDID, ZFULLNAME, ZFACECOUNT, ZKEYFACE FROM ZPERSON WHERE ZMDID = ? OR ZPERSONUUID = ?',
                            (mdid, identifier)).fetchone()
        if person is None and identifier.isdigit():
            uuid = photos_uuid_for_subject(source, identifier)
            if uuid:
                person = db.execute('SELECT Z_PK, ZPERSONUUID, ZMDID, ZFULLNAME, ZFACECOUNT, ZKEYFACE FROM ZPERSON WHERE ZPERSONUUID = ?', (uuid,)).fetchone()
        if person is None:
            return None
        assets = db.execute('''SELECT a.ZUUID AS uuid, a.ZFILENAME AS filename, a.ZDIRECTORY AS directory, a.ZDATECREATED AS created,
                                      f.ZSIZE AS face_size, f.ZQUALITY AS quality, f.Z_PK = ? AS key_face
                               FROM ZDETECTEDFACE f JOIN ZASSET a ON a.Z_PK = f.ZASSETFORFACE
                               WHERE f.ZPERSONFORFACE = ? AND a.ZTRASHEDSTATE = 0
                               ORDER BY key_face DESC, f.ZSIZE DESC, f.ZQUALITY DESC LIMIT ?''',
                            (person['ZKEYFACE'], person['Z_PK'], limit)).fetchall()
    rows = []
    for asset in assets:
        original = library / 'originals' / (asset['directory'] or '') / (asset['filename'] or '')
        rows.append(dict(uuid=asset['uuid'], taken=(apple_time(asset['created']) or '')[:16].replace('T', ' '),
                         key_face=bool(asset['key_face']), face_size=round(asset['face_size'] or 0, 3),
                         original=str(original) if original.is_file() else ''))
    return dict(apple_id=identifier if identifier.isdigit() else (person['ZMDID'] or '').removeprefix('md:'), photos_person_uuid=person['ZPERSONUUID'] or '', name=person['ZFULLNAME'] or '',
                faces=person['ZFACECOUNT'] or 0, assets=rows)


def reveal(uuid: str) -> None:
    """Select the asset in the Photos app so the person can be seen and named there."""
    script = f'tell application "Photos"\nactivate\nspotlight media item id "{uuid}/L0/001"\nend tell'
    subprocess.run(['osascript', '-e', script], check=False)


def main(args) -> None:
    library = args.library.expanduser()
    result = find_person(library, args.identifier, args.limit, args.source.expanduser())
    if result is None:
        raise ValueError(f'No Photos face cluster has id {args.identifier}')
    print(f"cluster {result['apple_id'] or result['photos_person_uuid']}: {result['faces']} faces, name {result['name'] or '(unnamed)'}")
    for row in result['assets']:
        marker = 'key' if row['key_face'] else '   '
        print(f"  {marker} {row['taken']}  face {row['face_size']:.3f}  {row['uuid']}  {'local' if row['original'] else 'iCloud only'}")
    if args.reveal and result['assets']:
        for row in result['assets'][:args.reveal]:
            reveal(row['uuid'])
    if args.open:
        local = [row['original'] for row in result['assets'] if row['original']][:args.open]
        if local:
            subprocess.run(['open', *local], check=False)
        else:
            print('  no originals stored locally; use --reveal to show them in Photos')
