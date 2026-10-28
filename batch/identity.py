"""Run and resume identities include source, semantic configuration and implementation."""
import hashlib
import json
import platform
import subprocess
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError
from step2code import __version__


def file_hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def implementation_identity():
    root=Path(__file__).resolve().parents[1]
    sources={str(p.relative_to(root)).replace('\\','/'):file_hash(p)
             for p in root.rglob('*.py') if not set(p.relative_to(root).parts)&{'out','tests','docs','__pycache__','.runtime'}}
    dependencies=dict(python=platform.python_version())
    for pkg in ('simplecadapi','cadquery-ocp','numpy','scipy'):
        try:dependencies[pkg]=version(pkg)
        except PackageNotFoundError:dependencies[pkg]='unavailable'
    try:
        revision=subprocess.check_output(['git','-c',f'safe.directory={root.as_posix()}','-C',str(root),'rev-parse','HEAD'],stderr=subprocess.DEVNULL,text=True).strip()
    except (OSError,subprocess.CalledProcessError):revision=None
    sources['plan/schema.json']=file_hash(root/'plan/schema.json')
    value=dict(tool_version=__version__,plan_version=1,compiler='scapi-plan-1',evaluator='shape-world-1',
        revision=revision,source_hashes=sources,dependencies=dependencies)
    return dict(**value,fingerprint=digest(value))


def case_identity(source,config,implementation):
    return dict(source_sha256=file_hash(source),configuration=config,
        implementation_fingerprint=implementation['fingerprint'],plan_version=1)
