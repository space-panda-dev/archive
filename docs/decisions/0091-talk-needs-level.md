# 0091. Talk needs level, and a studio preset never says talk

Status: accepted (operator, 2026-10-19); amends the presets of
[ADR 0066](0066-span-suggestions.md)

The span detector ([ADR 0066](0066-span-suggestions.md)) classed a span
`talk` by a relative rule ported from the V2 detector: each span's key
confidence and spectral flatness are ranked among the recording's own spans,
and a mean rank at or below 30% is talk. A relative rule is a quota. About a
fifth of every recording's spans became talk whatever it held, loud singing
included: on the recordings that hold accepted song occurrences, 17.5% of the
accepted song time (11.1 hours) was labelled talk, and the six studio
recordings of the studio test, which hold no talk at all, were labelled 17%
to 49% talk. Talk spans are left out of match windows (`cover.SEARCH`
`span_classes`), so a song that is mostly "talk" is hardly searched: nine
linked occurrences were more than half talk, and only three of them were
reached by a window.

Level separates the two. Talk between songs is quieter than the band, and
the rule never looked at level. Nor could a preset say "never talk": a rank
threshold of 0 still labels the lowest-ranked span.

## Decision

**The rank only nominates talk; level decides it.** A span whose mean rank
is at or below `rank_threshold_percent - rank_band_percent` is `talk` only
when its median hop level is more than `talk_margin_db` below the
recording's reference level, the `talk_reference_percentile` of all its hop
levels. A nominated span that is louder is `unknown`, which match windows
search. Music, quiet, and the edges are unchanged.

**`assign_talk` turns talk off.** A preset with `assign_talk` 0 never says
`talk`; its nominated spans are `unknown`. The new **`studio`** preset is
`jam` with `assign_talk` 0, for masters and studio recordings. `capture
import --detector-preset studio` uses it; `jam` stays the default.

**The values.** `jam` and `show` take `talk_reference_percentile` 80 and
`talk_margin_db` 10. They are new keys, so each preset is a new parameters
Artifact, and the rule is a new method: `archive-enrich/detect-spans`
version 2, agent `archive-enrich-detect@0.2.0`. As ADR 0066 requires, a
changed preset is a new run; earlier runs' suggestions remain and are not
superseded.

## How the rule and the margin were chosen

On the 21 recordings that hold accepted song occurrences (the 17 of the
window matcher's calibration workset and four more), with each recording its
own session except two that share an occurrence across a rollover (20
sessions). Song time is the first track of each accepted occurrence's
selection. There are no labels of real talk, so the only measure of what a
rule keeps is the time it calls talk outside accepted songs, some of which
may be noodling or tuning rather than talk.

| Rule (each session classed with what the others fitted) | Fitted under | Song time called talk | Linked occurrences over 25% talk | Talk outside songs |
|---|---|---|---|---|
| Rank alone (version 1, nothing fitted) | | 17.5% | 16 of 96 | 148 min |
| Rank and median level | 2% | 1.8% | 0 | 84 min |
| Rank and mean level | 2% | 2.1% | 1 | 74 min |
| Absolute: key confidence and flatness thresholds | 2% | 2.1% | 0 | 45 min |
| Rank and median level | 1.5% | 1.5% | 0 | 55 min |
| Rank and mean level | 1.5% | 1.6% | 0 | 51 min |
| Absolute: key confidence and flatness thresholds | 1.5% | 1.6% | 0 | 28 min |

For each held-out session, the margin (or the threshold pair) was the one
that calls the most time talk outside songs while keeping the other
sessions' song time under the bound and no linked occurrence over 25%. The
absolute alternative the investigation measured (key confidence below 0.40
and flatness above 0.003) keeps half as much talk at the same cost, and its
thresholds are fractions a preset of integers cannot hold. The median level
does better than the mean, and is less moved by a single loud hit.

Fitted on all sessions under 2%, the median margin is **10 dB** (the folds
chose 9 to 12). In sample, 1.99% of song time is talk, the most-talk linked
occurrence is 15% talk, and 100 minutes outside songs stay talk. The six
studio recordings, 17% to 49% talk under version 1, have none under
`studio`, nor under the new `jam`.

The window matcher's calibration (`enrich/calibrate_windows.py`) ran on the
x86-64 laptop for every candidate, on the same read-only archive copy, from
a workset measured with each preset in memory (98 accepted occurrences on 17
recordings; same corpus, same edges, only the classes differ):

| | Version 1 | Margin 13 | Margin 11 | Margin 10 |
|---|---|---|---|---|
| Occurrences reached | 90 | 97 | 97 | 97 |
| Suggested, lenient | 65 | 67 | 67 | 67 |
| Suggested, strict | 56 | 55 | 55 | 56 |
| Wrong suggestions, lenient | 2 | 2 | 2 | 2 |
| Of the nine linked occurrences over half talk: reached | 3 | 9 | 9 | 9 |
| ... suggested | 1 | 4 | 4 | 4 |

A wider margin demotes more of the talk around songs, which joins the
song's window: of the four occurrences margin 13 stops suggesting under the
strict rule, three are still suggested with the right Work at about the same
distance, in a window now more than twice their length. Margin 10, the fit
under 2%, keeps that talk and gives up nothing. The 1.5% bound was tried
for headroom; its margin (13 dB) costs a strict suggestion, and the 2%
bound's held-out song time (1.8%) is already under the target.

## Consequences

- Talk suggestions are fewer and quieter; a review client sees `unknown`
  where `jam` version 1 said talk over singing, and match windows reach the
  songs that were hidden.
- The margin sits on the edge of the target in sample (1.99%): one more
  loud span of song called talk would cross it. It rests on song time alone. A short list of candidate talk spans
  waits for the operator to label by ear ([backlog](../backlog.md)); with
  those labels the margin can be fitted on what it keeps as well as what it
  avoids.
- Level is relative to the recording's own loud hops, so a recording that is
  all talk has no reference to be quiet against; its talk-like spans come
  out `unknown`, which is searched, not hidden.

## Alternatives considered

- **A fixed level in dBFS.** Recorders and gains differ by more than the gap
  between talk and music.
- **Absolute key-confidence and flatness thresholds.** Measured above: less
  talk kept at the same cost, and not expressible in integer presets.
- **Searching talk spans too.** It would hide nothing, but a talk class that
  is a fifth of every recording tells a reviewer nothing either.
- **A rank threshold of 0 for studio recordings.** The lowest-ranked span
  still reaches it.
