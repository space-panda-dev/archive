# The feature bank format

This is the on-disk format of the feature bank
([ADR 0073](decisions/0073-proposed-feature-bank.md)) and the files an
extractor exchanges with the runner
([ADR 0074](decisions/0074-proposed-extractor-contract.md)), precisely enough
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
([ADR 0073](decisions/0073-proposed-feature-bank.md)), because the entry may
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
