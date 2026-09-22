from __future__ import annotations
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

PROTOCOL = 'wnp/1'
MAX_SEQUENCE = 2**53 - 1
MAX_RECORD = 65536
MAX_PAGE = 1048576
MAX_EVIDENCE = 8388608
STATES = {'idle','queued','running','waiting','paused','complete','failed','degraded','unknown'}
HEALTH = {'ok','degraded','error','unknown'}
IDENT = re.compile(r'^[a-z0-9][a-z0-9._-]{0,127}$')
HEX = re.compile(r'^[0-9a-f]{64}$')
META = {'protocol','stream','sequence','timestamp','observations','metrics'}

class WNPError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
    def envelope(self) -> dict:
        return {'protocol':PROTOCOL,'error':{'code':self.code,'message':self.message,'retryable':self.retryable}}

def require(ok: bool, message: str, code: str='invalid_request') -> None:
    if not ok: raise WNPError(code,message)

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')

def identifier(value: Any) -> None:
    require(isinstance(value,str) and bool(IDENT.fullmatch(value)), 'Invalid identifier.')

def sequence(value: Any, minimum: int=0) -> None:
    require(type(value) is int and minimum <= value <= MAX_SEQUENCE, 'Invalid sequence.')

def stream_id(value: Any) -> None:
    try: valid=isinstance(value,str) and str(uuid.UUID(value))==value
    except (ValueError,AttributeError):valid=False
    require(valid,'Invalid stream UUID.')

def timestamp(value: Any) -> None:
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        valid=isinstance(value,str) and parsed.tzinfo is not None and 'T' in value
    except (ValueError,TypeError,AttributeError):valid=False
    require(valid,'Invalid timestamp.')

def encode(value: Any) -> bytes:
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,RecursionError):raise WNPError('invalid_request','Invalid JSON value.') from None

def decode(raw: bytes, maximum: int=MAX_PAGE) -> Any:
    require(len(raw)<=maximum,'Document exceeds size limit.','limit_exceeded')
    def pairs(items):
        obj={}
        for k,v in items:
            require(k not in obj,'Duplicate JSON member.','integrity_error');obj[k]=v
        return obj
    def bad_constant(_):raise WNPError('integrity_error','Non-finite JSON number.')
    try:return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=bad_constant)
    except (ValueError,UnicodeError,RecursionError):raise WNPError('integrity_error','Malformed JSON document.') from None

def state_payload(value: Any) -> None:
    require(isinstance(value,dict),'State must be an object.')
    require(not (META & value.keys()),'State payload contains reserved metadata or live observations.')
    require(value.get('state') in STATES,'Invalid workload state.')
    require(value.get('health') in HEALTH,'Invalid health.')
    for key in ('run','task','attempt','detail'):
        if key in value and value[key] is not None:identifier(value[key])
    if 'message' in value:require(isinstance(value['message'],str) and len(value['message'])<=2048,'Invalid message.')
    if 'progress' in value and value['progress'] is not None:
        p=value['progress'];require(isinstance(p,dict),'Invalid progress.')
        current=p.get('current');total=p.get('total')
        require(type(current) in (int,float) and math.isfinite(current) and current>=0,'Invalid current progress.')
        identifier(p.get('unit'))
        if total is not None:
            require(type(total) in (int,float) and math.isfinite(total) and total>=current,'Invalid total progress.')
    encode(value)

def evidence_ref(ref: Any) -> None:
    require(isinstance(ref,dict),'Invalid evidence reference.')
    require(isinstance(ref.get('path'),str),'Missing evidence path.')
    require(bool(HEX.fullmatch(str(ref.get('sha256','')))),'Invalid evidence hash.')
    if 'bytes' in ref: require(type(ref['bytes']) is int and 0<=ref['bytes']<=MAX_EVIDENCE,'Invalid evidence size.')

def event(value: Any, expected_stream: str|None=None, expected_sequence: int|None=None) -> None:
    require(isinstance(value,dict),'Event must be an object.','integrity_error')
    stream_id(value.get('stream'));sequence(value.get('sequence'),1);timestamp(value.get('timestamp'));identifier(value.get('type'))
    if expected_stream is not None:require(value['stream']==expected_stream,'Wrong event stream.','integrity_error')
    if expected_sequence is not None:require(value['sequence']==expected_sequence,'Non-contiguous event sequence.','sequence_gap')
    if value['type']=='state.changed':require('snapshot' in value,'state.changed requires snapshot.','integrity_error')
    if 'snapshot' in value:state_payload(value['snapshot'])
    for k in ('run','task','attempt','detail'):
        if value.get(k) is not None:identifier(value[k])
    if 'data' in value:require(isinstance(value['data'],dict),'Event data must be an object.')
    refs=value.get('evidence',[]);require(isinstance(refs,list) and len(refs)<=32,'Invalid evidence list.')
    for ref in refs:evidence_ref(ref)
    require(len(encode(value))+1<=MAX_RECORD,'Event too large.','limit_exceeded')

def snapshot(value: Any, expected_stream: str|None=None) -> None:
    require(isinstance(value,dict) and value.get('protocol')==PROTOCOL,'Unsupported snapshot.','unsupported_version')
    stream_id(value.get('stream'));sequence(value.get('sequence'));timestamp(value.get('timestamp'))
    if expected_stream is not None:require(value['stream']==expected_stream,'Wrong snapshot stream.','integrity_error')
    state_payload({k:v for k,v in value.items() if k not in META})
