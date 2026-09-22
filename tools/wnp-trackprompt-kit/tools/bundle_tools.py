"""Content-verified, no-overwrite package staging. Does not configure or execute projects."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,stat,uuid,shutil

def _no_link(p:Path):
    if p.exists() or p.is_symlink():
        s=p.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:raise ValueError('Symlink/reparse point refused: '+str(p))
def _path(root:Path,relative:str):
    p=PurePosixPath(relative)
    if not relative or p.is_absolute() or '\\' in relative or ':' in relative or any(x in ('','.','..') for x in relative.split('/')):raise ValueError('Unsafe manifest path')
    out=root
    for part in p.parts:out=out/part;_no_link(out)
    if not out.resolve().is_relative_to(root.resolve()):raise ValueError('Path escapes package root')
    return out

def verify(root:Path):
    root=Path(root).resolve();path=root/'BUNDLE_MANIFEST.json';_no_link(path)
    manifest=json.loads(path.read_text(encoding='utf-8'));seen=set()
    if manifest.get('package') not in ('master','companion','trackprompt'):raise ValueError('Unknown package type')
    expected='wnp-'+manifest['package']+'-kit'
    if manifest.get('destination')!=expected:raise ValueError('Invalid installation destination')
    for item in manifest['files']:
        name=item['path']
        if name in seen or name=='BUNDLE_MANIFEST.json':raise ValueError('Duplicate/self-referencing manifest entry')
        seen.add(name);f=_path(root,name)
        if not f.is_file():raise ValueError('Missing package file: '+name)
        data=f.read_bytes()
        if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('Package integrity failed: '+name)
    # Never copy generated local output or arbitrary extras, even when present.
    return manifest

def install(root:Path,repo:Path,apply:bool=False):
    root=Path(root).resolve();repo=Path(repo).resolve();_no_link(repo)
    if not repo.is_dir() or not (repo/'.git').exists():raise ValueError('Target must be an existing Git repository/worktree root')
    manifest=verify(root);dest=_path(repo,'tools/'+manifest['destination'])
    names=[x['path'] for x in manifest['files']]+['BUNDLE_MANIFEST.json'];pending=[]
    for name in names:
        src=_path(root,name);target=_path(dest,name)
        if target.exists():
            if not target.is_file() or target.read_bytes()!=src.read_bytes():raise ValueError('Existing different file; nothing copied: '+str(target))
        else:pending.append((src,target))
    if apply:
        for src,target in pending:
            target.parent.mkdir(parents=True,exist_ok=True);_no_link(target.parent)
            # Exclusive create refuses a concurrent writer instead of overwriting.
            with src.open('rb') as source,target.open('xb') as out:
                shutil.copyfileobj(source,out);out.flush();os.fsync(out.fileno())
    return {'mode':'apply' if apply else 'dry_run','destination':str(dest),'new_files':len(pending),'unchanged_files':len(names)-len(pending),'project_code_modified':False}
