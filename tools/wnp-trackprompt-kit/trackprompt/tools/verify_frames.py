from pathlib import Path
import argparse,sys,json
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT.parent/'master/src')]
from frame_check import verify_frames
p=argparse.ArgumentParser(description='Read-only frame audit; does not write raw-verified.json or start rendering.')
p.add_argument('--directory',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--count',type=int,default=129);p.add_argument('--width',type=int,default=832);p.add_argument('--height',type=int,default=480)
a=p.parse_args()
if a.output.exists():p.error('Output already exists; choose a new audit report filename.')
if a.output.resolve().is_relative_to(a.directory.resolve()):p.error('Audit output must be outside the source frame directory.')
report=verify_frames(a.directory,a.count,a.width,a.height)
a.output.parent.mkdir(parents=True,exist_ok=True)
with a.output.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
print('Frame audit written. Workflow binding and final-film validation remain separate.')
