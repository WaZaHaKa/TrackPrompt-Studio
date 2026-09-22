from __future__ import annotations
import os
import re
import stat
import time
import uuid
from contextlib import AbstractContextManager
from pathlib import Path
from .model import WNPError, require

def reject_link(path: Path) -> None:
    if path.exists() or path.is_symlink():
        s=path.lstat()
        require(not stat.S_ISLNK(s.st_mode) and not (getattr(s,'st_file_attributes',0)&0x400), 'Links/reparse points are not allowed.','forbidden')

def safe_child(root: Path, relative: str) -> Path:
    require(isinstance(relative,str) and len(relative)<=512 and bool(re.fullmatch(r'[A-Za-z0-9_./-]+',relative)),'Unsafe relative path.','forbidden')
    parts=relative.split('/')
    require(all(p and p not in ('.','..') for p in parts),'Unsafe path component.','forbidden')
    reject_link(root)
    p=root
    for part in parts:
        p=p/part;reject_link(p)
    require(p.resolve().is_relative_to(root.resolve()),'Path escapes runtime root.','forbidden')
    return p

def atomic_write(path: Path, data: bytes) -> None:
    reject_link(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.parent/('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        if os.name!='nt':
            fd=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
    finally:
        if tmp.exists():tmp.unlink()

class FileLock(AbstractContextManager):
    """OS-owned lock: stale mtime never grants ownership; process exit releases it."""
    def __init__(self,path:Path,timeout:float=3):self.path=path;self.timeout=timeout;self.file=None;self.held=False
    def __enter__(self):
        reject_link(self.path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b')
        if self.file.seek(0,2)==0:self.file.write(b'0');self.file.flush()
        end=time.monotonic()+self.timeout
        while True:
            try:
                self.file.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.held=True;return self
            except (OSError,BlockingIOError):
                if time.monotonic()>=end:
                    self.file.close();self.file=None
                    raise WNPError('temporarily_unavailable','Another local owner holds the lock.',True) from None
                time.sleep(0.025)
    def __exit__(self,*exc):
        if self.file:
            try:
                self.file.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
            finally:self.file.close();self.file=None;self.held=False
