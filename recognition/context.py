"""Per-case provenance and native-call events are separate from plan geometry."""
from contextvars import ContextVar
from contextlib import contextmanager
import time

context = ContextVar('recognition_context', default=None)


def record_source(node_id, evidence):
    current=context.get()
    if current is not None:
        current['sources'][node_id]=evidence


@contextmanager
def native_call(operation):
    current=context.get()
    if current and current.get('events'):
        current['events'](event='start',phase='recognition.native',operation=operation,
                          source_body_id=current.get('body_id'))
    try:
        yield
    except Exception as exc:
        if current and current.get('events'):
            current['events'](event='fail',phase='recognition.native',operation=operation,
                source_body_id=current.get('body_id'),reason_code='native.search_failure',error=str(exc))
        raise
    else:
        if current and current.get('events'):
            current['events'](event='end',phase='recognition.native',operation=operation,
                              source_body_id=current.get('body_id'))


def csg_deadline(seconds):
    current=context.get()
    if current is None:return time.monotonic()+seconds
    if current.get('csg_deadline') is None:current['csg_deadline']=time.monotonic()+seconds
    return current['csg_deadline']


def namespace_sources(mapping, derivation):
    current=context.get()
    if current is None:return
    for old,new in mapping.items():
        if old in current['sources']:
            original=current['sources'][old]
            current['sources'][new]=dict(namespace=derivation,parent_node=old,derived_geometry=True,
                                        evidence=original)


def call(operation, function, *args, **kwargs):
    with native_call(operation):return function(*args,**kwargs)


def capture_sources(nodes):
    import copy
    current=context.get()
    if current is None:return {}
    return {key:copy.deepcopy(value) for key,value in current['sources'].items() if key in nodes}


def restore_sources(evidence):
    current=context.get()
    if current is not None:current['sources'].update(evidence.get('node_sources',{}))
