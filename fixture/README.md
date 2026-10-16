# Fixture archive

A published, synthetic archive any client can build for its own tests
([ADR 0059](../docs/decisions/0059-world-read-contract.md)). Every label,
title, and byte here is invented.

- [`plan.json`](plan.json) lists the records, step by step, with symbolic
  keys instead of identifiers. Together they cite every contract in the
  current specification's catalog except the specification upgrade, which
  Door refuses. A record may name its `agent`: derivation runs and the claims
  they make are asserted by a tool's Package URL (ADR 0064); every other
  record by the archive's operator.
- [`content/`](content/) holds the bytes the plan's artifacts admit.
  `recording.wav` is synthetic audio, 4000 frames of 16-bit mono at 8000 Hz
  (silence, a 440 Hz tone, then noise), made with:
  ```sh
  ffmpeg -f lavfi -i anullsrc=r=8000:cl=mono -f lavfi -i sine=frequency=440:sample_rate=8000 \
    -f lavfi -i anoisesrc=r=8000:a=0.1:seed=7 \
    -filter_complex '[0]atrim=end_sample=1000[a];[1]atrim=end_sample=2000[b];[2]atrim=end_sample=1000[c];[a][b][c]concat=n=3:v=0:a=1[out]' \
    -map '[out]' -ac 1 -ar 8000 -c:a pcm_s16le -bitexact -fflags +bitexact -map_metadata -1 recording.wav
  ```
  The plan's stream claim states its facts, which World's tests check
  against the file. The two `*-parameters.json` files are run configurations
  as canonical JSON.
- [`build.py`](build.py) builds the archive. It needs only Python's standard
  library, `archive`, `door`, `git`, and `git-annex`.

## Build

```sh
fixture/build.py /tmp/fixture/archive --door door --archive archive --manifest /tmp/fixture/manifest.json
```

The builder runs `archive init`, stages the content in a git-annex
repository beside the archive (`<root>-staging`), and hands each step to
`door admit` or `door transition` as a proposal (kept in
`<root>-proposals/`). It never writes a record itself: Door decides every
revision, and the builder stops if Door's revisions differ from the ones it
pinned in later drafts. The archive identifier is new on every build, so
object and revision identifiers differ from build to build; the manifest maps
each plan key to its object and latest revision, and lists the commit each
step made.

`ARCHIVE_GIT_EXECUTABLE` and `ARCHIVE_GIT_ANNEX_EXECUTABLE` must name the
executables Door uses, as for Door itself.

## Use

A client's CI builds the archive, starts `door serve` and the relay against
it with role `scratch`, and runs its own tests, including commits. Archive's
release image holds everything needed and, by default, runs
[`serve.py`](serve.py), which does all of that on `127.0.0.1:8450`
([ADR 0062](../docs/decisions/0062-release-bundle-and-ci.md)):

```sh
docker run -d --network host ghcr.io/<owner>/archive@sha256:<digest>
curl -H 'Tailscale-User-Login: ci@example.invalid' http://127.0.0.1:8450/health
```
