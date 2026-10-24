# 0119. Door evaluates contract schemas, patterns and canonical numbers with its own code, so its behaviour is part of the specification

Status: proposed (2026-10-24). Records a choice made in code without a
record; builds on ADRs [0014](0014-boundaries-and-languages.md),
[0021](0021-portable-identifiers.md),
[0025](0025-versioned-json-ld-contexts.md) and
[0027](0027-core-payloads-and-installed-contracts.md). Part of its history is
reconstructed, and says so where it is.

## Context

Door validates every record against JSON Schema bundles and canonicalises it
under RFC 8785 before hashing (P-3, P-11). Four pieces of that work are
Door's own code rather than a library's:

| Piece | Where | Size |
|---|---|---|
| a JSON Schema 2020-12 evaluator | `door/src/Archive/Door/Contracts.hs`: `schemaKeywords`, `evaluateSchema` and the `evaluate*` functions, `explainMismatch`; `door/src/Archive/Door/SchemaObjectKeywords.hs`: `evaluateObjectKeywords`, `objectKeywordNames` (the D-18 object keywords) | about 420 lines |
| a regular expression engine for `pattern` | the same module: `compilePattern`, `parsePattern`, `matchesPattern` | about 140 lines |
| RFC 8785 number normalisation | `door/src/Archive/Door/Canonical.hs`: `normalizeIJson`, `binary64Scientific`, `shortestDecimal` | about 90 lines |
| SHA-1 for UUIDv5 | `door/src/Archive/Door/Identity.hs`: `sha1` | about 50 lines |

RFC 8785 serialisation itself is not in-house: `encodeCanonical` is aeson's
`Data.Aeson.RFC8785`. Only the step before it, turning each number into the
binary64 value JCS prints, is Door's.

No decision record says why any of this is hand-written. The repository's
public history starts at one squashed commit (`4aea43d`, "the first public
commit of Archive, from the private history's 420a724"), so `git log -S` finds
no commit message that gives a reason; the private history is not available
here. What can be sourced is in the code's own comments, the specification,
and earlier records. This record collects it, says which parts of the
specification the code is the only full statement of, and draws the
consequence for tests.

## Decision

**The four pieces stay in Door, each for the reason below.** A reason marked
*reconstructed* is inferred from the code and the specification, not found
written down; [open questions](../open-questions.md#door) asks the operator to
confirm or correct it.

### 1. The schema evaluator: a closed subset, refused beyond it

Sourced:

- The evaluator "deliberately supports a small, closed subset of 2020-12. A
  bundle is specification-owned data, so silently accepting a new keyword
  would be worse than refusing a bundle until the evaluator grows" (the
  comment on `schemaKeywords`). An unknown keyword is refused when the bundle
  loads. This is INV-24: an unsupported contract is a result, never an empty
  schema (ADR 0027 rejects "treat a missing contract as an empty schema").
- Schemas are closed and self-contained with no remote `$ref` (D-8, the
  installed transition bundles; ADR 0027 rejects fetching `$ref` targets
  because "validation would depend on a mutable network service and the
  archive would not stand alone"). The evaluator resolves only local JSON
  Pointers.
- Door validates without JSON-LD processing (ADR 0025, D-6).
- Door's `x-archive-*` profile rules sit beside the keywords, and "generic
  JSON Schema success alone MUST NOT bypass them" (D-12, D-13); a Door that
  installs a bundle declaring a rule it does not enforce must refuse every
  record citing it (D-18).

Reconstructed: a general validator package would have to be wrapped to
refuse every keyword outside the subset, to refuse remote references, and to
give Door's refusal text, which names the instance's JSON Pointer and the
failing keyword (`explainMismatch`); at that point the wrapper is most of the
evaluator. Door has also been the one Haskell component since ADR 0014, and
the library has few dependencies.

**The subset the specification pins**, and where:

| What | Where |
|---|---|
| JSON Schema 2020-12, closed, self-contained, no remote `$ref` | D-8 (ADR 0027) |
| `maxLength` counts Unicode code points | D-13 (specification 0.0.5) |
| `anyOf`, `not`, `if`/`then`/`else` (no `then` or `else` without `if`), `uniqueItems` by JSON equality; every branch evaluated, so a malformed branch is refused | D-17 (0.0.9) |
| `propertyNames`, `minProperties`, `maxProperties`, applying only to objects | D-18 (0.0.10) |

The base set (`$schema`, `$defs`, local `$ref`, `type`, `const`, `enum`,
`required`, `properties`, `additionalProperties`, `items`, `minItems`,
`maxItems`, `contains`, `minContains`, `maxContains`, `allOf`, `pattern`,
`minLength`, `format` with only `date-time`, `minimum`, `maximum`, and the
annotations `$comment`, `title`, `description`, `default`, `deprecated`,
`examples`, `readOnly`, `writeOnly`) is not listed in the specification:
`schemaKeywords` is its only statement. `schemaKeywords` also appends
`objectKeywordNames` (the D-18 keywords, from
`Archive.Door.SchemaObjectKeywords`) and `profileRuleNames` (the
`x-archive-*` profile rules). That gap is an open question, not
something this record settles.

### 2. The pattern engine: the subset D-14 pins

Sourced: specification 0.0.5, D-14, pins it. "The pattern subset Door
evaluates is: anchored patterns of literals, escaped metacharacters, ASCII
character classes without negation, groups with alternation, and the
quantifiers `?`, `*`, `+`, `{n}`, and `{n,m}`; any other syntax MUST be
refused when the bundle is loaded." JSON Schema 2020-12 says a `pattern` is
an ECMA-262 regular expression; D-14 narrows that to constructs meant to read
the same in ECMA-262 and in Door. They do not always: the known differences
(among them that Door reads `^a|b$` as `^(a|b)$`, where ECMA-262 reads
`(^a)|(b$)`) are listed in [open questions](../open-questions.md#door).
`compilePattern` runs at bundle load
so a pattern outside it is refused before any record is checked. The engine
is a backtracking matcher over every way a prefix can be consumed, and each
further repetition must consume input, so an unbounded repeat of a pattern
that can match the empty string still terminates (the comment on
`repeatNode`). Contract patterns are short and anchored; the engine's
worst case on them has not been measured.

Reconstructed: no Haskell regular expression package speaks ECMA-262. The
common ones are POSIX (whose leftmost-longest alternation differs from
ECMA-262's ordered alternation only where a group's match is observed, which
`pattern` never observes) or bindings to a C library, a native dependency in
a component that otherwise has none. Either way Door would still need its
own parser to refuse everything outside D-14, and the parser is most of the
engine.

### 3. Canonical numbers: ECMAScript's spelling, which the libraries do not give

Sourced: JCS prints a number as ECMAScript's `Number::toString` of its
binary64 value. "`Scientific.fromFloatDigits` and aeson's `ToJSON Double`
intentionally preserve GHC's `Show` spelling in boundary cases, which is not
the ECMAScript `Number::toString` result required by JCS" (the comment on
`binary64Scientific`). Door therefore rounds each number to binary64, refuses
a value outside the finite range with the RFC's own reason, and finds the
shortest decimal that rounds back to it by exact rational arithmetic, before
handing the value to aeson's `encodeCanonical`. A wrong spelling would change
`revision_id` (P-3), which is why this is not left to a library default.
`door/test` checks RFC 8785 Appendix B's number vectors.

### 4. SHA-1: a namespacing digest, whose reason has lapsed

Sourced: UUIDv5's SHA-1 "is namespacing, not an integrity claim" (ADR 0021),
and "the SHA-1 implementation is kept here so identifier allocation does not
acquire a second package dependency merely for a digest that is not an
integrity claim" (the module comment of `Archive.Door.Identity`). The
library now depends on `cryptohash-sha1` anyway, for verifying SHA-1 Git
objects in `Archive.Door.RecordCommit`, so that reason no longer holds.
If the operator accepts decision S below, this record does not keep the
hand-written SHA-1: replacing it with the package's, behind the same `sha1`
name, is then a refactor any change may make, on one condition. The change
must keep every existing UUIDv5 identifier byte for byte, and show it with a
test: known RFC 9562 UUIDv5 vectors, or identifiers Door allocates today.
Until decision S is accepted the hand-written SHA-1 stays.

### 5. What changes the evaluators' behaviour

The evaluators' behaviour is part of the specification wherever the
specification's text stops: an archive written by this Door is valid because
this code accepted it, and another implementation must accept and refuse the
same records. So:

- A change that makes the schema evaluator, the pattern engine or the number
  normalisation accept or refuse anything it did not is a specification
  change, made in a release with a decision record, never as a refactor.
- A keyword, a pattern construct or a profile rule is added to the code only
  together with the specification text that names it, as D-17 and D-18 did.
- A refactor of these modules keeps every acceptance, every refusal and every
  refusal message byte for byte.

## Consequences

- The evaluators need tests of their own, not only admissions that happen to
  reach them. `compilePattern`, `matchesPattern` and `evaluateSchema` are not
  exported, and no test in `door/test` names them; they are exercised only
  through bundle loading and record admission. Tests should cover, per
  construct: each pattern form D-14 lists accepted and matched the way Door
  matches it today (whether that agrees with ECMA-262 is a separate, open
  question), each form it does not list refused at load, each keyword
  accepted and its refusal text, and an unknown keyword refused. Properties
  (test-only QuickCheck) suit the pattern engine and the number
  normalisation: a generated pattern inside the subset against generated
  strings, and every binary64 value's spelling round-tripping to itself.
- Where the code and the specification's words differ, the code is what
  archives were written under, and the difference is recorded as an open
  question rather than fixed silently.
- Door keeps its small dependency set; the cost is about 700 lines Door owns
  and must test.

## Alternatives considered

- **A general JSON Schema validator package.** It would accept keywords
  outside the closed subset unless wrapped, may resolve remote references
  ADR 0027 forbids, and gives none of Door's refusal text
  (reconstructed, section 1).
- **A regular expression package (POSIX or a C binding).** No dialect is
  ECMA-262, a C binding is a native dependency, and Door would still need its
  own parser to refuse syntax outside D-14 (reconstructed, section 2).
- **Aeson's own number output.** It keeps GHC's `Show` spelling in boundary
  cases, which is not JCS's (sourced, section 3).
- **Patterns as full ECMA-262.** The specification would inherit a whole
  language, and every implementation would need a conforming engine to agree
  on admission; D-14 chose a subset instead.
- **Leaving the reasons unrecorded.** The next refactor would treat these
  modules as ordinary code and could change what an archive admits.

## New nouns

None.

## Decisions for the operator

- **R.** Keep the schema evaluator, the pattern engine and the number
  normalisation in Door, with section 5's rule that their behaviour changes
  only through the specification.
- **S.** Let the hand-written SHA-1 be replaced by `cryptohash-sha1`'s, since
  its stated reason has lapsed, with a test that existing UUIDv5 identifiers
  stay byte-identical.
- **T.** Confirm or correct the reconstructed reasons in sections 1 and 2.
- **B.** Whether a later release lists the base keyword set in the
  specification, and settles the pattern differences listed in open
  questions.
