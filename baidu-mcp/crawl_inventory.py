"""Read-only resumable inventory of the normal Baidu Netdisk file tree."""
import argparse
import concurrent.futures
import datetime as dt
import json
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
from project_paths import DATA_DIR

OUT = DATA_DIR
OUT.mkdir(parents=True, exist_ok=True)
TZ = dt.timezone(dt.timedelta(hours=8))
LIMIT = 1000
local = threading.local()
rate_lock = threading.Lock()
next_request = 0.0
request_interval = 0.28


def now():
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def credential():
    cipher = (ROOT / "baidu-mcp/token.dpapi").read_text(encoding="ascii").strip()
    command = "$ErrorActionPreference = 'Stop'; Import-Module ($PSHOME + '\\Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1'); $s = [Console]::In.ReadToEnd().Trim() | ConvertTo-SecureString; $c = New-Object System.Management.Automation.PSCredential('baidu-mcp', $s); [Console]::Out.Write($c.GetNetworkCredential().Password)"
    result = subprocess.run(
        [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        input=cipher, text=True, capture_output=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Cannot decrypt local Baidu credential")
    return result.stdout.strip()


def request_slot():
    global next_request
    with rate_lock:
        delay = max(0, next_request - time.monotonic())
        next_request = max(next_request, time.monotonic()) + request_interval
    if delay:
        time.sleep(delay)


def fetch_page(path, start, token):
    if not hasattr(local, "session"):
        local.session = requests.Session()
    for attempt in range(5):
        request_slot()
        try:
            response = local.session.get(
                "https://pan.baidu.com/rest/2.0/xpan/file",
                params={"method": "list", "dir": path, "start": start, "limit": LIMIT,
                        "order": "name", "desc": 0, "web": 0, "folder": 0,
                        "showempty": 1, "access_token": token},
                timeout=(10, 35),
            )
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError("Transient HTTP " + str(response.status_code))
            if response.status_code != 200:
                return None, "HTTP " + str(response.status_code)
            data = response.json()
            errno = data.get("errno", data.get("error_code"))
            if errno != 0:
                if errno in (-7, 4, 31034, 31065):
                    raise RuntimeError("Transient API errno " + str(errno))
                return None, "API errno " + str(errno)
            if not isinstance(data.get("list"), list):
                return None, "API response has no list"
            return data["list"], None
        except Exception as exc:
            # Exception messages can embed credential-bearing request URLs.
            if attempt == 4:
                return None, "Read failure: " + type(exc).__name__
            time.sleep(min(2 ** attempt, 8))


def list_directory(path, token):
    items, seen, pages, start = [], set(), 0, 0
    while True:
        page, error = fetch_page(path, start, token)
        if error:
            return {"path": path, "error": error, "pages": pages, "items": items}
        pages += 1
        fresh = [x for x in page if str(x.get("fs_id")) not in seen]
        if page and not fresh:
            return {"path": path, "error": "Repeated pagination page", "pages": pages, "items": items}
        for item in fresh:
            seen.add(str(item.get("fs_id")))
            if not isinstance(item.get("path"), str) or item.get("isdir") not in (0, 1):
                return {"path": path, "error": "Malformed file metadata", "pages": pages, "items": items}
            items.append(item)
        start += len(page)
        if len(page) < LIMIT:
            return {"path": path, "error": None, "pages": pages, "items": items}


def connect():
    db = sqlite3.connect(OUT / "inventory.sqlite")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS nodes (
        fsid TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, parent TEXT NOT NULL,
        name TEXT NOT NULL, isdir INTEGER NOT NULL, size INTEGER NOT NULL,
        category INTEGER, md5 TEXT, mtime INTEGER, ctime INTEGER,
        seen_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent);
      CREATE TABLE IF NOT EXISTS directories (
        path TEXT PRIMARY KEY, depth INTEGER, status TEXT NOT NULL DEFAULT 'pending',
        error TEXT, pages INTEGER DEFAULT 0, child_files INTEGER DEFAULT 0,
        child_dirs INTEGER DEFAULT 0, listed_at TEXT
      );
      CREATE TABLE IF NOT EXISTS run_info(key TEXT PRIMARY KEY, value TEXT);
    """)
    db.execute("UPDATE directories SET status='pending' WHERE status='running'")
    db.execute("INSERT OR IGNORE INTO directories(path,depth) VALUES('/',0)")
    db.execute("INSERT OR IGNORE INTO run_info VALUES('started_at',?)", (now(),))
    db.commit()
    return db


def progress(db, finished=False):
    statuses = dict(db.execute("SELECT status,count(*) FROM directories GROUP BY status"))
    total, folders, size = db.execute("SELECT count(*),coalesce(sum(isdir),0),coalesce(sum(CASE WHEN isdir=0 THEN size ELSE 0 END),0) FROM nodes").fetchone()
    result = {"updatedAt": now(), "finished": finished, "directoryStatuses": statuses,
              "directoriesDiscoveredIncludingRoot": sum(statuses.values()),
              "folders": folders, "files": total - folders, "bytes": size,
              "complete": finished and not statuses.get("error") and not statuses.get("pending") and not statuses.get("running")}
    (OUT / "progress.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def store_result(db, result):
    path, stamp = result["path"], now()
    directories = files = 0
    for item in result["items"]:
        isdir = item["isdir"]
        db.execute("""INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(fsid) DO UPDATE SET path=excluded.path,parent=excluded.parent,
          name=excluded.name,isdir=excluded.isdir,size=excluded.size,category=excluded.category,
          md5=excluded.md5,mtime=excluded.mtime,ctime=excluded.ctime,seen_at=excluded.seen_at""",
          (str(item["fs_id"]), item["path"], path, item.get("server_filename", item["path"].rsplit("/",1)[-1]),
           isdir, item.get("size",0), item.get("category"), item.get("md5", ""),
           item.get("server_mtime"), item.get("server_ctime"), stamp))
        if isdir:
            directories += 1
            db.execute("INSERT OR IGNORE INTO directories(path,depth) VALUES(?,?)", (item["path"], item["path"].count("/")))
        else:
            files += 1
    db.execute("UPDATE directories SET status=?,error=?,pages=?,child_files=?,child_dirs=?,listed_at=? WHERE path=?",
               ("error" if result["error"] else "done", result["error"], result["pages"], files, directories, stamp, path))
    db.commit()


def main():
    global request_interval
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--interval", type=float, default=0.28)
    args = parser.parse_args()
    request_interval=max(0.15,args.interval)
    token, db = credential(), connect()
    if args.retry_errors:
        db.execute("UPDATE directories SET status='pending' WHERE status='error'")
        db.commit()
    print(json.dumps(progress(db), ensure_ascii=False), flush=True)
    last_log, completed = time.monotonic(), 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        active = {}
        while True:
            slots = args.workers - len(active)
            if slots:
                paths = [row[0] for row in db.execute("SELECT path FROM directories WHERE status='pending' ORDER BY depth,path LIMIT ?", (slots,))]
                for path in paths:
                    db.execute("UPDATE directories SET status='running' WHERE path=?", (path,))
                    active[pool.submit(list_directory, path, token)] = path
                db.commit()
            if not active:
                break
            done, _ = concurrent.futures.wait(active, timeout=5, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                path = active.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"path": path, "error": type(exc).__name__, "pages": 0, "items": []}
                store_result(db, result)
                completed += 1
                if result["error"]:
                    print(json.dumps({"directoryError": path, "reason": result["error"]}, ensure_ascii=False), flush=True)
            if time.monotonic() - last_log > 20 or completed and completed % 40 == 0:
                print(json.dumps(progress(db), ensure_ascii=False), flush=True)
                last_log = time.monotonic()
    final = progress(db, finished=True)
    db.execute("INSERT OR REPLACE INTO run_info VALUES('finished_at',?)", (now(),))
    db.commit()
    columns = [row[1] for row in db.execute("PRAGMA table_info(nodes)")]
    with (OUT / "all-files.jsonl").open("w", encoding="utf-8") as output:
        for row in db.execute("SELECT * FROM nodes ORDER BY path"):
            output.write(json.dumps(dict(zip(columns,row)),ensure_ascii=False) + "\n")
    print(json.dumps(final, ensure_ascii=False), flush=True)
    db.close()


if __name__ == "__main__":
    main()
