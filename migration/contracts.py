"""Count top-level V2 contract path stamps without interpreting record payloads."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

_CONTRACT_STAMP = re.compile(r"specs/changes/(?!\.{1,2}/)[^/\\\s\x00-\x1f\x7f]+/requirements\.md")
_SOURCE_CITATION = re.compile(r"specs/(?:[^/\\\s\x00-\x1f\x7f]+/)*[^/\\\s\x00-\x1f\x7f]+\.md")


def _is_safe_source_citation(value: str) -> bool:
    """Recognize relative Markdown citations without treating them as plan stamps."""
    if _SOURCE_CITATION.fullmatch(value) is None:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


class ContractCitationCensus:
    """Incrementally count top-level source contract stamps."""

    def __init__(self) -> None:
        self.citations: Counter[str] = Counter()
        self.schemas: Counter[str] = Counter()
        self.diagnostics: list[dict[str, Any]] = []
        self.records = 0

    def add(self, value: Any, locator: dict[str, Any]) -> None:
        if not isinstance(value, dict) or "contracts" not in value:
            return
        contracts = value["contracts"]
        if not isinstance(contracts, list):
            self.diagnostics.append({**locator, "kind": "malformed_contract_citation", "detail": "top-level contracts must be an array"})
            return
        stamped: set[str] = set()
        for index, contract in enumerate(contracts, start=1):
            citation_locator = {**locator, "citation_index": index}
            if not isinstance(contract, str):
                self.diagnostics.append({**citation_locator, "kind": "malformed_contract_citation", "detail": "contract citation must be a safe relative Markdown path under specs/"})
                continue
            if _CONTRACT_STAMP.fullmatch(contract):
                stamped.add(contract)
            elif not _is_safe_source_citation(contract):
                self.diagnostics.append({**citation_locator, "kind": "malformed_contract_citation", "detail": "contract citation must be a safe relative Markdown path under specs/"})
        if stamped:
            self.records += 1
            schema = value.get("schema")
            self.schemas[schema if isinstance(schema, str) and schema else "<missing>"] += 1
            self.citations.update(stamped)

    def report(self) -> dict[str, Any]:
        return {
            "complete": not self.diagnostics,
            "records_with_plan_contracts": self.records,
            "citations_by_contract": dict(sorted(self.citations.items())),
            "records_by_schema": dict(sorted(self.schemas.items())),
            "diagnostics": self.diagnostics,
        }


def measure(values: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    """Measure an iterable of source records; convenient for synthetic fixtures."""
    census = ContractCitationCensus()
    for value, locator in values:
        census.add(value, locator)
    return census.report()
