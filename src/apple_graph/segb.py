"""Reader for Apple Biome SEGB v2 stream files and their protobuf payloads.

A SEGB v2 file starts with a 32-byte header (magic ``SEGB``, record count,
creation time) followed by records, each with an 8-byte header. A footer at the
end of the file holds one 16-byte index entry per record, newest last, giving
the record's end offset relative to the header, its state and a timestamp.
Payloads are protobuf messages whose schemas Apple does not publish; the
decoder below keeps field numbers and lets callers map them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct

HEADER = struct.Struct('<4sIdIIII')
INDEX = struct.Struct('<IId')
RECORD_HEADER = 8
STATE_WRITTEN = 1


@dataclass
class Record:
    state: int
    timestamp: float  # Seconds since 2001-01-01 UTC.
    data: bytes


def read_segb(path: Path) -> list[Record]:
    data = Path(path).read_bytes()
    if len(data) < HEADER.size or data[:4] != b'SEGB':
        return []
    _, count, *_ = HEADER.unpack_from(data)
    records, start = [], HEADER.size
    for index in range(1, count + 1):
        offset = len(data) - INDEX.size * index
        if offset < HEADER.size:
            break
        end, state, timestamp = INDEX.unpack_from(data, offset)
        end += HEADER.size
        if end <= start + RECORD_HEADER or end > len(data):
            break
        records.append(Record(state, timestamp, data[start + RECORD_HEADER:end]))
        start = (end + 3) & ~3  # Records are 4-byte aligned.
    return records


def _varint(buffer: bytes, position: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = buffer[position]
        position += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, position


def parse_message(buffer: bytes, depth: int = 0) -> list[tuple[int, str, object]]:
    """Decode protobuf wire format into (field, kind, value) triples.

    Kinds are ``varint``, ``fixed64`` (as double), ``fixed32`` (as float),
    ``msg`` (nested list), ``str`` or ``bytes``. Length-delimited fields are
    treated as strings when they decode as printable UTF-8, otherwise as nested
    messages when they parse cleanly, otherwise as bytes.
    """
    position, fields = 0, []
    while position < len(buffer):
        tag, position = _varint(buffer, position)
        field, wire = tag >> 3, tag & 7
        if field == 0 or field > 10000:
            raise ValueError('implausible field number')
        if wire == 0:
            value, position = _varint(buffer, position)
            fields.append((field, 'varint', value))
        elif wire == 1:
            fields.append((field, 'fixed64', struct.unpack_from('<d', buffer, position)[0]))
            position += 8
        elif wire == 5:
            fields.append((field, 'fixed32', struct.unpack_from('<f', buffer, position)[0]))
            position += 4
        elif wire == 2:
            length, position = _varint(buffer, position)
            chunk = buffer[position:position + length]
            position += length
            if len(chunk) != length:
                raise ValueError('truncated field')
            fields.append((field, *_decode_chunk(chunk, depth)))
        else:
            raise ValueError(f'unsupported wire type {wire}')
    return fields


def _decode_chunk(chunk: bytes, depth: int) -> tuple[str, object]:
    try:
        text = chunk.decode('utf-8')
    except UnicodeDecodeError:
        text = None
    if text is not None and (text == '' or (text.isprintable() and len(text) < 2000)):
        return 'str', text
    if depth < 8 and chunk:
        try:
            return 'msg', parse_message(chunk, depth + 1)
        except (ValueError, IndexError, struct.error):
            pass
    if text is not None:
        return 'str', text
    return 'bytes', chunk


def values(fields: list, number: int, kind: str | None = None) -> list:
    return [value for field, field_kind, value in fields if field == number and (kind is None or field_kind == kind)]


def first(fields: list, number: int, kind: str | None = None, default=None):
    found = values(fields, number, kind)
    return found[0] if found else default


def is_bookkeeping(fields: list) -> bool:
    """Biome sync bookkeeping records start with a numeric string in field 1."""
    return bool(fields) and fields[0][0] == 1 and fields[0][1] == 'str' and str(fields[0][2]).isdigit()


def stream_events(stream_dir: Path, origin: str | None = None):
    """Yield (origin, device_id, fields) for written event records of one stream.

    ``origin`` is ``local`` for records written on this machine or ``remote``
    for records synced from another device, whose identifier is ``device_id``.
    """
    if not stream_dir.is_dir():
        return
    for path in sorted(stream_dir.rglob('*')):
        if not path.is_file() or path.name == 'lock':
            continue
        relative = path.relative_to(stream_dir).parts
        record_origin = relative[0] if relative and relative[0] in ('local', 'remote') else 'local'
        device_id = relative[1] if record_origin == 'remote' and len(relative) > 2 else ''
        if origin is not None and record_origin != origin:
            continue
        for record in read_segb(path):
            if record.state != STATE_WRITTEN:
                continue
            try:
                fields = parse_message(record.data)
            except (ValueError, IndexError, struct.error):
                continue
            if is_bookkeeping(fields):
                continue
            yield record_origin, device_id, fields
