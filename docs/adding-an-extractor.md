# Adding an extractor

An extractor writes features into the feature bank
([ADR 0073](decisions/0073-proposed-feature-bank.md)) under the extractor
contract ([ADR 0074](decisions/0074-proposed-extractor-contract.md)). It is a
directory with a manifest and a program in any language. Adding one changes
no other file: the runner finds it, orders it after what it needs, runs it
only when its entry is missing, and checks what it writes before filing it.
The byte-level format is in [the feature bank format](feature-bank.md).

## The steps

1. **Pick an id and declare the outputs.** Make `enrich/extractors/<id>/`
   (or a directory of your own, passed with `--extractors`) holding
   `extractor.json`: the id, a version, the command, the media types it
   accepts, the features it needs, its parameters, and each output's kind.
   Prefer reading `frames` series over decoding again: `"needs":
   ["frames/rms_db", "frames/hpcp"]` gives the program their paths, shapes,
   and grid. A feature you can do without goes in `"optional"`: it is in
   the request when the catalog has its extractor and absent otherwise, so
   the program checks for it (`melody` reads `stems/vocals` this way).
2. **Write the program.** It receives the path of `request.json` as its last
   argument (and, for convenience, `ARCHIVE_EXTRACTOR_INPUT` and
   `ARCHIVE_EXTRACTOR_OUTPUT` in its environment). It writes each declared
   output's file and `result.json` into the output directory, and exits 0.
   It refuses parameters it does not implement rather than measuring with
   others. What it prints goes to the failure log, nowhere else. It runs in
   a process group of its own, and when it exits or runs past `--timeout`
   the runner stops the whole group, so children it starts (an `ffmpeg`, a
   separator) end with it. Every output is a new file: a hard link to the
   input, or to anything else, is refused, since the runner makes outputs
   owner-only and would change the linked file too.
3. **Name its interpreter, if it needs one.** A command word `${name}` is a
   program the operator configures: `--program name=/path` or
   `{"programs": {"name": "/path"}}` in the file given to `--config`. A stem
   separator in its own environment is `["${demucs-python}", "separate.py"]`.
4. **Test it through the runner.** Run the real runner on synthetic audio,
   read the entry back with `bank.Bank`, and assert the second run is a hit
   with process launch replaced by a call that raises
   ([`enrich/test_bank.py`](../enrich/test_bank.py) does this for the example
   below).
5. **Change its version whenever its outputs can change.** A new version is
   a new key, so the old entries are never read again; nothing needs
   deleting. The key holds the version, not the code, so an edit without a
   new version would serve the old entries' bits. For a shipped extractor,
   `test_a_shipped_extractor_changes_only_with_its_version` pins a digest of
   its files (and of any file it imports from outside its directory) per
   version: add yours to `SHIPPED` in `enrich/test_bank.py`, and when the
   test fails, bump the version or, if no output can change, record the new
   digest. A version is letters, digits, and dots, at most 40 characters,
   so that it is also a Spool capability's version.

Feature names are `<extractor>/<output>`. Keep outputs on an existing grid
where you can: a series on the `frames` grid lines up row for row with every
other `frames` series.

## A worked example in shell

`loudness-r128` measures a recording's integrated loudness with `ffmpeg` and
writes one scalar. It is in
[`enrich/extractors/loudness-r128/`](../enrich/extractors/loudness-r128/extractor.json).

Its manifest:

```json
{
 "contract": "archive-extractor/1",
 "id": "loudness-r128",
 "version": "1",
 "description": "Integrated loudness of the first audio stream, EBU R 128, by ffmpeg's ebur128 filter. A shell script: the worked example of an extractor in another language.",
 "command": ["${sh}", "loudness.sh"],
 "accepts": ["audio/*", "video/*"],
 "requires": ["ffmpeg"],
 "needs": [],
 "parameters": {"filter": "ebur128=framelog=quiet", "stream": "0:a:0"},
 "outputs": {
  "integrated": {"kind": "scalar", "unit": "LUFS", "description": "integrated loudness; -70.0 is the gate's floor, silence"}
 }
}
```

Its program, `loudness.sh`:

```sh
#!/bin/sh
# The loudness-r128 extractor: a whole recording's integrated loudness
# (EBU R 128, as ffmpeg's ebur128 filter measures it), in LUFS.
#
# The worked example of docs/adding-an-extractor.md: an extractor in a
# language that is not Python. The runner invokes it as
# `loudness.sh <request.json>` and also names the input and the output
# directory in the environment, so it needs no JSON parser.
set -eu

input=${ARCHIVE_EXTRACTOR_INPUT:?the runner names the input}
out=${ARCHIVE_EXTRACTOR_OUTPUT:?the runner names the output directory}
ffmpeg=${FFMPEG:-ffmpeg}

# The settings are fixed. The request is canonical JSON, so its parameters
# are exactly these bytes when they are the ones this script implements.
grep -qF '"parameters":{"filter":"ebur128=framelog=quiet","stream":"0:a:0"}' "$1" || {
  printf 'loudness-r128 1 has fixed settings; the request names others\n' >&2; exit 1; }

# The filter prints its summary last; " I: <n> LUFS" is the integrated value.
log=$("$ffmpeg" -nostdin -hide_banner -nostats -i "$input" -map 0:a:0 \
      -af ebur128=framelog=quiet -f null - 2>&1) || {
  printf 'ffmpeg could not decode %s\n' "$input" >&2; exit 1; }
value=$(printf '%s\n' "$log" | sed -n 's/^ *I: *\(-\{0,1\}[0-9][0-9]*\.[0-9]\) LUFS$/\1/p' | tail -n 1)
[ -n "$value" ] || { printf 'ffmpeg reported no integrated loudness\n' >&2; exit 1; }

version=$("$ffmpeg" -version | sed -n '1s/^ffmpeg version \([^ ]*\).*/\1/p')

printf '{"contract":"archive-extractor-result/1",' >"$out/result.json"
printf '"software":[{"name":"ffmpeg","version":"%s"}],' "${version:-unknown}" >>"$out/result.json"
printf '"outputs":{"integrated":{"value":%s}}}\n' "$value" >>"$out/result.json"
```

Run it:

```sh
enrich/extract.py run loudness-r128 --content take.flac --bank /srv/feature-bank
```

The answer names the entry; the value is in its `entry.json`, or from Python:

```python
from pathlib import Path
import bank
entry = bank.Bank(Path("/srv/feature-bank")).entry(bank.file_digest(Path("take.flac")), "loudness-r128")
entry.scalar("integrated")   # -15.6
```

A second run answers `hit` and starts nothing. `extract.py run all` runs every
extractor that accepts the content's media type.

## Outputs that are not numbers

A file output, such as a separated stem, is declared with its media type and
suffix:

```json
"vocals": {"kind": "file", "media_type": "audio/flac", "suffix": ".flac"}
```

An extractor that needs `stems/vocals` receives its path, and the stem's
measures are filed under the recording's content digest and keyed by the
stem's entry, so a new separator version reruns everything measured from its
stems.

## Other media

An extractor for photographs accepts `image/*`; for video, `video/*`. Both
use the same manifest, request, result, and bank. Scalars, events, spans,
and files need no grid. A series' grid is a time base in integer samples; a
fractional frame rate fits it (29.97 frames a second is `sample_rate` 30000
and `hop` 1001). Spatial grids and regions of an image cannot be declared
yet ([open questions](open-questions.md#interfaces-and-tooling)).
