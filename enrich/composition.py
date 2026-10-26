"""Internal enrichment composition, with no scheduler or archival writer (ADR 0117)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import bank
import proposers
from proposers import FeatureTarget


class CompositionError(ValueError):
    """An invalid selection must fail before dispatch."""


@dataclass(frozen=True)
class ResultTarget:
    contract: tuple[str, str]
    methods: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    produce: tuple[ResultTarget, ...] = ()
    attempt: tuple[str, ...] = ()
    features: tuple[FeatureTarget, ...] = ()


@dataclass(frozen=True)
class Diagnostic:
    code: str
    method: str | None = None
    path: str | None = None
    target: FeatureTarget | None = None
    reason: str | None = None
    prerequisites: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        result = {key: value for key, value in vars(self).items() if value is not None}
        result['cost'] = None  # the existing scheduler owns estimates
        result['prerequisites'] = list(self.prerequisites)
        if self.target is not None:
            result['target'] = [self.target.extractor] + ([self.target.source] if self.target.source else [])
        return result


@dataclass(frozen=True)
class Expansion:
    targets: tuple[FeatureTarget, ...]
    proposers: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]


def _features(value: Any, where: str) -> tuple[FeatureTarget, ...]:
    if not isinstance(value, (list, tuple)):
        raise CompositionError(f'{where} must be an array')
    targets = []
    for item in value:
        if not (isinstance(item, (list, tuple)) and 1 <= len(item) <= 2
                and all(isinstance(part, str) and part for part in item)):
            raise CompositionError(f'{where}: a feature is [extractor] or [extractor, file-feature]')
        targets.append(FeatureTarget(*item))
    return tuple(dict.fromkeys(targets))


def _methods(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) and item for item in value):
        raise CompositionError(f'{where} must be an array of proposer ids')
    if len(set(value)) != len(value):
        raise CompositionError(f'{where} contains duplicate methods')
    return tuple(value)


def selection(config: Mapping[str, Any], flow: str, defaults: Selection,
              proposers_override: str | None = None) -> Selection:
    """Apply fields over defaults. None CLI input is omitted; empty disables attempts."""
    if flow not in ('capture', 'backfill'):
        raise CompositionError(f'unknown composition flow {flow!r}')
    composition = config.get('composition', {})
    if not isinstance(composition, dict) or set(composition) - {'capture', 'backfill'}:
        raise CompositionError('composition must be an object of capture and backfill')
    for name, fields in composition.items():
        if not isinstance(fields, dict) or set(fields) - {'produce', 'attempt', 'features'}:
            raise CompositionError(f'composition.{name} must contain only produce, attempt, features')
    fields = composition.get(flow, {})
    features = defaults.features
    legacy = config.get('backfill', {})
    if flow == 'backfill' and isinstance(legacy, dict) and 'units' in legacy:
        if 'features' in fields:
            raise CompositionError('backfill.units conflicts with composition.backfill.features')
        features = _features(legacy['units'], 'backfill.units')
    if 'features' in fields:
        features = _features(fields['features'], f'composition.{flow}.features')
    attempt = _methods(fields['attempt'], f'composition.{flow}.attempt') if 'attempt' in fields else defaults.attempt
    if proposers_override is not None:
        attempt = _methods([item.strip() for item in proposers_override.split(',')] if proposers_override else [], '--proposers')
    produce = defaults.produce
    if 'produce' in fields:
        value = fields['produce']
        if not isinstance(value, list):
            raise CompositionError(f'composition.{flow}.produce must be an array')
        results = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {'contract', 'methods'}:
                raise CompositionError('a produced result must contain contract and methods')
            contract = item['contract']
            if not (isinstance(contract, list) and len(contract) == 2
                    and all(isinstance(part, str) and part for part in contract)):
                raise CompositionError('a result contract is [id, version]')
            methods = _methods(item['methods'], 'result.methods')
            if not methods:
                raise CompositionError('a produced result requires at least one method')
            results.append(ResultTarget(tuple(contract), methods))
        produce = tuple(dict.fromkeys(results))
    return Selection(produce, attempt, features)


def _target(target: FeatureTarget, catalog: bank.Catalog) -> None:
    catalog.order([target.extractor])
    if target.source is not None:
        match = bank.FEATURE.fullmatch(target.source)
        if match is None:
            raise CompositionError(f'{target.source!r} is not an extractor/output reference')
        source, output = match.groups()
        if catalog[source].outputs.get(output, {}).get('kind') != 'file':
            raise CompositionError(f'{target.source}: only a file feature can be measured as content')
        if source in catalog.order([target.extractor]):
            raise CompositionError(f'{target.extractor} cannot measure an output it makes or needs: {target.source}')


def validate(chosen: Selection, catalog: bank.Catalog, scope: str = 'whole') -> None:
    """Fail before dispatch on references, exact contracts and unsupported scope."""
    if scope not in ('whole', 'region'):
        raise CompositionError(f'unsupported scope {scope!r}')
    methods = list(chosen.attempt)
    for result in chosen.produce:
        if not result.methods or len(set(result.methods)) != len(result.methods):
            raise CompositionError('produced results require nonempty, distinct methods')
        for method in result.methods:
            if method not in proposers.PROPOSERS:
                raise CompositionError(f'unknown proposer {method!r}')
            if result.contract not in proposers.PROPOSERS[method].requirement.contracts:
                raise CompositionError(f'{method} does not declare contract {result.contract!r}')
        methods.extend(result.methods)
    for method in dict.fromkeys(methods):
        if method not in proposers.PROPOSERS:
            raise CompositionError(f'unknown proposer {method!r}')
        requirement = proposers.PROPOSERS[method].requirement
        production = any(method in result.methods for result in chosen.produce)
        if scope not in requirement.scopes and not (requirement.resolver == 'unsupported' and not production):
            raise CompositionError(f'{method} does not support {scope} scope')
        for path in requirement.paths:
            for read in path.required:
                _target(FeatureTarget(read.extractor, read.source), catalog)
                for output in read.outputs:
                    if output not in catalog[read.extractor].outputs:
                        raise CompositionError(f'{method}: unknown output {read.extractor}/{output}')
            for target in path.production or ():
                _target(target, catalog)
        for read in requirement.optional:
            _target(FeatureTarget(read.extractor, read.source), catalog)
            for output in read.outputs:
                if output not in catalog[read.extractor].outputs:
                    raise CompositionError(f'{method}: unknown optional output {read.extractor}/{output}')
    for target in chosen.features:
        _target(target, catalog)


def configured(catalog: bank.Catalog, programs: Mapping[str, str]) -> dict[str, bool]:
    """Observe program configuration; callers may further constrain remote routes.

    These are direct capabilities: expand checks the Catalog dependency closure
    against the bank so cached prerequisites need no configured producer. A
    missing capability is unknown, never permission to launch a producer.
    """
    import extract
    return {name: extract.unconfigured(catalog[name], dict(programs)) is None
            for name in catalog.extractors}


def expand(chosen: Selection, context: proposers.Context, catalog: bank.Catalog,
           parameters: Mapping[str, Mapping[str, Any]] | None = None,
           capabilities: Mapping[str, bool] | None = None) -> Expansion:
    """Resolve again on each call; only explicit production adds prerequisites.

    Requirement recipes and Bank choose inputs through the same resolver used
    by proposer execution. The existing runner resolves keys/preferences and
    held failures again at dispatch; this report is not a dispatch lease.
    """
    validate(chosen, catalog, 'region' if context.region is not None else 'whole')
    # Validate parameters with the Catalog's own rules, without calculating keys.
    catalog.check_overrides(parameters or {})
    for name in catalog.extractors:
        catalog.parameters(name, parameters)
    targets = list(dict.fromkeys(chosen.features))
    diagnostics = [Diagnostic('feature-requested', target=target,
                              reason='explicit feature target; runner resolves dependencies and cache')
                   for target in targets]
    methods = list(dict.fromkeys(method for result in chosen.produce for method in result.methods))
    production_methods = set(methods)
    methods.extend(method for method in chosen.attempt if method not in production_methods)
    methods = list(dict.fromkeys(methods))
    for method in methods:
        try:
            resolved = proposers.resolve_inputs(method, context)
        except (bank.BankError, proposers.ProposerError) as exc:
            diagnostics.append(Diagnostic('damaged', method, reason=str(exc)))
            continue
        if resolved.ready:
            diagnostics.append(Diagnostic('cached-inputs', method, resolved.path,
                                          reason='selected cached path; no alternative production'))
            continue
        if method not in production_methods:
            diagnostics.append(Diagnostic('attempt-only', method, resolved.path,
                                          reason='opportunistic attempt creates no prerequisites',
                                          prerequisites=resolved.missing))
            continue
        candidates = resolved.production
        if not candidates:
            diagnostics.append(Diagnostic('unsupported-production', method, resolved.path,
                                          reason='resolver provides no production recipe',
                                          prerequisites=resolved.missing))
            continue
        selected = None
        damaged_path = False
        for path in candidates:
            if path.production is None:
                continue
            missing = []
            for target in path.production:
                _target(target, catalog)
                required = catalog.order([target.extractor])
                for name in required:
                    try:
                        available = (context.bank.entry_from(context.content, target.source, name)
                                     if target.source else context.bank.entry(context.content, name))
                    except (bank.BankError, proposers.ProposerError) as exc:
                        diagnostics.append(Diagnostic('damaged', method, path.id, target, str(exc)))
                        damaged_path = True
                        break
                    if available is None and (capabilities is None or not capabilities.get(name, False)):
                        missing.append(name)
                if damaged_path:
                    break
            if damaged_path:
                break
            if missing:
                diagnostics.append(Diagnostic('unconfigured-path', method, path.id,
                                              reason='mandatory producer capability is absent or unknown',
                                              prerequisites=tuple(dict.fromkeys(missing))))
                continue
            selected = path
            break
        if selected is None:
            diagnostics.append(Diagnostic('blocked', method, resolved.path,
                                          reason='damaged prerequisite; no alternative selected' if damaged_path else 'no configured production path',
                                          prerequisites=resolved.missing))
            continue
        for target in selected.production or ():
            if target not in targets:
                targets.append(target)
            diagnostics.append(Diagnostic('production-required', method, selected.id, target,
                                          'explicit result requires this target; runner rechecks cache and failures',
                                          resolved.missing))
        diagnostics.append(Diagnostic('deferred-input-choice', method, selected.id,
                                      reason='re-resolve after prerequisites; optional reads never produce work'))
    return Expansion(tuple(targets), tuple(methods), tuple(diagnostics))


def explain(expansion: Expansion, chosen: Selection, context: proposers.Context,
            catalog: bank.Catalog,
            parameters: Mapping[str, Mapping[str, Any]] | None = None, *,
            evidence: Any = None, config_identity: Any = None,
            policy_identity: Any = None, routes: Mapping[str, Any] | None = None,
            costs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Read-only operational explanation; never an executable dispatch plan.

    Bank-selected entries supply identity. Optional declarations stay unread;
    only execution can say which optional inputs were actually consumed.
    Routes and costs are caller observations, not inferred capabilities.
    """
    def declaration(read: proposers.FeatureRead) -> dict[str, Any]:
        return {'extractor': read.extractor, 'source': read.source,
                'outputs': list(read.outputs)}

    def bound(name: str, source: str | None, entry: bank.Entry | None,
              outputs: tuple[str, ...] = ()) -> dict[str, Any]:
        value: dict[str, Any] = {'extractor': name, 'source': source,
                                 'outputs': list(outputs), 'key': None,
                                 'parameters': None, 'cache': 'missing',
                                 'equivalence': None, 'coverage': None}
        effective = context.bank.catalog.parameters(name, context.bank.parameters)
        if source is not None:
            effective = context.bank.catalog.parameters(
                name, context.bank.catalog.overrides_for(source, context.bank.parameters))
        value['parameters'] = effective
        if entry is None:
            value['expected_key'] = None
            value['identity_state'] = 'deferred'
            value['reason'] = 'no resolved entry; source or prerequisites may be deferred'
            if source is None and not context.bank.catalog[name].prefers:
                value['expected_key'] = context.bank.catalog.within(context.content, context.region).keys(
                    context.content, [name], context.bank.parameters)[name]
                value['identity_state'] = 'expected'
                value['reason'] = 'configured mix identity known; no cached entry resolved'
            return value
        manifest = entry.manifest
        value.update(key=manifest['key'], parameters=manifest['parameters'],
                     outputs=list(outputs or tuple(manifest['outputs'])),
                     content=manifest['content'], version=manifest['extractor']['version'],
                     dependencies=manifest['needs'], region=entry.region,
                     timeline=entry.timeline, cache='cached',
                     equivalence=('configured-parameters' if manifest['parameters'] == effective
                                  else 'bank-selected-stand-in'),
                     coverage={'requested': context.region, 'selected': entry.timeline})
        return value

    methods = []
    for method in expansion.proposers:
        proposer = proposers.PROPOSERS[method]
        requirement = proposer.requirement
        damaged = None
        try:
            resolved = proposers.resolve_inputs(method, context)
        except (bank.BankError, proposers.ProposerError) as exc:
            damaged = str(exc)
            resolved = proposers.ResolvedInputs(None, {}, missing=(damaged,))
        path = next((p for p in requirement.paths if p.id == resolved.path), None)
        reads = path.required if path else ()
        inputs = []
        for read in reads:
            entry = resolved.entries.get(read.extractor)
            source = read.source
            if entry is not None and entry.manifest['content'] != context.content:
                source = resolved.source or source
            inputs.append(bound(read.extractor, source, entry, read.outputs))
        # Specialized resolvers may bind additional inputs (e.g. source stems).
        declared = {read.extractor for read in reads}
        inputs.extend(bound(name, None, entry) for name, entry in resolved.entries.items()
                      if name not in declared)
        methods.append({'method': method, 'version': proposer.version,
                        'contracts': [list(c) for c in requirement.contracts],
                        'selected_results': [list(r.contract) for r in chosen.produce
                                             if method in r.methods],
                        'attempt': method in chosen.attempt, 'path': resolved.path,
                        'source': resolved.source, 'ready': resolved.ready,
                        'observation': {'status': 'damaged', 'reason': damaged} if damaged else None,
                        'missing': list(resolved.missing), 'inputs': inputs,
                        'requirements': [{'path': p.id,
                                          'required': [declaration(r) for r in p.required],
                                          'production': None if p.production is None else
                                          [[t.extractor, t.source] for t in p.production]}
                                         for p in requirement.paths],
                        'optional': [{**declaration(r), 'key': None,
                                      'reason': 'optional input not read by explanation'}
                                     for r in requirement.optional],
                        'decisions': [d.as_json() for d in expansion.diagnostics
                                      if d.method == method]})
    targets = []
    for target in expansion.targets:
        damaged = None
        try:
            entry = (context.bank.entry_from(context.content, target.source, target.extractor)
                     if target.source else context.bank.entry(context.content, target.extractor))
        except (bank.BankError, proposers.ProposerError) as exc:
            damaged, entry = str(exc), None
        value = bound(target.extractor, target.source, entry)
        if damaged is not None:
            value.update(cache='damaged', reason=damaged)
        value['explicit'] = target in chosen.features
        value['dependencies'] = (entry.manifest['needs'] if entry else
                                 {'extractors': catalog.order([target.extractor])[:-1]})
        value['route'] = {'value': (routes or {}).get(target.extractor),
                          'reason': 'caller configuration' if target.extractor in (routes or {})
                          else 'route not supplied'}
        value['cost'] = {'value': (costs or {}).get(target.extractor),
                         'reason': 'caller observation' if target.extractor in (costs or {})
                         else 'cost and source unknown'}
        targets.append(value)
    return {'content': context.content, 'region': context.region, 'source': None,
            'evidence': evidence, 'config_identity': config_identity,
            'policy_identity': policy_identity,
            'catalog_versions': {name: extractor.version
                                 for name, extractor in catalog.extractors.items()},
            'parameters': {name: catalog.parameters(name, parameters)
                           for name in catalog.extractors},
            'methods': methods, 'targets': targets,
            'diagnostics': [d.as_json() for d in expansion.diagnostics]}
