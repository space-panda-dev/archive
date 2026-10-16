# 0006. Enrichment derives without mutating, and stays reproducible

Status: accepted

Transcription, loudness analysis, cover-song matching, format identification and
the like produce new knowledge from existing evidence. They must never damage
what they read, and their results must stay explainable.

## Decision

**Enrichment never mutates its inputs.** It reads evidence and produces new
output. Output worth keeping is proposed to Door as new evidence, pinned to the
exact revisions it used; everything else is disposable cache. Whether a kind of
output is kept is a policy decision, not the tool's.

**Tools stand alone.** A transcriber takes a file, not an archive. The
Archive-facing part is a thin adapter: find inputs, run the tool, package the
output with provenance, propose it or discard it. Enrichment tools live in this
repository for now, grouped for convenience; the grouping is not architecture.

**Configuration is always recorded.** The parameters, prompts, and presets a run
used are recorded as inputs of that run, never as payload fields.

**Environments are preserved by policy.** Keeping the software itself (model
weights, binaries, container images) is a policy choice, for example "preserve
the model of any agent whose claims we keep". A preserved environment is an
ordinary artifact linked to the agent's entity.

## Rejected

- **Enrichment writing results directly.** It would bypass Door and make every
  tool an authority.
- **Requiring environment preservation for every result.** Model weights run to
  gigabytes; keep them when reproducing the result matters.
