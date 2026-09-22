"""Local submission intent journal. Not a substitute for provider reconciliation."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from wnp_host.model import require,identifier,HEX,now

class AttemptLedger:
    def __init__(self,path:Path,owner):
        self.path=Path(path);self.owner=owner;self._owned();self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as c:c.execute('''CREATE TABLE IF NOT EXISTS attempts(run TEXT,shot TEXT,attempt TEXT,workflow_sha256 TEXT NOT NULL,input_sha256 TEXT NOT NULL,state TEXT NOT NULL,prompt_id TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(run,shot,attempt))''')
    def _owned(self):require(bool(getattr(self.owner,'held',False)),'Exclusive run ownership is required.','forbidden')
    @contextmanager
    def _db(self):
        c=sqlite3.connect(self.path,timeout=5);c.row_factory=sqlite3.Row;c.execute('PRAGMA synchronous=FULL')
        try:
            with c:yield c
        finally:c.close()
    @staticmethod
    def _ids(run,shot,attempt):
        for value in (run,shot,attempt):identifier(value)
        return run,shot,attempt
    def get(self,run,shot,attempt):
        with self._db() as c:r=c.execute('SELECT * FROM attempts WHERE run=? AND shot=? AND attempt=?',self._ids(run,shot,attempt)).fetchone()
        return dict(r) if r else None
    def prepare(self,run,shot,attempt,workflow_sha256,input_sha256):
        self._owned();ids=self._ids(run,shot,attempt)
        for digest in (workflow_sha256,input_sha256):require(bool(HEX.fullmatch(digest)),'Invalid attempt digest.')
        with self._db() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM attempts WHERE run=? AND shot=? AND attempt=?',ids).fetchone()
            if r:require((r['workflow_sha256'],r['input_sha256'])==(workflow_sha256,input_sha256),'Attempt identity changed.','identity_conflict')
            else:c.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)',(*ids,workflow_sha256,input_sha256,'prepared',None,now()))
        return self.get(*ids)
    def begin_send(self,run,shot,attempt):
        self._owned();ids=self._ids(run,shot,attempt)
        with self._db() as c:
            cur=c.execute("UPDATE attempts SET state='sending',updated_at=? WHERE run=? AND shot=? AND attempt=? AND state='prepared'",(now(),*ids))
            require(cur.rowcount==1,'Submission already started or intent is absent.','ambiguous_submission')
        # Caller may make ONE provider POST only after this method returns successfully.
    def accept(self,run,shot,attempt,prompt_id):
        self._owned();ids=self._ids(run,shot,attempt)
        require(isinstance(prompt_id,str) and 1<=len(prompt_id)<=256 and '\n' not in prompt_id,'Invalid provider job ID.')
        with self._db() as c:
            r=c.execute('SELECT * FROM attempts WHERE run=? AND shot=? AND attempt=?',ids).fetchone()
            require(r is not None and r['state'] in ('sending','accepted'),'No eligible submission to adopt.','integrity_error')
            require(r['prompt_id'] in (None,prompt_id),'Attempt already bound to another provider ID.','identity_conflict')
            c.execute("UPDATE attempts SET state='accepted',prompt_id=?,updated_at=? WHERE run=? AND shot=? AND attempt=?",(prompt_id,now(),*ids))
    def reject(self,run,shot,attempt):
        """Use only for a definitive provider rejection, NEVER for timeout/missing history."""
        self._owned();ids=self._ids(run,shot,attempt)
        with self._db() as c:
            cur=c.execute("UPDATE attempts SET state='rejected',updated_at=? WHERE run=? AND shot=? AND attempt=? AND state='sending'",(now(),*ids))
            require(cur.rowcount==1,'No pending submission to reject.','integrity_error')
