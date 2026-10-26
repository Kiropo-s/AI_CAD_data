"""Bounded best-first CSG search with sampled residual ordering.

All accepted states still pass the same exact, independently checked double-cut
measurement as the legacy route. Sampled masks cannot authorize source emission.
"""
from dataclasses import dataclass
import heapq
import time

from json2scapi.evaluation.metrics import volume
from step2code_direct.geometry import UnsupportedGeometry
from step2code_direct.hybrid import (State, HybridResult, generate_atoms,
                                     _combine, _difference)
from step2code_direct.search_guidance import build_guidance


@dataclass
class Work:
    state: State
    mask: object
    proposals: list | None = None
    cursor: int = 0


def _seed_order(atoms, guidance):
    def rank(index):
        mask = guidance.atoms.get(index) if guidance else None
        if mask is None:
            return (float('inf'), float('inf'), index)
        missing, extra = guidance.difference(mask)
        return (missing + extra, missing, index)
    ordered = sorted(range(len(atoms)), key=rank)
    exterior = [i for i in ordered if atoms[i].kind not in ('box', 'extrusion')
                and atoms[i].preferred_operation == 'union']
    if guidance:
        exterior.sort(key=lambda i: (guidance.difference(guidance.atoms[i])[0], rank(i))
                      if i in guidance.atoms else (float('inf'), rank(i)))
    first = ordered[:2] + exterior[:2]
    # Include a profile seed even if a coarse grid misses its thin features.
    first += [i for i in ordered if atoms[i].kind == 'extrusion'][:1]
    return list(dict.fromkeys(first + ordered))


def _proposals(work, atoms, guidance, tolerance):
    proposals = []
    for index, atom in enumerate(atoms):
        if index == work.state.base:
            continue
        operations = []
        if work.state.extra > tolerance:
            operations.extend(('cut', 'intersect'))
        if work.state.missing > tolerance:
            operations.append('union')
        for operation in operations:
            if (operation, index) in work.state.steps:
                continue
            mask = guidance.combine(work.mask, index, operation) if guidance else None
            if mask is None:
                approximate = float('inf')
            else:
                missing, extra = guidance.difference(mask)
                approximate = missing + extra
            proposals.append((approximate, int(operation != atom.preferred_operation),
                              index, operation, mask))
    proposals.sort(key=lambda item: item[:4])
    return proposals


def _prune(queue, width):
    """Keep strong states and a limited missing/extra tradeoff; retain seed roots."""
    roots = [item for item in queue if not item[2].state.steps]
    descendants = [item for item in queue if item[2].state.steps]
    selected = heapq.nsmallest(width, descendants)
    if descendants:
        for metric in ('missing', 'extra'):
            diverse = min(descendants, key=lambda item: (getattr(item[2].state, metric), item[0], item[1]))
            if all(diverse[1] != item[1] for item in selected):
                selected.append(diverse)
    result = roots + selected
    heapq.heapify(result)
    return result


def recognize_guided(solid, index, options, on_progress=None, seed_prisms=()):
    started = time.monotonic()
    deadline = started + options.hybrid_timeout_seconds
    tolerance = options.reconstruction_relative_tolerance
    diagnostics = dict(source_solid_index=index, method=('native_hybrid_csg_lookahead_v3' if options.hybrid_search == 'lookahead'
                else 'native_hybrid_csg_guided_v2'),
        search_algorithm=('occupancy_lookahead_then_exact' if options.hybrid_search == 'lookahead'
                          else 'sampled_residual_best_first'), atom_errors=[], sampling_errors=[],
        operation_errors=[], max_boolean_steps=options.max_boolean_steps,
        max_boolean_trials=options.max_boolean_trials, time_budget_seconds=options.hybrid_timeout_seconds,
        boolean_trials=0, valid_trials=0, exact_score_calls=0, seed_score_calls=0,
        best_symmetric_difference_relative=None, sampled_atoms=0, sampling_points=0,
        stage_times_seconds={}, operation_counts={})

    def progress(stage, operation=None):
        diagnostics.update(stage=stage, elapsed_seconds=time.monotonic() - started)
        if operation is not None:
            diagnostics['active_operation'] = operation
        else:
            diagnostics.pop('active_operation', None)
        if on_progress:
            on_progress(dict(diagnostics))

    def measured(stage, function, *args):
        call_started = time.monotonic()
        try:
            return function(*args)
        finally:
            timings = diagnostics['stage_times_seconds']
            timings[stage] = timings.get(stage, 0.0) + time.monotonic() - call_started
            counts = diagnostics['operation_counts']
            counts[stage] = counts.get(stage, 0) + 1

    exact_scores = {}

    def score(shape, seed=False):
        cached = exact_scores.get(id(shape))
        if cached is not None:
            diagnostics['exact_score_cache_hits'] = diagnostics.get('exact_score_cache_hits', 0) + 1
            return cached[1]
        diagnostics['exact_score_calls'] += 1
        diagnostics['seed_score_calls'] += int(seed)
        result = measured('exact_scoring', _difference, solid, shape, source_volume)
        # Keep a strong reference: Python object IDs must not be reused here.
        exact_scores[id(shape)] = (shape, result)
        return result

    def remember(state):
        best = diagnostics['best_symmetric_difference_relative']
        if best is None or state.error < best:
            diagnostics['best_symmetric_difference_relative'] = state.error

    def accepted(state):
        remember(state)
        diagnostics.update(status='recognized', stop_reason='verified_geometry',
            selected_base_atom=state.base,
            selected_steps=[{'operation': op, 'atom_index': n, 'kind': atoms[n].kind}
                            for op, n in state.steps],
            selected_operands=[{'atom_index': n, 'kind': atoms[n].kind, 'source': atoms[n].evidence}
                               for n in dict.fromkeys([state.base] + [n for _, n in state.steps])])
        progress('finished')
        return HybridResult(atoms[state.base], tuple((op, atoms[n]) for op, n in state.steps),
                            state.shape, dict(diagnostics))

    progress('generate_atoms')
    source_volume = measured('reference_volume', volume, solid)
    atoms = measured('generate_atoms', generate_atoms, solid, index, options, deadline, diagnostics)
    if options.hybrid_search == 'lookahead' and seed_prisms:
        from OCP.BRepLib import BRepLib
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.TopoDS import TopoDS
        from OCP.gp import gp_Vec
        from step2code_direct.hybrid import Atom, _valid_single
        import numpy as np
        retained = []
        for native in seed_prisms:
            if time.monotonic() >= deadline:
                break
            direction = np.cross(native.x_axis, native.y_axis) * native.distance
            try:
                shape = TopoDS.Solid_s(BRepPrimAPI_MakePrism(native.face, gp_Vec(*direction)).Shape())
                if BRepLib.OrientClosedSolid_s(shape) and _valid_single(shape):
                    retained.append(Atom('extrusion', shape, {}, dict(native.evidence), 'union', native))
            except Exception as exc:
                diagnostics['atom_errors'].append({'retained_prism': str(exc)})
        atoms = retained + atoms[:max(0, options.max_hybrid_atoms - len(retained))]
        diagnostics['retained_prism_atoms'] = len(retained)
    diagnostics['atom_count'] = len(atoms)
    guidance = None
    if atoms and time.monotonic() < deadline:
        progress('sampling')
        sampling_deadline = min(deadline, time.monotonic() + min(3.0, options.hybrid_timeout_seconds * 0.2))
        try:
            guidance = measured('sampling', build_guidance, solid, atoms,
                                options.length_tolerance, sampling_deadline, diagnostics)
        except Exception as exc:
            diagnostics['sampling_errors'].append({'error': str(exc)})
    if options.hybrid_search == 'lookahead':
        # Preserve simple single-operand solutions before speculative programs.
        # It also avoids replacing an editable sphere by a redundant clipped one.
        for n in _seed_order(atoms, guidance)[:2]:
            if time.monotonic() >= deadline:
                break
            progress('single_atom_scoring', {'atom_index': n})
            try:
                missing, extra = score(atoms[n].shape, seed=True)
                single = State(atoms[n].shape, n, (), missing, extra)
                remember(single)
                if single.error <= tolerance:
                    diagnostics['solution_route'] = 'single_atom'
                    return accepted(single)
            except Exception as exc:
                if len(diagnostics['atom_errors']) < 24:
                    diagnostics['atom_errors'].append({'atom_index': n, 'error': str(exc)})
    if options.hybrid_search == 'lookahead' and guidance is not None:
        from step2code_direct.occupancy_search import propose_programs
        # Reserve half the trial budget; cap this phase to 35% of configured
        # time. A single native call can still exceed the soft deadline.
        phase_deadline = min(deadline, time.monotonic() + options.hybrid_timeout_seconds * 0.35)
        progress('occupancy_planning')
        programs = measured('occupancy_planning', propose_programs, guidance, options,
                            min(phase_deadline, time.monotonic() + 1.0), diagnostics)
        cache, failures, scored = {}, set(), set()
        trial_limit = max(1, options.max_boolean_trials // 2)
        diagnostics.update(lookahead_programs_evaluated=0, lookahead_cache_hits=0)
        for approximate_error, base, steps, _ in programs[:40]:
            if time.monotonic() >= phase_deadline or diagnostics['boolean_trials'] >= trial_limit:
                break
            shape = atoms[base].shape
            failed = False
            diagnostics['lookahead_programs_evaluated'] += 1
            for offset, (operation, n) in enumerate(steps):
                key = (base, steps[:offset + 1])
                if key in failures:
                    failed = True
                    break
                if key in cache:
                    diagnostics['lookahead_cache_hits'] += 1
                    shape = cache[key]
                    continue
                if time.monotonic() >= phase_deadline or diagnostics['boolean_trials'] >= trial_limit:
                    failed = True
                    break
                diagnostics['boolean_trials'] += 1
                progress('lookahead_build', {'base': base, 'depth': offset + 1,
                    'operation': operation, 'atom_index': n, 'sample_error_points': approximate_error})
                try:
                    shape = measured('boolean_construction', _combine, shape, atoms[n].shape, operation)
                    cache[key] = shape
                except Exception as exc:
                    failures.add(key)
                    failed = True
                    if len(diagnostics['operation_errors']) < 16:
                        diagnostics['operation_errors'].append({'operation': operation, 'atom_index': n,
                                                                'error': str(exc)})
                    break
            if failed or (base, steps) in scored or time.monotonic() >= phase_deadline:
                continue
            scored.add((base, steps))
            progress('lookahead_scoring', {'base': base, 'depth': len(steps)})
            try:
                missing, extra = score(shape)
                state = State(shape, base, steps, missing, extra)
                diagnostics['valid_trials'] += 1
                remember(state)
                if state.error <= tolerance:
                    diagnostics['solution_route'] = 'occupancy_lookahead'
                    return accepted(state)
            except Exception as exc:
                if len(diagnostics['operation_errors']) < 16:
                    diagnostics['operation_errors'].append({'final_scoring_error': str(exc)})
        diagnostics['lookahead_prefixes_cached'] = len(cache)
        progress('guided_fallback')
    if options.hybrid_search == 'lookahead':
        diagnostics['solution_route'] = 'guided_fallback'
    seed_order = _seed_order(atoms, guidance)
    queue, serial, seen = [], 0, set()
    seed_target = max(4, options.hybrid_beam_width * 2)
    seeds_scored = 0
    batch_size = max(4, options.hybrid_beam_width * 2)

    while (seed_order or queue) and time.monotonic() < deadline:
        # Only a bounded initial shortlist is scored before search starts.
        # Unscored atoms stay eligible and are admitted if the frontier runs dry.
        if seed_order and (seeds_scored < seed_target or not queue):
            n = seed_order.pop(0)
            seeds_scored += 1
            progress('seed_scoring', {'atom_index': n})
            try:
                missing, extra = score(atoms[n].shape, seed=True)
                state = State(atoms[n].shape, n, (), missing, extra)
                remember(state)
                if state.error <= tolerance:
                    return accepted(state)
                if missing < 1 - tolerance:
                    mask = guidance.atoms.get(n) if guidance else None
                    # Give every admitted root one bounded batch before revisiting roots.
                    heapq.heappush(queue, (-1.0, serial, Work(state, mask)))
                    serial += 1
            except Exception as exc:
                if len(diagnostics['atom_errors']) < 24:
                    diagnostics['atom_errors'].append({'atom_index': n, 'error': str(exc)})
            continue
        if diagnostics['boolean_trials'] >= options.max_boolean_trials:
            break
        if not queue:
            break
        _, _, work = heapq.heappop(queue)
        state = work.state
        if len(state.steps) >= options.max_boolean_steps:
            continue
        if work.proposals is None:
            work.proposals = _proposals(work, atoms, guidance, tolerance)
        attempts = 0
        while work.cursor < len(work.proposals) and attempts < batch_size:
            if diagnostics['boolean_trials'] >= options.max_boolean_trials or time.monotonic() >= deadline:
                break
            _, _, n, operation, mask = work.proposals[work.cursor]
            work.cursor += 1
            signature = (state.base, state.steps + ((operation, n),))
            if signature in seen:
                continue
            seen.add(signature)
            attempts += 1
            diagnostics['boolean_trials'] += 1
            progress('boolean_search', {'base': state.base, 'depth': len(state.steps) + 1,
                                       'operation': operation, 'atom_index': n})
            try:
                shape = measured('boolean_construction', _combine, state.shape, atoms[n].shape, operation)
                missing, extra = score(shape)
                child = State(shape, state.base, signature[1], missing, extra)
                diagnostics['valid_trials'] += 1
                remember(child)
                if child.error <= tolerance:
                    return accepted(child)
                if child.error < state.error - max(tolerance * 0.01, 1e-12):
                    heapq.heappush(queue, (child.error, serial, Work(child, mask)))
                    serial += 1
            except Exception as exc:
                if len(diagnostics['operation_errors']) < 16:
                    diagnostics['operation_errors'].append({'operation': operation, 'atom_index': n,
                                                            'error': str(exc)})
        # Revisit unfinished alternatives after better children get a chance.
        if work.cursor < len(work.proposals):
            priority = state.error + 0.01 * work.cursor / max(1, len(work.proposals))
            heapq.heappush(queue, (priority, serial, work))
            serial += 1
        queue = _prune(queue, options.hybrid_beam_width)
        progress('boolean_search')

    reason = ('time_budget' if time.monotonic() >= deadline else
              'boolean_trial_budget' if diagnostics['boolean_trials'] >= options.max_boolean_trials else
              'no_monotone_solution_within_depth_and_operands')
    diagnostics.update(status='not_recognized', stop_reason=reason)
    progress('finished')
    raise UnsupportedGeometry(f'Solid {index}: guided hybrid reconstruction found no verified solution', diagnostics)
