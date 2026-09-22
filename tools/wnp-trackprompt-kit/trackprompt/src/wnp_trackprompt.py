"""Publish sanitized, normalized TrackPrompt observations to WNP.
The caller must prove checkpoint provenance; existence of a marker is insufficient.
"""
from __future__ import annotations
import hashlib
from wnp_host.model import require,identifier,HEX,encode
PHASES={'queued','prepared','submitted','generating','frames_complete','raw_verified','finalizing','final_verified','complete','failed','waiting_provider','ambiguous','reconciling'}

def publish(store,observation:dict):
    require(isinstance(observation,dict),'Normalized observation required.')
    o=observation
    identifier(o.get('run'));require(o.get('phase') in PHASES,'Unknown TrackPrompt phase.')
    phase=o['phase']
    if phase=='raw_verified':
        require(bool(HEX.fullmatch(str(o.get('raw_manifest_sha256','')))),'Verified raw phase requires a bound frame manifest.')
        require(o.get('workload_state')!='complete','Raw verification is not final-video completion.')
    if o.get('workload_state')=='complete':
        require(phase in ('complete','final_verified') and o.get('final_verified') is True and bool(HEX.fullmatch(str(o.get('final_manifest_sha256','')))),'Run completion requires final validated deliverables.')
    patch={'state':o.get('workload_state'),'health':o.get('health'),'run':o['run'],'task':o.get('task'),'attempt':o.get('attempt'),'detail':'render.'+phase}
    for key in ('task','attempt'):
        if o.get(key) is not None:identifier(o[key])
    ext={k:o[k] for k in ('raw_manifest_sha256','final_manifest_sha256','final_verified','tasks') if k in o}
    patch['extensions']={'trackprompt':ext}
    key='trackprompt:'+hashlib.sha256(encode(patch)).hexdigest()
    # Only suppress identical CURRENT observations. Repeated earlier states remain real transitions.
    prior=store.snapshot(live=False)
    changed=any(prior.get(k)!=v for k,v in patch.items())
    if changed:
        # Event key includes predecessor sequence: safe retries, but does not erase legitimate state revisits.
        store.append('state.changed',patch=patch,key=f'{key}:{prior["sequence"]}')
    live={k:o[k] for k in ('worker','provider','metrics','progress') if k in o}
    if live:store.observe(live)
    return store.snapshot()
