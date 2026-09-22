from __future__ import annotations
import hashlib
import io
import warnings
from pathlib import Path
from wnp_host.model import encode,require,now,WNPError
from wnp_host.fs import reject_link

def verify_frames(directory:Path,count:int=129,width:int=832,height:int=480)->dict:
    """Read-only full decode, continuity, dimensions, stable-file and digest checks."""
    try:from PIL import Image
    except ImportError:raise WNPError('decoder_unavailable','Use the existing environment with Pillow; no verification was performed.') from None
    require(count>0 and width>0 and height>0,'Positive frame requirements are required.')
    directory=Path(directory);reject_link(directory)
    expected=[f'frame_{i:08d}.png' for i in range(1,count+1)]
    actual=sorted(p.name for p in directory.iterdir() if p.suffix.lower()=='.png')
    require(actual==expected,'Frame filenames/count are not exactly the expected contiguous sequence.','integrity_error')
    records=[]
    for name in expected:
        p=directory/name;reject_link(p);before=p.stat()
        require(0<before.st_size<=64*1024*1024,'Frame size exceeds safety bound.','integrity_error')
        with p.open('rb') as f:raw=f.read(64*1024*1024+1)
        require(0<len(raw)<=64*1024*1024,'Frame is empty or exceeds safety bound.','integrity_error')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as im:
                    require(im.format=='PNG' and im.size==(width,height),'Unexpected frame format/dimensions.','integrity_error');im.verify()
                with Image.open(io.BytesIO(raw)) as im:im.load()
        except WNPError:raise
        except Exception:raise WNPError('integrity_error','A frame failed full PNG decoding.') from None
        after=p.stat();require((before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'Frame changed during verification.','temporarily_unavailable')
        records.append({'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    require(sorted(p.name for p in directory.iterdir() if p.suffix.lower()=='.png')==expected,'Sequence changed during verification.','temporarily_unavailable')
    return {'status':'decoded_frames_verified','observed_at':now(),'count':count,'width':width,'height':height,'manifest_sha256':hashlib.sha256(encode(records)).hexdigest(),'frames':records,'workflow_binding':'not_checked','note':'This proves file integrity, NOT workflow identity, visual quality, or final-video completion.'}
