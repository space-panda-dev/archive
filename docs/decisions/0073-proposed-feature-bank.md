# 0073. Proposed: a feature bank holds regenerable features, keyed by content and method

Status: proposed

Every enrichment tool decodes its own audio. The span detector decodes a
stream to measure levels and chroma, the window matcher decodes each window
again for its pitch-class profiles, and derived media decodes the recording
a third time. Each new measure would add another decode and another private
copy of the same frame-level numbers. The V2 system reached the same point
and wrote a dense feature timeline so "other jobs can read it instead of
repeatedly decoding", but never let them read it; elsewhere it kept
disposable JSON caches.

The operator asked for each recording to be decoded once into
frame-level features that every enrichment reads, for adding an audio
measure to be easy, for photographs and video to share the same mechanism
later, and for nothing in Archive to depend on the language a measure is
written in. The window matcher's next experiment also needs whole-recording
pitch-class profiles that a reader can trust to be the bits a fresh analysis
would make.

This record proposes the bank and its keys. [ADR 0074](0074-proposed-extractor-contract.md)
proposes the contract its writers follow, and
[the feature bank format](../feature-bank.md) specifies the bytes.

## Proposed decision

**A feature is a measurement of content, made for other tools to read.** It
is one of five kinds: a *track* (rows of numbers on a time grid, such as a
pitch-class profile every 93 ms), a *scalar*, a list of *events*, a list of
*segments*, or a *file* (such as a separated stem). A feature is not a claim
and is never admitted. It is named `<extractor>/<output>`, such as
`frames/hpcp`.

**The feature bank is regenerable.** It holds features made by extractors
(ADR 0074) from content, and every one can be made again from the content
by the same extractor. Losing the bank, or any entry in it, loses nothing;
every reader must work, more slowly, without it
([state model](../state-model.md)). Its root is configuration, outside the
source tree and outside any archive.

**An entry is filed by content and keyed by everything that decides it.**
Entries live at `sha256/<ab>/<cd>/<hex>/bank/<extractor>/<key hex>/` under the
root, the same per-content directory ADR 0069 gives derived media, so a bank
and a derived-media root may be one directory. The key is the SHA-256 of the
canonical JSON of the extractor's id and version, the digest of its
canonical parameters, the content digest, and, for each feature it needs,
the key of the entry that holds it. A change upstream is therefore a new key
everywhere below it.

**Nothing is invalidated in place.** A new extractor version or other
parameters make a new key and a new entry; the old entry is never read again
and may be deleted at any time. An entry is never rewritten.

**Software is recorded, not keyed.** A key must be known before anything
runs, and the worker's library versions are not. Each entry records the
software its extractor reports. An extractor whose output depends on an
exact build pins that build in its parameters, so another build is another
key: `frames` pins Essentia 2.1b6.dev1438, as ADR 0071 requires of the
measure it shares.

**An entry is complete or absent.** It is a directory holding `entry.json`
and its outputs' files, which appears by one rename after every output has
been checked against its extractor's manifest. A failure is recorded beside
where the entry would be, as operational state, and never read as an entry.
A directory without a readable `entry.json` is damage, refused, not taken
for a miss.

**A reader finds an entry by the key its installed extractors give.** It
never searches for "any frames entry" of a content: features made by another
version or other settings are invisible to it.

**Features reach the archive only through enrichment.** A tool that makes a
claim from features is an ordinary enrichment tool: the adapter proposes its
derivation run and outputs to Door (ADR 0064). Bank entries are not archive
objects, so the run's `used` references pin the Artifact whose content the
features were made from, and its parameters record, for each feature read,
the feature's name, its extractor's id and version, and its entry's key,
which together with the content reproduce it. No tool proposes a claim from
the bank yet, so no such run has been written.

**The bank is under the operator's custody, like derived media.** A
separated vocal stem is the recording. Sending an entry anywhere the
operator does not control is disclosure (ADR 0016), and nothing here sends
one.

### Why a new noun

*Feature bank* and *feature* pass the [new-noun test](../vocabulary.md#adding-a-noun):

1. Nothing else keeps machine measurements of content for other tools to
   read. Derived media are made for a person's browser and served by name;
   claims are evidence.
2. Its verb is *read*: tools read features; extractors write them (ADR 0074).
3. It enforces that a feature is found only by the key that decides it, so a
   reader never mixes outputs of different methods, and that an entry is
   complete or absent.
4. It is code inside Enrichment (`enrich/bank.py`), not a new concept beside
   it; the noun names a store whose layout other programs must share.
5. It owns no authoritative state: everything in it is regenerable.
6. It is a layout and a library, not a subsystem: no service, no daemon.

## Consequences

- `enrich/bank.py` holds the layout, the keys, the shapes, and a reader;
  `enrich/extract.py` fills the bank (ADR 0074). Both need only the standard
  library; reading a track as an array needs numpy.
- The first extractor, `frames`, writes pitch-class profiles that are the
  same bits as `cover.Analyser.features` on the whole recording, so a reader
  measures nothing again. `cover.py --bank` reads them for whole files.
- The window search still decodes each window. A window's profile cut from
  the whole recording differs at its edges from one measured on the window
  alone, so reading windows from the bank changes the method and needs its
  own calibration (ADR 0071); that is a separate decision.
- `frames` takes about 11 MB a recording-hour. On the development Mac it
  measured a synthetic hour of 48 kHz stereo FLAC in 6.4 seconds, decoding
  included, with about 590 MB resident; real recordings have not been
  timed.
- Stale entries accumulate until someone deletes them; nothing prunes the
  bank yet ([open questions](../open-questions.md#interfaces-and-tooling)).

## Alternatives considered

- **Features as claims.** Millions of numbers per recording as triples, and a
  regenerable measurement would become permanent evidence (as ADR 0069 found
  for peaks).
- **One JSON file per recording, as the V2 system's caches were.** Frame
  tracks in JSON are several times larger and slow to parse, and one file
  for every measure makes each measure's version invalidate all the others.
- **NumPy archives, HDF5, Parquet, or SQLite.** Each needs a library to read
  and ties readers to a language or a format version; raw little-endian
  arrays with a JSON manifest are readable from anything.
- **Keying by Artifact identifier.** Two Artifacts may share content, and
  features describe bytes, not objects; ADR 0069 keys derived media by
  digest for the same reason.
- **Keying by file name, as derived media do.** `<name>-<version>` cannot
  carry parameters or what a feature was made from.
- **A cache inside each tool.** It keeps the duplicated decoding, and no tool
  could read another's measurements.
