"""Build the offline, interactive proposal from the completed inventory snapshot."""
import json
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
from project_paths import DATA_DIR

OUT = DATA_DIR


def load(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


def main():
    summary = load('summary.json')
    assert summary['complete'], 'A completed inventory is required.'
    units = [json.loads(line) for line in (OUT / 'resource-units.jsonl').read_text(encoding='utf-8').splitlines()]
    destinations = load('destination-summary.json')
    photos = load('photo-groups.json')
    assert sum(x['files'] for x in units) == sum(x['files'] for x in destinations) == summary['files']
    assert sum(x['files'] for x in photos) == summary['photoGroupFiles']
    # This payload is deliberately restricted to the report's non-secret metadata.
    payload = {'summary': {k: summary[k] for k in [
        'files', 'folders', 'bytes', 'photoGroups', 'photoGroupFiles',
        'nonPhotoFiles', 'zeroByteFiles', 'archives', 'emptyLeafDirectories',
        'duplicateGroups', 'duplicateExtraCopies', 'duplicateCandidateExtraBytes']},
        'units': units, 'destinations': destinations, 'photos': photos}
    serialized = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    md = (OUT / '目录重构建议.md').read_text(encoding='utf-8')
    report = MarkdownIt('default', {'html': False, 'linkify': False}).render(md)
    page = (ROOT / 'baidu-mcp' / 'proposal-template.html').read_text(encoding='utf-8')
    page = page.replace('%%PAYLOAD%%', serialized).replace('%%REPORT%%', report)
    assert '%%PAYLOAD%%' not in page and '%%REPORT%%' not in page
    target = OUT / '目录重构建议.html'
    target.write_text(page, encoding='utf-8')
    print(f'Built {target.name}: {target.stat().st_size:,} bytes; {len(units)} resource units; {len(destinations)} destination groups.')


if __name__ == '__main__':
    main()
