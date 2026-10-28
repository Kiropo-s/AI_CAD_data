"""Primitive-first CSG proposals, with residual-selected auxiliary extrusions.

Sample masks only rank programs. Native construction and exact difference checks
remain mandatory. All phases and the guided fallback share the original budget.
"""
import time
import numpy as np

from step2code_direct.hybrid import State, _combine


def residual_operands(reference, current, masks, extrusions, used=(), per_operation=2):
    """Select a few sketch tools that affect missing or excess sampled material."""
    selected = {}
    missing, extra = reference & ~current, current & ~reference
    before = (reference ^ current).bit_count()
    for operation in ('union', 'cut', 'intersect'):
        candidates = []
        for index in extrusions:
            if (operation, index) in used or index not in masks:
                continue
            operand = masks[index]
            benefit = ((missing & operand).bit_count() if operation == 'union' else
                       (extra & operand).bit_count() if operation == 'cut' else
                       (extra & ~operand).bit_count())
            if not benefit:
                continue
            result = (current | operand if operation == 'union' else
                      current & ~operand if operation == 'cut' else current & operand)
            gain = before - (reference ^ result).bit_count()
            candidates.append((-gain, -benefit, index))
        selected[operation] = [item[2] for item in sorted(candidates)[:per_operation]]
    return selected


def propose_csg_programs(guidance, atoms, options, deadline, diagnostics, *, auxiliary=False):
    """Use primitive roots; optionally add only residual-relevant sketch tools."""
    masks = {i: int.from_bytes(np.packbits(mask).tobytes(), 'little')
             for i, mask in guidance.atoms.items()}
    reference = int.from_bytes(np.packbits(guidance.reference).tobytes(), 'little')
    primitives = [i for i in masks if atoms[i].kind != 'extrusion']
    extrusions = [i for i in masks if atoms[i].kind == 'extrusion'] if auxiliary else []
    width = max(16, min(96, options.hybrid_beam_width * 16))
    allowance = max(2, int(reference.bit_count() * 0.1))
    error = lambda mask: (reference ^ mask).bit_count()
    frontier = sorted((error(masks[i]), i, (), masks[i]) for i in primitives)
    # Keep distinct roots and constructions even when sampled masks coincide.
    output = list(frontier)
    expansions = 0
    for depth in range(options.max_boolean_steps):
        children = []
        for score, base, steps, current in frontier:
            if time.monotonic() >= deadline:
                break
            auxiliary_tools = residual_operands(reference, current, masks, extrusions, steps)
            for operation in ('union', 'cut', 'intersect'):
                for index in primitives + auxiliary_tools[operation]:
                    if index == base or (operation, index) in steps:
                        continue
                    operand = masks[index]
                    result = (current | operand if operation == 'union' else
                              current & ~operand if operation == 'cut' else current & operand)
                    expansions += 1
                    if not result or result == current:
                        continue
                    next_error = error(result)
                    if next_error > score + allowance:
                        continue
                    children.append((next_error, base, steps + ((operation, index),), result))
        def rank(item):
            sketch_count = sum(atoms[i].kind == 'extrusion' for _, i in item[2])
            return item[0], sketch_count, len(item[2]), item[1], item[2]
        children.sort(key=rank)
        frontier, counts = [], {}
        for item in children:
            if counts.get(item[3], 0) >= 3:
                continue
            counts[item[3]] = counts.get(item[3], 0) + 1
            frontier.append(item)
            if len(frontier) >= width:
                break
        output.extend(frontier[:12])
        if not frontier or time.monotonic() >= deadline:
            break
    diagnostics['expansions'] = expansions
    diagnostics['program_count'] = len(output)
    output.sort(key=lambda item: (item[0], sum(atoms[i].kind == 'extrusion' for _, i in item[2]),
                                  len(item[2]), item[1], item[2]))
    return output


def search_csg_first(atoms, guidance, options, deadline, diagnostics,
                     score, remember, progress, measured):
    """Return exact-scored states to seed the bounded guided fallback."""
    diagnostics['primitive_pool_size'] = sum(a.kind != 'extrusion' for a in atoms)
    diagnostics['auxiliary_pool_size'] = sum(a.kind == 'extrusion' for a in atoms)
    diagnostics['csg_phases'] = []
    if guidance is None:
        diagnostics['csg_planning_unavailable'] = 'occupancy guidance unavailable'
        return []
    cached, scored, states = {}, set(), []
    # Reserve a quarter of remaining soft time for exact guided fallback.
    remaining = max(0.0, deadline - time.monotonic())
    phase_deadlines = [min(deadline, time.monotonic() + remaining * 0.25),
                       min(deadline, time.monotonic() + remaining * 0.75)]
    for auxiliary, phase_deadline, max_scores, trial_fraction in zip(
            (False, True), phase_deadlines, (6, 20), (0.25, 0.75)):
        phase = {'name': 'residual_extrusions' if auxiliary else 'primitive_only',
                 'programs_evaluated': 0, 'exact_scores': 0, 'cache_hits': 0}
        diagnostics['csg_phases'].append(phase)
        progress('csg_' + phase['name'])
        planning_deadline = min(phase_deadline, time.monotonic() + 0.5)
        programs = measured('csg_planning', propose_csg_programs,
                            guidance, atoms, options, planning_deadline, phase, auxiliary=auxiliary)
        trial_limit = max(1, int(options.max_boolean_trials * trial_fraction))
        for _, base, steps, _ in programs:
            if (time.monotonic() >= phase_deadline or phase['exact_scores'] >= max_scores
                    or diagnostics['boolean_trials'] >= trial_limit):
                break
            if (base, steps) in scored:
                continue
            phase['programs_evaluated'] += 1
            shape, failed = atoms[base].shape, False
            for offset, (operation, index) in enumerate(steps):
                key = (base, steps[:offset + 1])
                if key in cached:
                    shape = cached[key]
                    phase['cache_hits'] += 1
                    if shape is None:
                        failed = True
                        break
                    continue
                if time.monotonic() >= phase_deadline or diagnostics['boolean_trials'] >= trial_limit:
                    failed = True
                    break
                progress('csg_build', {'phase': phase['name'], 'base': base, 'depth': offset + 1,
                                       'operation': operation, 'atom_index': index})
                diagnostics['boolean_trials'] += 1
                try:
                    shape = measured('boolean_construction', _combine, shape, atoms[index].shape, operation)
                    cached[key] = shape
                except Exception as exc:
                    cached[key] = None
                    if len(diagnostics['operation_errors']) < 16:
                        diagnostics['operation_errors'].append({'phase': phase['name'], 'error': str(exc)})
                    failed = True
                    break
            if failed or time.monotonic() >= phase_deadline:
                continue
            scored.add((base, steps))
            phase['exact_scores'] += 1
            progress('csg_scoring', {'phase': phase['name'], 'base': base, 'depth': len(steps)})
            try:
                missing, extra = score(shape, seed=not steps)
                state = State(shape, base, steps, missing, extra)
                diagnostics['valid_trials'] += int(bool(steps))
                remember(state)
                states.append(state)
                if state.error <= options.reconstruction_relative_tolerance:
                    diagnostics['solution_route'] = 'csg_' + phase['name']
                    return [state]
            except Exception as exc:
                if len(diagnostics['operation_errors']) < 16:
                    diagnostics['operation_errors'].append({'phase': phase['name'], 'scoring_error': str(exc)})
        progress('csg_' + phase['name'] + '_finished')
    diagnostics['solution_route'] = 'csg_guided_fallback'
    return sorted(states, key=lambda state: (state.error, len(state.steps)))[:options.hybrid_beam_width]
