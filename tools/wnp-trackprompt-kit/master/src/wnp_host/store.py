from __future__ import annotations
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any
from . import model as m
from .fs import FileLock, atomic_write, reject_link, safe_child

class Store:
    """Small reference journal for local filesystems; not a distributed/shared-drive store."""
    def __init__(self,root:Path|str):
        self.root=Path(root).resolve();self.runtime=self.root/'.wnp'
        reject_link(self.runtime)
        m.require((self.runtime/'node.json').is_file(),'WNP is not initialized.','not_found')
    @classmethod
    def initialize(cls,root:Path|str,project:str,node:str,kind:str='generic'):
        m.identifier(project);m.identifier(node);m.identifier(kind)
        root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True);runtime=root/'.wnp';reject_link(runtime)
        runtime.mkdir(exist_ok=True)
        with FileLock(runtime/'writer.lock'):
            if (runtime/'node.json').exists():
                existing=cls(root)
                n=existing.node();m.require(n['project']==project and n['node']==node,'Existing node identity differs.','identity_conflict')
                return existing
            m.require(not any(p.name!='writer.lock' for p in runtime.iterdir()),'Incomplete initialization: preserve and inspect runtime.','integrity_error')
            project_path=root/'wnp.json';reject_link(project_path)
            descriptor={'protocol':m.PROTOCOL,'project':project,'kind':kind,'capabilities':['events','evidence']}
            if project_path.exists():
                descriptor=m.decode(project_path.read_bytes());m.require(descriptor.get('protocol')==m.PROTOCOL and descriptor.get('project')==project,'Project descriptor conflict.','identity_conflict')
            else:atomic_write(project_path,m.encode(descriptor)+b'\n')
            stream=str(uuid.uuid4());created=m.now()
            initial={'protocol':m.PROTOCOL,'stream':stream,'sequence':0,'timestamp':created,'state':'idle','health':'unknown'}
            atomic_write(runtime/'genesis.json',m.encode(initial)+b'\n')
            atomic_write(runtime/'events.ndjson',b'')
            atomic_write(runtime/'state.json',m.encode(initial)+b'\n')
            (runtime/'evidence').mkdir()
            atomic_write(runtime/'node.json',m.encode({'protocol':m.PROTOCOL,'node':node,'project':project,'stream':stream,'agent':'wnp-host/1.0.0'})+b'\n')
        return cls(root)
    def _read(self,name:str,maximum:int=m.MAX_PAGE):
        p=safe_child(self.runtime,name)
        with p.open('rb') as f:return m.decode(f.read(maximum+1),maximum)
    def project(self):
        p=self.root/'wnp.json';reject_link(p);d=m.decode(p.read_bytes())
        m.require(d.get('protocol')==m.PROTOCOL,'Unsupported project protocol.','unsupported_version');m.identifier(d.get('project'));m.identifier(d.get('kind'))
        caps=d.get('capabilities');m.require(isinstance(caps,list) and 'events' in caps and len(caps)==len(set(caps)),'Invalid capabilities.')
        for cap in caps:m.identifier(cap)
        return d
    def node(self):
        n=self._read('node.json');m.require(n.get('protocol')==m.PROTOCOL,'Unsupported node protocol.','unsupported_version')
        m.identifier(n.get('node'));m.identifier(n.get('project'));m.stream_id(n.get('stream'))
        m.require(n['project']==self.project()['project'],'Project/node mismatch.','integrity_error')
        m.require(isinstance(n.get('agent'),str) and 1<=len(n['agent'])<=128,'Invalid agent.')
        return n
    def _load(self):
        node=self.node();state=self._read('genesis.json');m.snapshot(state,node['stream'])
        m.require(state['sequence']==0,'Genesis must be sequence zero.','integrity_error')
        events=[];states=[dict(state)];seen=set();path=safe_child(self.runtime,'events.ndjson')
        with path.open('rb') as f:
            while True:
                line=f.readline(m.MAX_RECORD+1)
                if not line:break
                m.require(line.endswith(b'\n') and len(line)<=m.MAX_RECORD,'Incomplete/oversized journal tail; local recovery required.','integrity_error')
                e=m.decode(line,m.MAX_RECORD);m.event(e,node['stream'],len(events)+1)
                if 'key' in e:
                    m.require(e['key'] not in seen,'Duplicate producer key.','integrity_error');seen.add(e['key'])
                if 'snapshot' in e:payload=e['snapshot']
                else:payload={k:v for k,v in state.items() if k not in m.META}
                state={'protocol':m.PROTOCOL,'stream':node['stream'],'sequence':e['sequence'],'timestamp':e['timestamp'],**payload}
                events.append(e);states.append(state)
        cached=safe_child(self.runtime,'state.json')
        if cached.exists():
            disk=self._read('state.json');m.snapshot(disk,node['stream'])
            m.require(disk['sequence']<=len(events),'Snapshot is ahead of journal.','integrity_error')
            m.require(disk==states[disk['sequence']],'Snapshot contradicts replay.','integrity_error')
        return events,states
    def events(self):
        with FileLock(self.runtime/'writer.lock'):return self._load()[0]
    def snapshot(self,live:bool=True):
        with FileLock(self.runtime/'writer.lock'):
            state=dict(self._load()[1][-1])
            if live and (self.runtime/'observations.json').exists():
                try:state['observations']=self._read('observations.json')
                except (m.WNPError,OSError):state['observations']={'available':False}
            return state
    def append(self,event_type:str,*,patch:dict|None=None,context:dict|None=None,data:dict|None=None,key:str|None=None,evidence:list|None=None):
        m.identifier(event_type);patch=patch or {};context=context or {};data=data or {};evidence=evidence or []
        m.require(isinstance(patch,dict) and isinstance(context,dict) and isinstance(data,dict),'Invalid event input.')
        m.require(set(context)<= {'run','task','attempt','detail','message'},'Unsupported context key.')
        if key is not None:m.require(isinstance(key,str) and 1<=len(key)<=256,'Invalid producer key.')
        request={'type':event_type,'patch':patch,'context':context,'data':data,'evidence':evidence}
        fingerprint=hashlib.sha256(m.encode(request)).hexdigest()
        with FileLock(self.runtime/'writer.lock'):
            events,states=self._load()
            if key is not None:
                for prior in events:
                    if prior.get('key')==key:
                        m.require(prior.get('request_sha256')==fingerprint,'Producer key reused with different content.','idempotency_conflict');return prior
            payload={k:v for k,v in states[-1].items() if k not in m.META};payload.update(patch);m.state_payload(payload)
            e={'stream':states[-1]['stream'],'sequence':len(events)+1,'timestamp':m.now(),'type':event_type,'snapshot':payload,**context}
            if data:e['data']=data
            if key is not None:e.update(key=key,request_sha256=fingerprint)
            if evidence:
                for ref in evidence:
                    m.evidence_ref(ref);self._read_evidence(ref)
                e['evidence']=evidence
            m.event(e)
            with safe_child(self.runtime,'events.ndjson').open('ab') as f:f.write(m.encode(e)+b'\n');f.flush();os.fsync(f.fileno())
            state={'protocol':m.PROTOCOL,'stream':e['stream'],'sequence':e['sequence'],'timestamp':e['timestamp'],**payload}
            atomic_write(self.runtime/'state.json',m.encode(state)+b'\n')
            return e
    def observe(self,payload:dict):
        m.require(isinstance(payload,dict) and set(payload)<={'worker','provider','metrics','progress','message'},'Unsupported live observation.')
        value={'sampled_at':m.now(),**payload};raw=m.encode(value)
        m.require(len(raw)<=m.MAX_RECORD,'Observation too large.','limit_exceeded')
        with FileLock(self.runtime/'writer.lock'):atomic_write(self.runtime/'observations.json',raw+b'\n')
    def sync(self,stream:str,after:int,limit:int=500,until:int|None=None):
        m.stream_id(stream);m.sequence(after);m.require(type(limit) is int and 1<=limit<=5000,'Invalid page limit.')
        with FileLock(self.runtime/'writer.lock'):
            events,states=self._load();current=len(events)
            m.require(stream==states[-1]['stream'],'Stream changed.','stream_changed')
            head=current if until is None else until;m.sequence(head)
            m.require(after<=head<=current,'Cursor is ahead of available history.','cursor_ahead')
            selected=[];size=0
            for e in events[after:head]:
                n=len(m.encode(e))+1
                if len(selected)>=limit or size+n>m.MAX_PAGE//2:break
                selected.append(e);size+=n
            nxt=selected[-1]['sequence'] if selected else after
            result={'protocol':m.PROTOCOL,'stream':stream,'after':after,'head':head,'events':selected,'more':nxt<head,'next_after':nxt}
            if not result['more']:result['snapshot']=states[head]
            return result
    def register_evidence(self,content:bytes,media_type:str='application/octet-stream'):
        m.require(isinstance(content,bytes) and len(content)<=m.MAX_EVIDENCE,'Evidence too large.','limit_exceeded')
        m.require(isinstance(media_type,str) and len(media_type)<=128 and '\r' not in media_type and '\n' not in media_type,'Invalid media type.')
        sha=hashlib.sha256(content).hexdigest();ref={'path':f'evidence/{sha}.bin','sha256':sha,'bytes':len(content),'media_type':media_type}
        with FileLock(self.runtime/'writer.lock'):
            p=safe_child(self.runtime,ref['path'])
            if p.exists():self._read_evidence(ref)
            else:atomic_write(p,content)
        return ref
    def _read_evidence(self,ref):
        m.require(ref['path'].startswith('evidence/'),'Evidence root required.','forbidden');p=safe_child(self.runtime,ref['path'])
        try:
            with p.open('rb') as f:content=f.read(m.MAX_EVIDENCE+1)
        except FileNotFoundError:raise m.WNPError('not_found','Evidence is unavailable.') from None
        m.require(len(content)<=m.MAX_EVIDENCE,'Evidence too large.','limit_exceeded')
        m.require(hashlib.sha256(content).hexdigest()==ref['sha256'],'Evidence digest mismatch.','integrity_error')
        if 'bytes' in ref:m.require(len(content)==ref['bytes'],'Evidence size mismatch.','integrity_error')
        return content
    def evidence(self,path:str):
        safe_child(self.runtime,path)
        with FileLock(self.runtime/'writer.lock'):
            for e in self._load()[0]:
                for ref in e.get('evidence',[]):
                    if ref['path']==path:return self._read_evidence(ref)
        raise m.WNPError('not_found','No registered evidence at this path.')
    def recover(self):
        with FileLock(self.runtime/'writer.lock'):
            path=safe_child(self.runtime,'events.ndjson');tail=False
            # Only an unterminated final record may be quarantined. Complete bad records remain untouched.
            with path.open('rb') as f:
                start=0; expected=1; stream=self.node()['stream']
                while True:
                    line=f.readline(m.MAX_RECORD+1)
                    if not line:break
                    if not line.endswith(b'\n'):
                        remaining=line+f.read(m.MAX_RECORD+1)
                        m.require(len(remaining)<=m.MAX_RECORD,'Oversized tail requires operator inspection.','integrity_error')
                        quarantine=self.runtime/'recovery'/f'{uuid.uuid4().hex}.tail'
                        atomic_write(quarantine,remaining)
                        with path.open('r+b') as out:out.truncate(start);out.flush();os.fsync(out.fileno())
                        tail=True;break
                    # Validate before allowing later-tail mutation.
                    m.event(m.decode(line,m.MAX_RECORD),stream,expected)
                    expected+=1
                    start=f.tell()
            events,states=self._load()
            atomic_write(self.runtime/'state.json',m.encode(states[-1])+b'\n')
            return {'head':len(events),'tail_quarantined':tail,'stream':states[-1]['stream']}
