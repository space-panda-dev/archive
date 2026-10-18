# 0075. Proposed: stems are separated outside the archive's environment, and a stem is content to every extractor

Status: proposed

The operator wants a recording's vocals, drums, bass, and everything else as
separate signals, and every audio measure to work on each of them as it works
on the recording: the pitch-class profiles of the vocal line alone, the level
of the drums alone, the loudness of whatever is left. The V2 system separated
its whole accepted corpus with Demucs's `htdemucs` model (8.9 hours in about
4 hours on the operator's eight-core server, CPU only, one run at a time
because one run already saturates six to seven cores), kept the stems as
disposable WAV files outside its archive (27 GB), and measured them. Its
findings bound what a stem is good for:

- In a listening test on the operator's own material, vocals isolate cleanly
  in both directions: the voice alone in `vocals`, no voice bleeding into
  `other`, and stems that should be empty come back silent.
- `drums` discriminates: full where there is a kit, about 31 dB lower where
  there is none.
- `bass` is empty where no bassist plays, which is right, not a failure; for
  a solo guitarist with no bassist, the guitar's low end is routed into
  `bass` erratically from song to song. An empty or full stem is evidence
  about the model only to someone who knows what was in the room, and the
  archive does not record lineups.
- Stems that sound clean are not therefore good inputs: speech recognition
  on one band's clean-sounding vocal stem failed outright while the mix
  transcribed. Each measure on a stem needs its own evidence.

The feature bank ([ADR 0073](0073-proposed-feature-bank.md)) and the
extractor contract ([ADR 0074](0074-proposed-extractor-contract.md)) already
say that a separator runs in its own environment, named by configuration,
and that its stems are `file` features. Two things were left: how a separator
is pinned and run so that its stems are worth keying, and how an extractor
that measures content (`frames`, `loudness-r128`, every one to come)
measures a stem without being rewritten for it.

## Proposed decision

**`stems` is an extractor that runs under the operator's Demucs
interpreter.** Its command is `["${demucs-python}", "stems.py"]`; the runner
starts the program with the interpreter configured under that name, so
PyTorch is never imported by the archive's environment and the runner does
not know the program is Python. The program needs numpy and `ffmpeg` to
decode, join, measure, and encode, and imports `torch` and `demucs` only to
load the model. It writes four `file` features, `vocals`, `drums`, `bass`,
and `other`, and their level summaries.

**The method is pinned so that a key means one set of bits.** Its
parameters, and so its key, name:

- the decode: the first audio stream at 44,100 Hz stereo float32 by
  `ffmpeg` (htdemucs's rate and channels; a mono recording is duplicated);
- the model by the digests of its files: Demucs 4.1.0 and `htdemucs` from
  a fixed revision of its published weights, each file checked against its
  SHA-256 before it is loaded. Demucs itself fetches the latest revision
  from the model hub; this does not. Other weights are another method;
- Demucs's application settings, with `shifts` 0: Demucs's default shifts
  the input by a random offset, so two runs differ; with none, two runs on
  one build gave the same bits (tested on real separation);
- chunks of 600 seconds joined by 10-second linear crossfades. htdemucs
  holds the whole input and four outputs as float32 tensors, about 6 GB a
  recording-hour, so a three-hour rehearsal would not fit the 16 GB machine
  that runs it. Each chunk is normalised as Demucs's own interface
  normalises a whole track, so a long recording's stems are close to, but
  not the bits of, one whole-file run;
- the stem encoding: 16-bit FLAC, quantised by the program (round to
  nearest, clamped at full scale) rather than by `ffmpeg`, with `ffmpeg`'s
  bit-exact flags so that the encoder's version is not written into the
  file.

Software that is not pinned (`torch`, `ffmpeg`, numpy) is recorded in the
entry, as ADR 0073 has it.

**Stems are 16-bit FLAC at the model's rate and channels.** Demucs makes
float32 at 44,100 Hz stereo. As float WAV, four stems take about 5.1 GB a
recording-hour; as 16-bit WAV, as the V2 system kept them, 2.5 GB (it used
27 GB for 8.9 hours). FLAC is lossless on the 16-bit samples and needs no
library a reader lacks; a stem that is nearly silent, which is common
(`drums` with no kit, `bass` with no bassist), costs almost nothing. 24-bit
FLAC would add about 8 bits a sample that are mostly noise, about 1.3 GB a
recording-hour more, for detail 16 bits already holds: their quantisation
noise is about 101 dB below full scale, under the separation's own
artefacts (htdemucs's published signal-to-distortion ratio is about 9 dB)
and under a live room's noise floor. A stem that exceeds full scale (four
stems need not each fit where their sum does) is clamped in its file, and
the entry says by how many samples. The levels are measured on the float
stems before quantisation, so no summary depends on the file format.
Downmixing to mono would halve the size; it is not done, because a stem is
also for listening and the recordings are stereo.

**Each stem's levels are features of the same entry.** For each stem: its
whole RMS and sample peak in dBFS and the samples its file clamped, all
scalars; and `levels`, a series of each stem's RMS every 4,096 samples over
8,192-sample frames centred like `frames`' (about 10.8 rows a second, at the
same instants as the `frames` grid), on a grid of its own named `stems`. The
input's RMS (`mix_rms_db`) is a scalar beside them, because the V2 system
found a stem's level relative to the mix or to another stem the fair
comparison: absolute levels follow each rig's gain.

**A file feature can be measured as content.** `extract.py run <extractor>
--content <recording> --from stems/vocals` makes `stems` for the recording if
its entry is missing, and then runs the named extractors with the stem as
their input:

- the stem's own digest, which the stems entry records and the runner
  checks again before filing anything, is their `content`, and its media
  type (`audio/flac`) is the input's. The key is therefore the key of those
  bits: a stem made again with the same bits keeps its measures, and one
  with other bits is other content, measured again. The extractor receives
  an ordinary request and never knows it measures a stem; `frames` on a
  vocal stem writes the pitch-class profiles `cover.Analyser.features`
  makes of that stem, so the sweep reads a vocal line as it reads a
  recording;
- their entries are filed under the **recording's** directory,
  `sha256/<ab>/<cd>/<recording hex>/bank/<extractor>/<key hex>/`, not under
  the stem's digest. Deleting a recording's directory, which destroying the
  recording requires (ADR 0004, ADR 0073), then deletes its stems and
  everything measured from them; nothing made from it survives elsewhere in
  the bank. Keys never collide there, since each holds its own content
  digest;
- `run all --from stems/vocals` runs every extractor but the one that makes
  the feature and those that need it; naming one of those is refused, as is
  a feature that is not a file.

A reader asks `Bank.input_of(recording, "stems/vocals")` for the stem's path,
digest, and media type, and `Bank.entry(stem digest, "frames",
filed_under=recording)` for what was measured from it. A derivation run that
reads a stem's features records the stem's own entry as well as theirs
(`Entry.provenance("vocals")` of the stems entry), because the separator's
software is part of how the claim was made.

**In `run all`, an extractor whose program is not configured is skipped and
says so.** `stems` needs `${demucs-python}`, which only a machine with
Demucs has. Named on its own, or needed by what is named, it fails as
before; in `all` it is skipped with the `--program` to pass, so a machine
without Demucs still fills the rest of the bank.

## Consequences

- `enrich/extractors/stems/` holds the manifest and the program; the runner
  gains `--from` and the reader `input_of` and `filed_under`. The format
  ([feature bank format](../feature-bank.md)) states where entries made from
  a file feature are filed. No other extractor changes.
- Separation costs about four CPU-seconds per second of audio on the
  eight-core server (1.35 times real time while sharing it, evidence
  below); a recording-hour takes most of an hour. The program's own work
  (decode, join, measure, encode) is about 1% of that.
- A recording's stems cost what FLAC makes of them: 0.48 GB an hour on a
  synthetic signal, expected up to about 1.3 GB on music. Nothing prunes them; they are entries
  like any other and may be deleted at any time.
- A run takes the machine: `--jobs` above 1 contends rather than
  parallelises with `stems` in the graph, as the V2 system found.
- Separation is not run for every recording by anything here; the operator
  runs it, or a later Spool worker offering `archive-extractor.stems@1`
  does. Spool's open return path for binary outputs
  ([open questions](../open-questions.md#interfaces-and-tooling)) matters
  most here: four stems an hour are a gigabyte or more.
- A stem is the recording. It has the bank's owner-only modes, and sending
  one anywhere is disclosure (ADR 0016), as ADR 0073 says of every entry.
- Whether a stem holds what its name says depends on what was in the room,
  which the archive does not record; no claim is made from a stem's level
  alone.

## Evidence

Real htdemucs on the server, a 12-second synthetic chord with clicks: the
four stems summed to the mix within -36 dB, and a second run on the same
build gave the same file digests. On 10 minutes of synthetic audio (a
chord, clicks, and pink noise, 48 kHz stereo FLAC), real htdemucs on the
same server while another job kept five of its cores busy and this one ran
at lower priority: 444 seconds, 1.35 times real time, 2,406 CPU-seconds
(about four CPU-seconds per second of audio), 3.9 GB peak resident. The V2
system's 2.0 times real time was on an idle machine. The entry took 80 MB,
0.48 GB a recording-hour, of which `other` (where the noise went) was
38 MB and the three nearly empty stems 12 to 18 MB each. Synthetic noise
says little about how real stems compress: a stem carrying music is
expected to take nearer half its 16-bit WAV size, so a recording-hour of
four stems up to about 1.3 GB and less where stems are empty. The first
real recordings separated should be measured before the bank is sized.
With the model replaced by fixed gains, the program's own work took 4.6
seconds on a development Mac, 130 times real time.

## Alternatives considered

- **Filing a stem's measures under the stem's own digest.** Every extractor
  would work unchanged and the runner would need nothing new, but
  destroying the recording would leave measures of it in directories no one
  connects to it.
- **An extractor per stem that needs `stems/vocals`** (`vocal-frames`,
  `drum-levels`). Each measure would be written once for the recording and
  again for each stem, which is what the operator asked not to do.
- **Keying a stem's measures on the stems entry's key** instead of the
  stem's digest. Making the stems again with the same bits would discard
  every measure of them.
- **Running Demucs's command line.** It loads the whole recording, shifts
  the input by a random offset unless told not to, and fetches whatever
  weights the model hub serves; the Python interface lets the program pin
  the weights, bound memory, and stay deterministic.
- **Float32 or 24-bit stems.** Float WAV is 5.1 GB a recording-hour,
  several times the FLAC; 24-bit FLAC adds about 1.3 GB an hour. Both keep
  detail below what separation preserves; see above.
- **Separating in the archive's environment.** PyTorch is gigabytes and
  pins its own numpy; ADR 0074 keeps every extractor's environment its own.
