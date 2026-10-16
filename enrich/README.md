# Enrichment

Tools that derive something from files
([ADR 0064](../docs/decisions/0064-derivation-runs.md)). A tool takes files
and writes a plain result; it never reads or writes an archive.

## Derived media

[`media.py`](media.py) makes the waveform peaks and the listening copy of one
audio file ([ADR 0069](../docs/decisions/0069-derived-media.md)):

```sh
enrich/media.py --root /path/to/derived-media --digest sha256:<hex> /path/to/recording.wav
```

Both are regenerable, never admitted. They go under the derived-media root at
`sha256/<ab>/<cd>/<hex>/<name>`, where `<hex>` is the content's SHA-256 and
`<name>` carries the method version ([`derived.py`](derived.py) is the layout,
which the [relay](../relay/README.md) serves from):

| Name | What |
| --- | --- |
| `peaks-1.dat` | an audiowaveform binary file, version 2, 8-bit: per pixel, a signed minimum and maximum for every channel, from the full-resolution decode, with `samples_per_pixel` the sample rate over 100 rounded half to even |
| `listen-1.mp4` | AAC-LC at 192 kb/s in MP4 with its index first, downmixed to stereo when the source has more than two channels |

- The file's SHA-256 must be the digest given; the digest names the output.
- The file must hold exactly one audio stream. More than one is refused,
  because which one is the recording is not the tool's to guess.
- Each output is written to a temporary file beside it and renamed into
  place, so a reader sees all of it or none. A rerun on complete outputs
  decodes and writes nothing; an incomplete output is made again.
- The root must already exist. Files are made readable by their owner only:
  a derivative carries the recording, so run the relay as the same user.
- 8-bit values follow audiowaveform's rule for 16-bit samples: a full-scale
  sample `x` becomes `floor(x * 128)`, clipped to -128..127.

It prints `{"digest", "outputs": {kind: {"path", "status"}}}`, where `status`
is `written` or `present`; a failure prints `{"status": "failed", "detail"}`
to stderr and exits 1. It needs `ffmpeg` and `ffprobe` (`--ffmpeg` and
`--ffprobe` name others) and numpy ([`requirements.txt`](requirements.txt)).

## Test

```sh
python -m pip install -r enrich/requirements.txt
python -m unittest discover -s enrich/tests
```

The tests make their audio with numpy and ffmpeg's lavfi sources. Without
ffmpeg they are skipped, unless `ARCHIVE_REQUIRE_INTEGRATION` is set.
