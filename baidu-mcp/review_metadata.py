"""Manually approve catalog descriptions; never modify Baidu Netdisk files."""
import argparse
from sqlite_inventory import approve_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', required=True, help='Current full cloud path')
    parser.add_argument('--name', dest='display_name')
    parser.add_argument('--category')
    parser.add_argument('--summary')
    parser.add_argument('--purpose')
    args = parser.parse_args()
    changes = {key: getattr(args, key) for key in
               ('display_name', 'category', 'summary', 'purpose')
               if getattr(args, key) is not None}
    if not changes:
        parser.error('Specify at least one of --name, --category, --summary, --purpose')
    ref = approve_metadata(args.path, **changes)
    print(f'Saved local metadata for {ref}. Rebuild catalog to view; no cloud changes.')


if __name__ == '__main__':
    main()
