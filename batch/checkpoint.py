"""Content-bound checkpoints. A candidate is never reused as acceptance evidence."""
import json
from pathlib import Path
from step2code.plan import load, dump
from step2code.plan.codec import write_json
from .identity import digest


class Checkpoints:
    def __init__(self,root,identity,*,resume=False):
        self.root=Path(root);self.identity=identity;self.key=digest(identity)
        self.state=dict(identity=identity,identity_hash=self.key,body_states={})
        state_path=self.root/'case-state.json'
        if resume and state_path.exists():
            previous=json.loads(state_path.read_text(encoding='utf8'))
            if previous['identity_hash']!=self.key:
                raise ValueError('Resume identity mismatch: source/configuration/implementation changed')
            self.state=previous
        write_json(state_path,self.state)

    def initialize(self,count):
        self.state['source_body_count']=count
        for i in range(1,count+1):
            self.state['body_states'].setdefault(str(i),dict(state='pending'))
        write_json(self.root/'case-state.json',self.state)

    def begin(self,index):
        self.state['active_body']=index
        if self.state['body_states'].get(str(index),{}).get('state')!='complete':
            self.state['body_states'][str(index)]=dict(state='running')
        write_json(self.root/'case-state.json',self.state)

    def fail(self,reason):
        index=self.state.get('active_body')
        if index and self.state['body_states'].get(str(index),{}).get('state')!='complete':
            self.state['body_states'][str(index)]=dict(state='failed',reason_code=reason)
        write_json(self.root/'case-state.json',self.state)

    def restore(self):
        result={}
        for i,item in self.state['body_states'].items():
            if item['state']!='complete':continue
            folder=self.root/'work'/f'body_{int(i):03d}'
            plan=load(folder/'candidate.plan.json')
            if plan.sha256!=item['plan_hash']:raise ValueError('Checkpoint plan hash mismatch')
            evidence=json.loads((folder/'provenance.json').read_text(encoding='utf8'))
            if digest(evidence)!=item['provenance_hash']:raise ValueError('Checkpoint provenance hash mismatch')
            result[int(i)]=(plan,evidence)
        return result

    def save(self,index,plan,evidence,preparation):
        folder=self.root/'work'/f'body_{index:03d}';folder.mkdir(parents=True,exist_ok=True)
        dump(plan,folder/'candidate.plan.json');write_json(folder/'provenance.json',evidence)
        write_json(folder/'preparation.json',preparation)
        self.state['body_states'][str(index)]=dict(state='complete',plan_hash=plan.sha256,provenance_hash=digest(evidence))
        self.state.pop('active_body',None)
        write_json(self.root/'case-state.json',self.state)
