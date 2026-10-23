"""Read-only loopback server used while deletion is locked. No write routes."""
import json
import mimetypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,unquote

from project_paths import DATA_DIR

OUT = DATA_DIR


def create_server(port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,data,mime='application/json; charset=utf-8'):
            if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        def do_POST(self):self.send(403,{'error':'所有删除入口已停用；仅允许恢复及整包边界只读核对。'})
        def do_GET(self):
            if self.headers.get('Host')!=f'127.0.0.1:{port}':return self.send(403,{'error':'仅允许本机访问。'})
            path=unquote(urlsplit(self.path).path)
            if path=='/api/health':return self.send(200,{'deletionDisabled':True,'mode':'package-review-only'})
            if path=='/api/state':
                f=OUT/'恢复记录/完整恢复核验.json'
                return self.send(200,{'deletionDisabled':True,'recovery':json.loads(f.read_text(encoding='utf-8')) if f.exists() else {'status':'checking'}})
            name=path.lstrip('/') or '文件夹去重清单.html'
            if '/' in name or '\\' in name or Path(name).suffix not in ('.html','.json','.md','.png'):
                return self.send(404,{'error':'未找到文件。'})
            target=OUT/name
            if not target.is_file():return self.send(404,{'error':'未找到文件。'})
            return self.send(200,target.read_bytes(),mimetypes.guess_type(name)[0] or 'application/octet-stream')
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)
