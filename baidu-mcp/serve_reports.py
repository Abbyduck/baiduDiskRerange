"""Serve saved reports locally without credentials or cloud mutation routes."""
import argparse

from package_review_server import create_server
from project_paths import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not DATA_DIR.is_dir():
        parser.error('No saved reports. Create an inventory or set BAIDU_NETDISK_DATA_DIR.')
    server = create_server(args.port)
    print(f'Read-only reports: http://127.0.0.1:{args.port}/整包内容介绍.html', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
