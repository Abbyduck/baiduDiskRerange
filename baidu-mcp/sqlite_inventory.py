"""SQLite-backed complete catalog snapshots and manual package annotations.

Uses saved, verified JSON scans; never requests or changes Baidu Netdisk files.
Original JSON scans and existing audit records remain untouched.
"""
import datetime as dt
import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from project_paths import DATA_DIR

SCAN_ID = re.compile(r"scan-[a-f0-9]+\Z")
PRIORITY = re.compile(r"^([01])[_-]\s*")


def connect(data_dir=DATA_DIR):
    db = Path(data_dir) / 'inventory.sqlite'
    db.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(db)
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys=ON')
    cx.execute('PRAGMA journal_mode=WAL')
    cx.executescript("""
        CREATE TABLE IF NOT EXISTS catalog_scans (
            scan_id TEXT PRIMARY KEY,
            finished_at TEXT,
            entries INTEGER NOT NULL,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS catalog_entries (
            scan_id TEXT NOT NULL REFERENCES catalog_scans(scan_id),
            fsid TEXT, path TEXT NOT NULL, parent TEXT NOT NULL,
            name TEXT NOT NULL, isdir INTEGER NOT NULL,
            size INTEGER NOT NULL, md5 TEXT, mtime INTEGER, ctime INTEGER,
            PRIMARY KEY (scan_id, path)
        );
        CREATE INDEX IF NOT EXISTS idx_catalog_entries_id ON catalog_entries(scan_id, fsid);
        CREATE INDEX IF NOT EXISTS idx_catalog_entries_parent ON catalog_entries(scan_id, parent);
        CREATE TABLE IF NOT EXISTS package_reviews (
            package_id TEXT PRIMARY KEY,
            fsid TEXT UNIQUE,
            current_path TEXT NOT NULL,
            original_name TEXT NOT NULL,
            priority INTEGER CHECK(priority IN (0,1) OR priority IS NULL),
            display_name TEXT,
            category TEXT,
            summary TEXT,
            purpose TEXT,
            reviewed_at TEXT,
            last_seen_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_package_reviews_path ON package_reviews(current_path);
    """)
    return cx


def _read_scan_header(data_dir):
    path = Path(data_dir) / '实时索引/latest-scan.json'
    scan = json.loads(path.read_text(encoding='utf-8'))
    if (scan.get('status') != 'complete'
            or not SCAN_ID.fullmatch(str(scan.get('directory', '')))
            or type(scan.get('pages')) is not int or scan['pages'] < 1
            or type(scan.get('entries')) is not int or scan['entries'] < 0):
        raise ValueError('Saved live scan is not complete. Previous SQLite snapshots were preserved.')
    return scan


def _entry(raw):
    path = raw['path']
    if not isinstance(path, str) or not path.startswith('/') or path == '/' or raw.get('isdir') not in (0, 1):
        raise ValueError('Invalid entry in saved scan; import aborted.')
    parent = path.rsplit('/', 1)[0] or '/'
    name = raw.get('name', raw.get('server_filename', path.rsplit('/', 1)[-1]))
    if not isinstance(name, str) or not name:
        raise ValueError('Entry is missing its filename.')
    return (str(raw.get('fsid') or raw.get('fs_id') or '') or None,
            path, parent, name, raw['isdir'], int(raw.get('size') or 0),
            raw.get('md5') or '', raw.get('mtime', raw.get('server_mtime')),
            raw.get('ctime', raw.get('server_ctime')))


def _validate_tree(cx, scan_id):
    missing = cx.execute("""SELECT e.path FROM catalog_entries e
        WHERE e.scan_id=? AND e.parent<>'/' AND NOT EXISTS (
          SELECT 1 FROM catalog_entries d
          WHERE d.scan_id=e.scan_id AND d.path=e.parent AND d.isdir=1)
        LIMIT 1""", (scan_id,)).fetchone()
    if missing:
        raise ValueError('Complete snapshot has a missing directory parent.')
    duplicate = cx.execute("""SELECT fsid FROM catalog_entries WHERE scan_id=?
        AND fsid IS NOT NULL GROUP BY fsid HAVING COUNT(*) > 1 LIMIT 1""",
        (scan_id,)).fetchone()
    if duplicate:
        raise ValueError('Complete snapshot contains duplicate cloud file IDs.')


def load_complete_snapshot(data_dir=DATA_DIR):
    """Import a complete saved scan transactionally; generate the catalog from SQLite."""
    scan = _read_scan_header(data_dir)
    scan_id = scan['directory']
    with closing(connect(data_dir)) as cx:
        exists = cx.execute('SELECT entries FROM catalog_scans WHERE scan_id=?', (scan_id,)).fetchone()
        if exists is None:
            with cx:
                cx.execute('INSERT INTO catalog_scans VALUES (?,?,?,?)', (
                    scan_id, scan.get('completedAt') or scan.get('finishedAt'),
                    scan['entries'], dt.datetime.now(dt.timezone.utc).isoformat()))
                count = 0
                for page in range(1, scan['pages'] + 1):
                    filename = Path(data_dir) / '实时索引' / scan_id / f'page-{page:05d}.json'
                    rows = json.loads(filename.read_text(encoding='utf-8'))['items']
                    if not isinstance(rows, list):
                        raise ValueError('Malformed saved scan page.')
                    values = [(scan_id, *_entry(item)) for item in rows]
                    cx.executemany("""INSERT INTO catalog_entries
                        (scan_id,fsid,path,parent,name,isdir,size,md5,mtime,ctime)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""", values)
                    count += len(values)
                if count != scan['entries']:
                    raise ValueError('Saved scan entry count mismatch; import rolled back.')
                _validate_tree(cx, scan_id)
        elif exists['entries'] != scan['entries']:
            raise ValueError('Saved scan identity was reused with a different entry count.')
        _validate_tree(cx, scan_id)
        items = [dict(r) for r in cx.execute("""
            SELECT fsid,path,parent,name,isdir,size,md5,mtime,ctime
            FROM catalog_entries WHERE scan_id=? ORDER BY path""", (scan_id,))]
        if len(items) != scan['entries']:
            raise ValueError('SQLite catalog snapshot is inconsistent; no report generated.')
    return scan, items


def snapshot_diff(data_dir=DATA_DIR, scan_id=None):
    """Entry-level diff (not logical packages); parent moves count all children."""
    with closing(connect(data_dir)) as cx:
        scans = [row['scan_id'] for row in cx.execute(
            'SELECT scan_id FROM catalog_scans ORDER BY rowid DESC')]
        current = scan_id or (scans[0] if scans else None)
        if current not in scans:
            raise ValueError('Unknown completed scan.')
        index = scans.index(current)
        previous = scans[index + 1] if index + 1 < len(scans) else None
        if previous is None:
            return {'previousScanId': None, 'added': 0, 'removed': 0,
                    'movedOrRenamed': 0, 'modified': 0}

        def collect(sid):
            rows = cx.execute('SELECT fsid,path,size,md5,isdir FROM catalog_entries WHERE scan_id=?', (sid,))
            return {(f'id:{r["fsid"]}' if r['fsid'] else f'path:{r["path"]}'): r for r in rows}

        before, after = collect(previous), collect(current)
        shared = set(before) & set(after)
        return {'previousScanId': previous,
                'added': len(set(after) - set(before)),
                'removed': len(set(before) - set(after)),
                'movedOrRenamed': sum(before[k]['path'] != after[k]['path'] for k in shared),
                'modified': sum((before[k]['size'], before[k]['md5'], before[k]['isdir']) !=
                                (after[k]['size'], after[k]['md5'], after[k]['isdir']) for k in shared)}


def observe_packages(packages, data_dir=DATA_DIR):
    """Preserve approved metadata across path changes when the cloud fsid is stable."""
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    with closing(connect(data_dir)) as cx:
        with cx:
            for item in packages:
                path = item['path']
                fsid = str(item.get('fsid') or '') or None
                row = (cx.execute('SELECT * FROM package_reviews WHERE fsid=?', (fsid,)).fetchone()
                       if fsid else cx.execute("""SELECT * FROM package_reviews
                           WHERE fsid IS NULL AND current_path=? ORDER BY rowid DESC LIMIT 1""", (path,)).fetchone())
                match = PRIORITY.match(item['title'])
                priority = int(match.group(1)) if match else None
                if row is None:
                    package_id = uuid.uuid4().hex
                    cx.execute("""INSERT INTO package_reviews
                        (package_id,fsid,current_path,original_name,priority,last_seen_at)
                        VALUES (?,?,?,?,?,?)""", (package_id,fsid,path,item['title'],priority,stamp))
                    row = cx.execute('SELECT * FROM package_reviews WHERE package_id=?', (package_id,)).fetchone()
                else:
                    cx.execute("""UPDATE package_reviews SET current_path=?,original_name=?,
                        priority=?,last_seen_at=? WHERE package_id=?""",
                        (path,item['title'],priority,stamp,row['package_id']))
                item['id'] = 'B' + row['package_id'][:12]
                item['priority'] = priority
                item['manuallyReviewed'] = bool(row['reviewed_at'])
                for key, column in (('displayTitle','display_name'),('category','category'),
                                    ('summary','summary'),('purpose','purpose')):
                    if row[column] is not None:
                        item[key] = row[column]
                if row['reviewed_at']:
                    item['notes'].append('已复用 SQLite 中人工确认的内容说明；云盘位置按本次扫描更新。')
    return packages


def approve_metadata(path, data_dir=DATA_DIR, **updates):
    """Approve catalog text in SQLite; never change the cloud filename or path."""
    allowed = {'display_name','category','summary','purpose'}
    if not updates or set(updates) - allowed:
        raise ValueError('No valid metadata updates.')
    if any(value is not None and (not isinstance(value, str) or not value.strip())
           for value in updates.values()):
        raise ValueError('Metadata values must be nonempty text.')
    with closing(connect(data_dir)) as cx:
        with cx:
            row = cx.execute("""SELECT package_id FROM package_reviews WHERE current_path=?
                ORDER BY last_seen_at DESC, rowid DESC LIMIT 1""", (path,)).fetchone()
            if row is None:
                raise ValueError('Unknown package. Rebuild the catalog before approving.')
            assignments = ','.join(f'{column}=?' for column in updates)
            cx.execute(f"""UPDATE package_reviews SET {assignments},reviewed_at=?
                WHERE package_id=?""", (*updates.values(),dt.datetime.now(dt.timezone.utc).isoformat(),row['package_id']))
            return row['package_id']
