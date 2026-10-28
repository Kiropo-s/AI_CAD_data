"""Immutable, self-contained STEP Plan 1; no legacy payload detection."""
from __future__ import annotations
import copy
import hashlib
import json
from dataclasses import dataclass


class PlanError(ValueError):
    def __init__(self, code, pointer, message, details=None):
        super().__init__(f'{code} at {pointer or "/"}: {message}')
        self.code, self.pointer, self.details = code, pointer, details or {}


@dataclass(frozen=True)
class Plan:
    _json: str

    @classmethod
    def from_dict(cls, document):
        from .validation import validate
        validate(document)
        return cls(json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False))

    @property
    def data(self):
        return json.loads(self._json)

    @property
    def sha256(self):
        return hashlib.sha256(self._json.encode('utf8')).hexdigest()

    def with_parameters(self, values):
        data = self.data
        for key, value in values.items():
            if key not in data['parameters']:
                raise PlanError('parameter.unknown', '/parameters/'+key, 'Unknown parameter')
            if data['parameters'][key]['editability'] != 'independent':
                raise PlanError('parameter.fixed', '/parameters/'+key, 'Fixed parameter cannot be edited')
            data['parameters'][key]['value'] = copy.deepcopy(value)
        return Plan.from_dict(data)


def empty_document():
    return dict(format='step-plan', version=1, units=dict(length='mm', angle='rad'),
                parameters={}, nodes={}, outputs=[])


def node(op, inputs=None, **args):
    return dict(op=op, inputs=inputs or {}, args={key:dict(literal=value) for key,value in args.items()})


def arguments(document, item):
    return {key:(binding['literal'] if 'literal' in binding else
                 document['parameters'][binding['param']]['value']) for key,binding in item['args'].items()}
