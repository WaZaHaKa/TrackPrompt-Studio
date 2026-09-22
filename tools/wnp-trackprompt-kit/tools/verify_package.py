from pathlib import Path
import json,sys
from bundle_tools import verify
try:
    m=verify(Path(__file__).resolve().parents[1]);print(json.dumps({'verified':True,'package':m['package'],'version':m['version'],'files':len(m['files']),'note':'Hashes verify this manifest, not publisher authenticity.'},indent=2))
except (OSError,ValueError,KeyError) as e:print(str(e),file=sys.stderr);raise SystemExit(2)
