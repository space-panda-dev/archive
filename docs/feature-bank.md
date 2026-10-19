# The feature bank format

This is the on-disk format of the feature bank
([ADR 0073](decisions/0073-feature-bank.md)) and the files an
extractor exchanges with the runner
([ADR 0074](decisions/0074-extractor-contract.md)), precisely enough
for a program in any language to read the bank, write an extractor, or
replace the runner. [`enrich/bank.py`](../enrich/bank.py) is the reference
reader and checker, and [`enrich/extract.py`](../enrich/extract.py) the
reference runner. The bank is regenerable: a reader that finds nothing
measures for itself or reports the feature missing; it never fails because
the bank was deleted.

All JSON is UTF-8. All binary numbers are little-endian. Digests are written
`sha256:` followed by 64 lower-case hexadecimal digits.

## Canonical JSON

Keys and parameter digests are computed over **canonical JSON**, which is
the JSON Canonicalization Scheme of [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785)
applied to values limited so that every language's encoder agrees:

- numbers are integers from -(2^53 - 1) to 2^53 - 1, written in decimal
  without a plus sign, an exponent, or a fraction; a fraction is decimal
  text (`"0.85"`), as a derivation run's parameters are
  ([ADR 0064](decisions/0064-derivation-runs.md));
- object keys are ASCII, so sorting them by UTF-16 code unit (RFC 8785) and
  by code point are the same order;
- text is UTF-8 with no lone surrogates. Exactly these characters are
  escaped: `"` as `\"`, `\` as `\\`, U+0008, U+0009, U+000A, U+000C, and
  U+000D as `\b`, `\t`, `\n`, `\f`, and `\r`, and the other characters
  below U+0020 as `\u` and four lower-case hexadecimal digits. Every other
  character, including `/`, `<`, `>`, `&`, U+2028, and U+2029, is written as
  itself (Go's `encoding/json` escapes some of these by default; turn that
  off);
- no whitespace between tokens.

The runner refuses parameters outside these limits. In Python this is
`json.dumps(value, sort_keys=True, separators=(",", ":"),
ensure_ascii=False).encode("utf-8")`;
`test_parameters_are_limited_to_what_every_language_encodes_alike` in
[`enrich/test_bank.py`](../enrich/test_bank.py) shows the escaping.

## The key

An entry's key is `sha256:` and the SHA-256 of the canonical JSON of:

```json
{"contract": "archive-feature-bank-key/1",
 "extractor": "<extractor id>",
 "version": "<extractor version>",
 "parameters": "<digest of the canonical JSON of the parameters>",
 "content": "<the content's digest>",
 "needs": {"<extractor>/<output>": "<key of the entry holding it>", ...}}
```

`parameters` are the extractor's declared parameters with the operator's
configured values replacing them field by field. `needs` has one member for
every feature the extractor's manifest needs, so the keys are computed
upstream first. The test `test_a_key_is_the_digest_of_its_canonical_description`
in [`enrich/test_bank.py`](../enrich/test_bank.py) writes one key out in full.

## Layout

```
<root>/sha256/<ab>/<cd>/<hex>/bank/<extractor id>/<key hex>/entry.json
<root>/sha256/<ab>/<cd>/<hex>/bank/<extractor id>/<key hex>/<output files>
<root>/sha256/<ab>/<cd>/<hex>/bank/<extractor id>/<key hex>.failed.json
<root>/sha256/<ab>/<cd>/<hex>/bank/<extractor id>/<key hex>.lock
<root>/tmp/
```

`<hex>` is the content digest's hexadecimal part and `<ab>` and `<cd>` its
first two pairs, the directory [ADR 0069](decisions/0069-derived-media.md)
gives derived media; `<key hex>` is the key's hexadecimal part. Extractor ids
match `[a-z][a-z0-9-]{0,62}`.

An entry is a directory holding `entry.json` and exactly the files its
outputs name. **It is complete or absent:** a writer builds it under
`<root>/tmp/` on the same file system, writes `entry.json` last, flushes the
files and the directory, and renames the directory into place. A rename that
finds the entry already there means another writer finished first; the
existing entry stands. A directory at an entry's place without a readable
`entry.json` is damage, and a reader refuses it rather than treating it as
missing.

Entries are readable by their owner only (files `0600`, directories
`0700`): a feature can carry the recording, as a stem does.

`<key hex>.failed.json` records the last failure to make that entry
(operational state: the extractor, key, content, error, times, and the end of
its log). It is never read as an entry, and the next success removes it.
`<key hex>.lock` is an empty file a writer holds an exclusive `flock` on
while it makes that entry; a second writer waits for it, then finds the
entry and runs nothing (operational; it may be deleted when no writer
runs). `tmp/` holds work in progress and may be emptied when no writer runs.

**An entry made from a file feature is filed with the content that feature
was made from** ([ADR 0075](decisions/0075-stems-as-content.md)).
When an extractor measures a stem of a recording, the stem's digest (the
`sha256` its entry records) is the new entry's `content` and decides its
key, but the entry lives under the recording's directory:
`sha256/<recording ab>/<recording cd>/<recording hex>/bank/<extractor>/<key hex>/`.
Its failure record and lock sit beside it there. Nothing is ever filed under
a stem's own digest, so a content's directory holds everything made from it.
A reader that knows the recording finds the stem in the recording's entry
and then the measure by the stem's digest in the recording's directory
(`Bank.input_of` and `Bank.entry(..., filed_under=...)` in
[`enrich/bank.py`](../enrich/bank.py)). The request an extractor receives
is the same as for any content: `input` names the stem's path, digest, and
media type.

Entries are never rewritten. Old entries whose keys no installed extractor
gives are stale; any entry, or the whole root, may be deleted at any time.
Every entry of a content is under its `sha256/<ab>/<cd>/<hex>/` directory,
which must be deleted when that content is destroyed
([ADR 0004](decisions/0004-deletion.md)); a stem is the recording.

## `entry.json`

```json
{"contract": "archive-feature-bank-entry/1",
 "key": "sha256:...",
 "extractor": {"id": "loudness-r128", "version": "1"},
 "parameters": {"filter": "ebur128=framelog=quiet", "stream": "0:a:0"},
 "content": "sha256:...",
 "media_type": "audio/wav",
 "needs": {},
 "software": [{"name": "ffmpeg", "version": "8.1.2"}],
 "grids": {},
 "outputs": {"integrated": {"kind": "scalar", "unit": "LUFS", "value": -15.6}},
 "started_at": "2000-01-01T00:00:00.100000Z",
 "ended_at": "2000-01-01T00:00:00.300000Z",
 "runner": {"name": "archive-enrich-extract", "version": "1"}}
```

Every field is present. `key` must equal the key computed from `extractor`,
`parameters`, `content`, and `needs`; a reader refuses an entry where it does
not. `software` is what the extractor reported, at most 32 pairs. Times are
UTC. A derivation run that reads an entry copies its `software` into the
run's own, with the feature, the extractor's id and version, and the key
([ADR 0073](decisions/0073-feature-bank.md)), because the entry may
be deleted and the run may not lose what it held.

Each member of `outputs` repeats the manifest's declaration of that output
(without its description) and adds:

| Kind | Added fields | File |
|---|---|---|
| `series` | `shape`: `[rows, columns]` | `<name>.<dtype>`: rows x columns elements, row-major |
| `scalar` | `value`: a finite number, text, or a boolean | none |
| `events` | none | `<name>.json`: `[{"time", "value"?, "label"?}, ...]`, times non-decreasing |
| `spans` | none | `<name>.json`: `[{"start", "end", "value"?, "label"?}, ...]`, start <= end |
| `file` | none | `<name><suffix>`, of the declared `media_type` |

Every output with a file also has `file` (its name), `bytes`, and `sha256`.
A series' `dtype` is one of `f32`, `f64`, `i16`, `i32`, `i64`, `u8`, all
little-endian; floating-point series hold only finite values. Times in
events and spans, and a grid's sample 0, are measured from the first decoded
sample of the stream the extractor read (for `frames`, the first audio
stream), unless the output's `unit` says otherwise. A stream's start offset
in its container is not recorded: a reader that relates two streams of one
file, such as a video's frames to its audio's `frames` series, adds the
streams' start times (from `ffprobe`) itself.

### Grids

A series that names a `grid` has its time base in `grids`:

```json
"grids": {"frames": {"sample_rate": 22050, "hop": 2048, "frame": 4096, "first_centre": 0}}
```

Row `k` is centred on sample `first_centre + k * hop` of a signal sampled at
`sample_rate`, which is `(first_centre + k * hop) / sample_rate` seconds from
the start of the stream; `frame`, when present, is the window length in
samples. All four are integers. For `frames`, row `k` is the Essentia frame
centred on analysis sample `k * 2048`, zero-padded where it reaches past the
audio, and `n` decoded samples make `ceil(n / 2048) + 1` rows.

## The extractor manifest: `extractor.json`

```json
{"contract": "archive-extractor/1",
 "id": "loudness-r128",
 "version": "1",
 "description": "...",
 "command": ["${sh}", "loudness.sh"],
 "accepts": ["audio/*", "video/*"],
 "requires": ["ffmpeg"],
 "needs": [],
 "parameters": {"filter": "ebur128=framelog=quiet", "stream": "0:a:0"},
 "outputs": {"integrated": {"kind": "scalar", "unit": "LUFS", "description": "..."}}}
```

`contract`, `id`, `version`, `command`, `accepts`, and `outputs` are
required; `description`, `requires`, `needs`, and `parameters` may be
given; nothing else is allowed. `version` matches
`[A-Za-z0-9][A-Za-z0-9.]{0,39}`, so that `archive-extractor.<id>@<version>`
is a Spool capability. Output names match `[a-z][a-z0-9_]{0,62}`.
A series declares `dtype`, `columns`, and optionally `grid`; a file declares
`media_type` and `suffix` (such as `.flac`); any output may have `unit` and
`description`. `accepts` holds media type patterns, `type/subtype` with `*`
for either part.

In `command`, `${name}` is the program the operator configured under that
name (`python` defaults to the runner's interpreter, `sh` to `/bin/sh`), and
a word naming a file in the extractor's directory becomes its absolute path.

## The request

The runner makes a scratch directory, writes `request.json` there as
canonical JSON, and runs the command with that path as its last argument and
the scratch directory as its working directory:

```json
{"contract": "archive-extractor-request/1",
 "extractor": {"id": "spectral-summary", "version": "1"},
 "key": "sha256:...",
 "parameters": {...},
 "input": {"path": "/abs/take.flac", "digest": "sha256:...", "media_type": "audio/flac"},
 "needs": {"frames/centroid": {"kind": "series", "dtype": "f32", "columns": 1, "unit": "Hz",
                               "shape": [4019, 1], "file": "centroid.f32", "bytes": 16076,
                               "sha256": "sha256:...", "path": "/abs/bank/.../centroid.f32",
                               "entry": "sha256:<key of the frames entry>",
                               "grid": {"name": "frames", "sample_rate": 22050, "hop": 2048,
                                        "frame": 4096, "first_centre": 0}}},
 "output_dir": "/abs/bank/tmp/run-.../spectral-summary-.../out"}
```

Each need is the providing entry's output description, with `path` for any
output that has a file, `entry` for the providing key, and its grid inlined.
The environment also carries `ARCHIVE_EXTRACTOR_REQUEST`,
`ARCHIVE_EXTRACTOR_INPUT`, and `ARCHIVE_EXTRACTOR_OUTPUT`.

## The result

The extractor writes its outputs' files and `result.json` into `output_dir`,
and exits 0:

```json
{"contract": "archive-extractor-result/1",
 "software": [{"name": "essentia", "version": "2.1b6.dev1438"}],
 "grids": {"frames": {"sample_rate": 22050, "hop": 2048, "frame": 4096, "first_centre": 0}},
 "outputs": {"hpcp": {"shape": [4019, 12]}, "seconds": {"value": 373.0}, "beats": {}, "vocals": {}}}
```

`outputs` has exactly the declared outputs: a series gives only its `shape`,
a scalar only its `value`, and every other kind an empty object. The
directory must hold nothing but `result.json` and the declared files, each
a regular file with one link: a symbolic or hard link is refused, since the
runner makes every file owner-only and a hard link would change the linked
file (perhaps the content itself). The runner replaces `result.json` with `entry.json` when it files the entry.

## The shipped extractors

The extractors in [`enrich/extractors/`](../enrich/extractors/), each after
what it needs, with the version that goes into its entries' keys, the
features it needs (by extractor, then output), and its outputs by kind. The
manifests are authoritative; `test_the_format_lists_every_shipped_extractor_as_its_manifest_declares_it`
in [`enrich/test_bank.py`](../enrich/test_bank.py) fails when this table and
the manifests disagree. What each output means, its units, and its
parameters are in the manifest's descriptions and in the
[`enrich/README.md`](../enrich/README.md#extractpy-the-feature-bank) table.

| Extractor | Version | Needs | Outputs |
|---|---|---|---|
| `beats-beatthis` | 1 | none | scalars `beat_count`, `downbeat_count`, `median_bpm`; events `beats`, `downbeats` |
| `chords-chordino` | 1 | none | scalars `chord_count`, `seconds`; spans `chords` |
| `chords-jiang` | 1 | none | scalars `chord_count`, `decoded_in_top3`; spans `chords`; files `readings` |
| `frames` | 1 | none | series `hpcp`, `rms_db`, `centroid`, `rolloff`, `flatness`, `flux`, `onset_hfc`, `mfcc`, `melbands`; scalars `samples`, `seconds` |
| `colour` | 3 | `frames`: centroid, hpcp, rms_db | series `colour`; files `palette`, `preview` |
| `f0-pesto` | 1 | none | series `f0`; scalars `rows_from_tracker` |
| `f0-torchcrepe` | 1 | none | series `f0`; scalars `rows_from_tracker` |
| `loudness-r128` | 1 | none | scalars `integrated` |
| `melody` | 2 | none | series `pitch`, `confidence`, `voiced`, `midi`, `cents`, `level_db`; scalars `voiced_fraction`, `melodia_voiced_fraction`, `loud_db`, `gate_db`, `reference_hz`, `samples`; spans `notes` |
| `notes-basic-pitch` | 1 | none | scalars `note_count`, `seconds`; files `notes` |
| `quality` | 3 | `frames`: samples | series `correlation`, `peak_db`; scalars `sample_rate`, `channels`, `seconds`, `integrated`, `loudness_range`, `true_peak`, `sample_peak`, `full_scale_samples`, `clipped_seconds`, `dc_offset`, `whole_correlation`, `anti_phase`, `false_stereo`, `noise_floor`, `snr`, `bandwidth`, `band_limited`, `hum_examined`; events `clicks`, `discontinuities`; spans `clipping`, `overs`, `gaps`, `noise_bursts`, `hum` |
| `rhythm` | 4 | none | series `tempo`, `onset_count`, `onset_deviation`, `offgrid`, `chunk_bpm`, `chunk_confidence`, `primary_tempo`, `tempo_support`, `downbeat_margin`; scalars `bpm`, `confidence`, `onset_rate`, `seconds`, `primary_bpm`, `meter`; events `beats`, `onsets`, `pulse`, `downbeats`, `tempo_changes`; spans `tempo_spans` |
| `stems` | 1 | none | series `levels`; scalars `vocals_rms_db`, `vocals_peak_db`, `vocals_clamped`, `drums_rms_db`, `drums_peak_db`, `drums_clamped`, `bass_rms_db`, `bass_peak_db`, `bass_clamped`, `other_rms_db`, `other_peak_db`, `other_clamped`, `mix_rms_db`, `samples`, `seconds`; files `vocals`, `drums`, `bass`, `other` |
| `stems-6s` | 1 | none | series `levels`; scalars `vocals_rms_db`, `vocals_peak_db`, `vocals_clamped`, `drums_rms_db`, `drums_peak_db`, `drums_clamped`, `bass_rms_db`, `bass_peak_db`, `bass_clamped`, `guitar_rms_db`, `guitar_peak_db`, `guitar_clamped`, `piano_rms_db`, `piano_peak_db`, `piano_clamped`, `other_rms_db`, `other_peak_db`, `other_clamped`, `mix_rms_db`, `samples`, `seconds`; files `vocals`, `drums`, `bass`, `guitar`, `piano`, `other` |
| `structure` | 2 | `frames`: hpcp, rms_db, mfcc, seconds; `rhythm`: onsets, beats | series `novelty`; scalars `floor_db`, `floor`, `reference_db`, `threshold_db`; spans `playing`, `tuning`, `count_ins`, `false_starts`, `endings`, `sections`, `loops` |
| `tonal` | 5 | `frames`: rms_db | series `pitched`, `tuning_windows`, `chroma`, `bass_chroma`, `dissonance`; scalars `tuning_frequency`, `tuning_cents`, `tuning_confidence`, `tuning_window_count`, `drift_spread_cents`, `detuned`, `key`, `scale`, `mode`, `scale_notes`, `key_strength`, `collection`, `collection_margin`, `tonic_margin`, `tonic_confidence`, `tonic_named`; spans `tuning_drift`, `detuned_spans`, `keys`, `key_regions` |

Five of them run a model in an environment of the operator's, never the
archive's, each under a program name the operator configures, so `run all`
skips any that is not configured: `stems` (`${demucs-python}`), `stems-6s`
(`${stems-6s-python}`, which can be the same interpreter, so that a second
separation is made only where it is asked for), `notes-basic-pitch`
(`${basic-pitch-python}`), and `f0-pesto` and `f0-torchcrepe`
(`${f0-python}`). Each checks its model files against the digests in its
parameters before loading them.

A version changes whenever an extractor's code or outputs do, so an older
entry is never read as a newer one (ADR 0073). The versions above 1, and
what each changed:

- `rhythm` 2 added the primary tempo level among the tracker's 1:2 to 2:1
  relations, chosen by onset support, with its alternatives' support,
  meter, downbeats, spans of one level and meter, and changes that say
  `meter` where the beat persists; 3 decides each take on its own, prefers
  the tracker's own octave, and publishes downbeats only where
  `downbeat_margin` is clear; 4 places bar lines by the low accent less the
  backbeat's and by the beat-synchronous chroma, publishes them from a
  margin of 1 (on 17 of 18 tracks of a test album, where 3 published them
  on 8), prefers bars of 5 to 4 in a song that changes meter, and takes a
  second whose window cannot lay every level as no evidence.
- `tonal` 1 and 2 measured tuning, key, and chords with Essentia's
  `TuningFrequency`, `Key`, and `ChordsDetection` (2 records its second
  decode and blanks harmony on a detuned recording); 3 takes the tuning as a
  confidence-weighted median of windows, publishes profiles referenced to
  it (`chroma`, `bass_chroma`), reports the key as a note collection with
  a tonic and mode while keeping `key` and `scale`, and moves chords to
  `chords`; 4 leaves mains hum out of the tuning and profiles, names a
  tonic only where it leads, and draws `key_regions` by note collection; 5
  reports a recording's tuning only from enough confident windows (four,
  and half of those at least half pitched), chooses the tonic among the
  collection's notes by their share of the bass, favouring the major or
  minor tonic, adds a calibrated `tonic_confidence` that decides
  `tonic_named`, and drops `key_margin`.
- `structure` 2 measures the playing threshold from a noise floor only
  when the recording has one (steady or unpitched quiet), so a master is
  all playing, and says which in `floor`.
- `quality` 2 scaled the detectors' frames with the sample rate and
  dropped quantisation-level bursts; 3 reports
  clipping only at full scale or as long flat runs at the recording's own
  peak, overs on their own, events that stand out from the music around
  them or stand alone, and hum only at mains frequencies.
- `melody` 2 resolves pitch in 2-cent bins, gates voicing by level, and
  adds `level_db`.
- `colour` 2 stopped copying the peaks into the bank and checks its
  palette; 3 sets the centroid hue's
  default range to 500 Hz to 4 kHz, which
  [`enrich/hue_range.py`](../enrich/hue_range.py) can replace with a
  collection's own.

### Retired extractors

An extractor is retired by removing it from the catalog: `run all` no longer
makes its entries, and nothing that ships reads them. Its entries stay where
they are and stay readable, since an entry is described by its own
`entry.json` (`bank.read_entry` on its directory needs no catalog), and they
are regenerable: run the retired extractor's directory, from the history,
with `--extractors <dir>`, and the same version and parameters give the same
key. A retired id is not reused for another method, so an old entry is never
read as a new one.

- `chords` (Essentia profiles, retired after version 2). Version 1 named a
  chord per half beat from `tonal`'s profiles by a Viterbi path, with a bass
  of its own and three candidates a span; 2 priced the six-note m11 above
  the five-note chords. On an album of 18 adversarial jazz and soul masters
  it was the worst of five chord tools on every measure: in practice it
  named only major and minor triads, 53% of its spans were shorter than
  half a second, its bass matched three transcribed bass lines 18 to 53% of
  the time where `chords-jiang`'s matched 47 to 63%, and its roots matched
  another machine's labels on ten tracks 40% of the time where
  `chords-chordino`'s matched 53%.
  `chords-jiang` (a learned large vocabulary, with weighted readings) and
  `chords-chordino` (a second opinion of another kind) replace it. Its last
  source is `enrich/extractors/chords/` at the commit before its removal.
