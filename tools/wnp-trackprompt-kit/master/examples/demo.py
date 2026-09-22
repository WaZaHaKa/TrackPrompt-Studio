"""Synthetic, local-only demonstration. Writes only a fresh temporary directory."""
from pathlib import Path
import json,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from wnp_host.store import Store
with tempfile.TemporaryDirectory(prefix='wnp-demo-') as temporary:
    host=Store.initialize(Path(temporary),'example','demo-node')
    host.append('state.changed',patch={'state':'waiting','health':'degraded','run':'demo','task':'shot-002'},key='transition-1')
    host.observe({'worker':{'state':'stale'},'metrics':{'gpu_utilization_percent':0}})
    print(json.dumps(host.snapshot(),indent=2))
    print(json.dumps(host.sync(host.node()['stream'],0),indent=2))
