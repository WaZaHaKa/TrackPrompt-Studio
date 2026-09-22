from pathlib import Path
import argparse,json
from bundle_tools import install
p=argparse.ArgumentParser(description='Verify and stage this kit into an existing repository; never overwrite differences.')
p.add_argument('--target',type=Path,required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
try:print(json.dumps(install(Path(__file__).resolve().parents[1],a.target,a.apply),indent=2))
except (OSError,ValueError,KeyError) as e:p.exit(2,str(e)+'\n')
