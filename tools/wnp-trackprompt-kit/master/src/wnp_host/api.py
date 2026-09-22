from __future__ import annotations
import secrets
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs,unquote
from .model import WNPError,PROTOCOL,encode,require

CODES={'unauthorized':401,'forbidden':403,'not_found':404,'stream_changed':409,'cursor_ahead':409,'identity_conflict':409,'integrity_error':409,'sequence_gap':409,'temporarily_unavailable':503,'limit_exceeded':413,'unsupported_version':400}

def route(store,path,query):
    if path=='/v1/project':return store.project(),'application/json'
    if path=='/v1/node':return store.node(),'application/json'
    if path=='/v1/status':return store.snapshot(),'application/json'
    if path=='/v1/health':
        n=store.node();return {'protocol':PROTOCOL,'service':'ok','stream':n['stream']},'application/json'
    if path=='/v1/sync':
        require(set(query)<={'stream','after','limit','until'},'Unexpected sync query.')
        require('stream' in query and 'after' in query,'stream and after are required.')
        try:
            return store.sync(query['stream'],int(query['after']),int(query.get('limit','500')),int(query['until']) if 'until' in query else None),'application/json'
        except ValueError:raise WNPError('invalid_request','Invalid numeric cursor.') from None
    if path.startswith('/v1/evidence/'):
        require(not query,'Evidence queries are not supported.')
        return store.evidence(unquote(path[len('/v1/evidence/'):])),'application/octet-stream'
    raise WNPError('not_found','Unknown WNP resource.')

class BoundedServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=16
    def __init__(self,*args,**kwargs):self.slots=threading.BoundedSemaphore(16);super().__init__(*args,**kwargs)
    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):self.shutdown_request(request);return
        try:super().process_request(request,client_address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.slots.release()

def make_server(store,token:str,host:str='127.0.0.1',port:int=9761,certfile:str|None=None,keyfile:str|None=None):
    require(host=='127.0.0.1','This reference host binds only to IPv4 loopback. Use an SSH tunnel.','forbidden')
    require(isinstance(token,str) and len(token)>=32 and '\n' not in token and '\r' not in token,'A strong reader token is required.')
    class Handler(BaseHTTPRequestHandler):
        server_version='WNP/1';sys_version='';protocol_version='HTTP/1.0'
        def setup(self):super().setup();self.connection.settimeout(10)
        def log_message(self,*args):pass
        def respond(self,status,body,kind='application/json'):
            raw=body if isinstance(body,bytes) else encode(body)
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Connection','close');self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError,socket.timeout):pass
        def do_GET(self):
            try:
                require(len(self.path)<=2048,'Request target too long.','limit_exceeded')
                credentials=self.headers.get_all('Authorization',[])
                require(len(credentials)==1 and secrets.compare_digest(credentials[0].encode(),('Bearer '+token).encode()),'Authentication required.','unauthorized')
                require(self.headers.get('Content-Length','0')=='0' and not self.headers.get('Transfer-Encoding'),'GET request bodies are not accepted.')
                u=urlsplit(self.path);require(not u.scheme and not u.netloc,'Absolute request URLs are not accepted.')
                raw=parse_qs(u.query,keep_blank_values=True,max_num_fields=8)
                require(all(len(v)==1 for v in raw.values()),'Repeated query parameter.')
                body,kind=route(store,u.path,{k:v[0] for k,v in raw.items()});self.respond(200,body,kind)
            except WNPError as e:self.respond(CODES.get(e.code,400),e.envelope())
            except (ValueError,UnicodeError):self.respond(400,WNPError('invalid_request','Malformed request.').envelope())
            except Exception:self.respond(500,WNPError('internal_error','Internal WNP service error.').envelope())
        def mutation_denied(self):self.respond(405,WNPError('forbidden','WNP/1 does not expose remote mutations.').envelope())
        do_POST=do_PUT=do_PATCH=do_DELETE=do_OPTIONS=mutation_denied
    server=BoundedServer((host,port),Handler)
    if certfile or keyfile:
        require(bool(certfile and keyfile),'Both TLS certificate and key are required.')
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile,keyfile);server.socket=context.wrap_socket(server.socket,server_side=True)
    return server
