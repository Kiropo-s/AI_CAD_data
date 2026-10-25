"""Emit standalone readable SimpleCADAPI programs from CAD IR."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

# Retain the existing API for callers of the command compatibility module.
from .sketch_builder import (SketchBuilder, _slug, _vec2, _vec3, _circumcenter,
                             fmt_literal, fmt_num, fmt_vec)


def generate(payload: Mapping[str, Any], stem: str, *, style: str = 'readable') -> str:
    if style != 'readable':
        raise ValueError(f'unsupported emitter style: {style!r}')
    from json2scapi.codegen.readable import StructuredEmitter
    return StructuredEmitter(payload, stem).emit()


def generate_readable_with_metadata(payload: Mapping[str, Any], stem: str) -> tuple[str, dict[str, Any]]:
    from json2scapi.codegen.readable import StructuredEmitter
    emitter = StructuredEmitter(payload, stem)
    return emitter.emit(), emitter.metadata


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--stem')
    parser.add_argument('--style', choices=('readable',), default='readable')
    args = parser.parse_args(argv)
    payload = json.loads(args.input.read_text(encoding='utf-8'))
    stem = args.stem or (args.input.parent.name if args.input.name == 'model.ir.json'
                         else args.input.name.removesuffix('.ir.json').removesuffix('.json'))
    from json2scapi.runtime.output_layout import case_dir
    output = args.out or case_dir(stem) / 'model.readable.ftc.py'
    from json2scapi.codegen.readable import StructuredEmitter
    emitter = StructuredEmitter(payload, stem)
    source = emitter.emit()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding='utf-8')
    prefix = output.name.removesuffix('.ftc.py')
    for suffix,value in (('.symbols.json',emitter.metadata), ('.prepared.ir.json',emitter.payload)):
        output.with_name(prefix+suffix).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'{args.input} -> {output} ({len(source.splitlines())} lines)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
