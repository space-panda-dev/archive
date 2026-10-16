# Real-record refusal fixtures

These fixtures are minimized from operator-authorized copies of two real
records and their referenced bytes. They retain only the evidence needed to
reproduce each refusal and contain no source paths, object identifiers,
original filenames, timestamps, personal names, or personal content. The generic
`._capture.wav` name preserves only the sidecar naming mechanism.

- `marker-content-mismatch.json` retains the content identity written in the
  record and the content identity measured from the copied byte target. Both
  size and digest differ.
- `appledouble-capture.json` retains the copied record's capture classification
  and the first 32 bytes of the copied target. The header begins with the
  AppleDouble magic number `00051607`. Its `occurrence_kind`, `source`, and
  `name` projection corresponds to the copied record's
  `payload.occurrence.{kind,source,name}` fields.

They are evidence for permanent Door tests, not definitions of the unsettled
artifact payload or staging protocol. Until those contracts are accepted, Door
must fail before claiming either record is admissible.
