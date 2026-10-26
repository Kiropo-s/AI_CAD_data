"""Bounded CSG program proposals on sampled occupancy, never an acceptor.

A proposal is only a tuple of native atom references and ordinary operations.
It must be rebuilt with OCC and pass exact differences before code is emitted.
"""
import time
import numpy as np


def _bits(mask):
    return int.from_bytes(np.packbits(mask).tobytes(), 'little')


def propose_programs(guidance, options, deadline, diagnostics):
    if guidance is None or len(guidance.atoms) < 2:
        return []
    masks = {index: _bits(mask) for index, mask in guidance.atoms.items()}
    reference = _bits(guidance.reference)
    allowance = max(2, int(reference.bit_count() * 0.1))
    width = max(16, min(96, options.hybrid_beam_width * 16))
    error = lambda mask: (reference ^ mask).bit_count()
    frontier = sorted((error(mask), base, (), mask) for base, mask in masks.items())
    output, expansions = [], 0
    for depth in range(options.max_boolean_steps):
        children = []
        for score, base, steps, current in frontier:
            if time.monotonic() >= deadline:
                break
            for index, operand in masks.items():
                if index == base:
                    continue
                for operation in ('union', 'cut', 'intersect'):
                    if (operation, index) in steps:
                        continue
                    mask = (current | operand if operation == 'union' else
                            current & ~operand if operation == 'cut' else current & operand)
                    expansions += 1
                    # This is only the pre-search. Unchanged masks can still
                    # contain thin features; the subsequent exact search allows them.
                    if not mask or mask == current:
                        continue
                    next_error = error(mask)
                    if next_error > score + allowance:
                        continue
                    children.append((next_error, base, steps + ((operation, index),), mask))
        children.sort(key=lambda item: (item[0], item[1], item[2]))
        frontier, counts = [], {}
        for item in children:
            count = counts.get(item[3], 0)
            if count >= 3:
                continue
            counts[item[3]] = count + 1
            frontier.append(item)
            if len(frontier) >= width:
                break
        output.extend(frontier[:12])
        if not frontier or time.monotonic() >= deadline:
            break
    diagnostics['occupancy_expansions'] = expansions
    diagnostics['occupancy_programs'] = len(output)
    output.sort(key=lambda item: (item[0], len(item[2]), item[1], item[2]))
    return output
