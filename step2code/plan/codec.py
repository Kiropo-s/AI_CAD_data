"""Strict JSON and atomic serialization; reject ambiguous JSON before kernel use."""
import json
import os
import time
import uuid
from pathlib import Path
from .model import Plan, PlanError

MAX_BYTES = 32 * 1024 * 1024


def _pairs(items):
    result = {}
    for key,value in items:
        if key in result:
            raise PlanError('json.duplicate_key', '', f'Duplicate key: {key}')
        result[key] = value
    return result


def loads(text):
    if len(text.encode('utf8')) > MAX_BYTES:
        raise PlanError('json.size_limit', '', 'Plan exceeds 32 MiB')
    def invalid(value):
        raise PlanError('json.nonfinite', '', value)
    try:
        data = json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise PlanError('json.invalid', '', str(exc)) from exc
    return Plan.from_dict(data)


def load(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:
        raise PlanError('json.size_limit', '', 'Plan exceeds 32 MiB')
    return loads(data.decode('utf-8-sig'))


def write_json(path, value):
    """Atomic replace with bounded retries for Windows sharing/antivirus contention."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('w',encoding='utf8') as stream:
            stream.write(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(8):
            try:
                temporary.replace(path)
                break
            except PermissionError as exc:
                if getattr(exc,'winerror',None) not in (5,32,33) or attempt==7:
                    raise
                time.sleep(min(.01*2**attempt,.25))
    finally:
        if temporary.exists():temporary.unlink()


def dump(plan, path):
    write_json(path, plan.data)
