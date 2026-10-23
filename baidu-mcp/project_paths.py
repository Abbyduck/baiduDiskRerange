"""Shared locations for private runtime data; no computer-specific user path."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resolve_data_dir(root=ROOT, environ=None):
    environ = os.environ if environ is None else environ
    root = Path(root).resolve()
    configured = environ.get('BAIDU_NETDISK_DATA_DIR', '').strip()
    if configured:
        path = Path(configured).expanduser()
        return (path if path.is_absolute() else root / path).resolve()
    current = root / 'local-data' / 'inventory'
    legacy = root / '盘点_2026-10-20'
    # Keep existing checkouts usable before moving their saved data.
    return legacy if legacy.is_dir() and not current.is_dir() else current


DATA_DIR = resolve_data_dir()
