"""Flushed per-case events survive native crashes and worker termination."""
import json
import time
from pathlib import Path


class Events:
    def __init__(self,path,case_id,deadline=None):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.case_id=case_id;self.started=time.monotonic();self.deadline=deadline;self.sequence=0;self.attempt_id=str(time.time_ns())
        if self.path.exists():
            for line in self.path.read_text(encoding='utf8').splitlines():
                try:self.sequence=max(self.sequence,json.loads(line)['sequence'])
                except (ValueError,KeyError):continue

    def __call__(self,*,event,phase,**details):
        self.sequence+=1;now=time.monotonic()
        record=dict(sequence=self.sequence,case_id=self.case_id,attempt_id=self.attempt_id,event=event,phase=phase,
                    elapsed_seconds=now-self.started,**details)
        if self.deadline is not None:record['remaining_seconds']=max(0,self.deadline-now)
        with self.path.open('a',encoding='utf8') as stream:
            stream.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n');stream.flush()
