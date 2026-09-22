from __future__ import annotations
import argparse
import base64
import os
import secrets
import sys
from pathlib import Path
from .store import Store
from .model import WNPError,decode,encode,require

def parser():
    p=argparse.ArgumentParser(description='WNP/1 local host and read-only client interface.')
    p.add_argument('--root',type=Path,required=True);p.add_argument('--json',action='store_true')
    sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--project',required=True);init.add_argument('--node',required=True);init.add_argument('--kind',default='generic')
    for c in ('project','node','status','recover','rpc','token-init'):sub.add_parser(c)
    sync=sub.add_parser('sync');sync.add_argument('--stream',required=True);sync.add_argument('--after',type=int,required=True);sync.add_argument('--limit',type=int,default=500);sync.add_argument('--until',type=int)
    e=sub.add_parser('evidence');e.add_argument('path')
    a=sub.add_parser('append');a.add_argument('--file',type=Path,required=True)
    o=sub.add_parser('observe');o.add_argument('--file',type=Path,required=True)
    serve=sub.add_parser('serve');serve.add_argument('--port',type=int,default=9761);serve.add_argument('--token-file',type=Path);serve.add_argument('--certfile');serve.add_argument('--keyfile')
    return p

def rpc(store):
    # Intended for a fixed, operator-installed SSH forced command; no remote command strings accepted.
    req=decode(sys.stdin.buffer.readline(16385),16384);require(isinstance(req,dict),'RPC object required.')
    op=req.get('operation');require(op in ('project','node','status','sync','evidence'),'Read-only RPC operation required.','forbidden')
    allowed={'operation'}|({'stream','after','limit','until'} if op=='sync' else {'path'} if op=='evidence' else set())
    require(set(req)<=allowed,'Unexpected RPC attribute.')
    if op in ('project','node'):return getattr(store,op)()
    if op=='status':return store.snapshot()
    if op=='sync':return store.sync(req.get('stream'),req.get('after'),req.get('limit',500),req.get('until'))
    data=store.evidence(req.get('path'));return {'encoding':'base64','data':base64.b64encode(data).decode()}

def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.command=='init':result=Store.initialize(args.root,args.project,args.node,args.kind).node()
        else:
            store=Store(args.root)
            if args.command in ('project','node'):result=getattr(store,args.command)()
            elif args.command=='status':result=store.snapshot()
            elif args.command=='sync':result=store.sync(args.stream,args.after,args.limit,args.until)
            elif args.command=='recover':result=store.recover()
            elif args.command=='rpc':result=rpc(store)
            elif args.command=='append':
                request=decode(args.file.read_bytes());result=store.append(request.pop('type'),**request)
            elif args.command=='observe':store.observe(decode(args.file.read_bytes()));result={'observed':True}
            elif args.command=='evidence':sys.stdout.buffer.write(store.evidence(args.path));return 0
            elif args.command=='token-init':
                path=store.runtime/'auth/read.token';path.parent.mkdir(mode=0o700,exist_ok=True)
                # Never display the credential; owner-only mode is enforced on POSIX.
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(secrets.token_urlsafe(32)+'\n')
                result={'created':True,'token_file':str(path),'note':'Keep this private. On Windows verify NTFS ACLs.'}
            elif args.command=='serve':
                from .api import make_server
                token_path=args.token_file or store.runtime/'auth/read.token'
                token=token_path.read_text(encoding='utf-8').strip()
                server=make_server(store,token,port=args.port,certfile=args.certfile,keyfile=args.keyfile)
                print('WNP read-only API listening on loopback port '+str(args.port),flush=True)
                try:server.serve_forever()
                except KeyboardInterrupt:pass
                finally:server.server_close()
                return 0
        if args.json or args.command=='rpc':print(encode(result).decode())
        elif args.command=='status':
            for key in ('state','health','run','task','detail','sequence','timestamp'):print(f'{key:12} {result.get(key,"-")}')
        else:print(encode(result).decode())
        return 0
    except WNPError as e:print(encode(e.envelope()).decode(),file=sys.stderr);return 2
    except (OSError,KeyError,TypeError):print(encode(WNPError('local_io_error','Local input/file operation failed; check paths and permissions.').envelope()).decode(),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
