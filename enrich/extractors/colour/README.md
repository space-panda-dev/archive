# The colour extractor

A colour-coded waveform's data: one colour for each pixel of the waveform
peaks that [`enrich/media.py`](../../media.py) makes as derived media
([ADR 0069](../../../docs/decisions/0069-derived-media.md), kind `peaks-1`,
about ten milliseconds a pixel). A client draws the peaks it already fetches
from the relay (`/media/<digest>/peaks`) and tints pixel `k` with row `k` of
`colour`.

| Output | Kind | What it is |
| --- | --- | --- |
| `colour` | series, `u8`, 3 columns, grid `peaks` | red, green, blue for each peaks pixel |
| `palette` | file, `application/json` | how the colours were made, and legend stops (below) |
| `preview` | file, `image/png` | the whole recording, RGBA, transparent behind the waveform |

The peaks are not an output. They are derived media, and the bank holds no
copy of them ([ADR 0073](../../../docs/decisions/0073-proposed-feature-bank.md),
"Derived media are not copied into the bank"). The program still decodes the
content at its own rate with the same decoder (`enrich/waveform.py`), into a
temporary file it deletes, because it needs the number of pixels and every
channel's envelope for the preview, and `frames` holds neither.

The grid `peaks` is written at twice the source rate (`sample_rate` 2 x
rate, `hop` and `frame` 2 x samples per pixel, `first_centre` samples per
pixel), so each pixel's centre, half a pixel in, is an integer sample.

## When the colour changes

Colours are interpolated linearly, in decibels and in hertz, from the
`frames` grid (rows 2,048 samples apart at 22,050 Hz, each a 4,096-sample
window, about 186 ms) to each pixel's centre. A sharp change therefore shows
as a ramp about ten pixels long, and the colour moves before the waveform
does: from silence (the -120 dBFS floor) to a tone, mid lightness is reached
about 90 ms before the onset. That is the window, not an offset; the test
`test_the_colour_changes_where_the_frames_say_and_leads_the_onset` pins where
the change falls to within 0.15 of a pixel.

## The palette, `archive-colour-palette/2`

One JSON object with exactly these fields. `check_palette` in
[`colour.py`](colour.py) refuses anything else, and the program checks its
own palette before writing it. Numbers are JSON numbers; counts are
integers.

| Field | Value |
| --- | --- |
| `contract` | `"archive-colour-palette/2"` |
| `parameters` | the extractor's parameters for this entry, as `extractor.json` declares them |
| `colour.dtype` | `"u8"` |
| `colour.columns` | `["red", "green", "blue"]` |
| `colour.rows` | the number of rows, equal to `peaks.pixels` |
| `colour.row` | text: which time a row stands for |
| `peaks.kind` | the derived-media kind the rows follow, `parameters.peaks` (`"peaks-1"`) |
| `peaks.format` | `"audiowaveform binary version 2, 8-bit"` |
| `peaks.sample_rate`, `peaks.samples_per_pixel`, `peaks.pixels`, `peaks.channels` | the peaks file's header, as counts |
| `frames` | the grid the colours were interpolated from: `sample_rate`, `hop`, `first_centre`, and `frame` when it has one |
| `hue.source` | `"centroid"` or `"chroma"`, as `parameters.hue.source` |
| `hue.stops[]` | at least two of `{value, unit, hue, rgb}`: `value` a frequency (`unit` `"Hz"`) or a pitch class `A` to `G#` (`unit` `"pitch class"`), `hue` degrees from 0 to 360, `rgb` `#rrggbb` at mid lightness |
| `saturation` | `"fixed"` for a centroid hue; for a chroma hue, `"saturation x the pitch-class profile's concentration"` |
| `lightness.source` | `"frames/rms_db"` |
| `lightness.unit` | `"dBFS"` |
| `lightness.floor_db`, `lightness.ceiling_db` | the decibels lightness spans for this recording, floor below ceiling |
| `lightness.darkest`, `lightness.lightest` | the HSL lightness at the floor and the ceiling, as in the parameters |
| `lightness.stops[]` | at least two of `{value, lightness, rgb}`: decibels, lightness from 0 to 1, and a grey `#rrggbb` |

With `lightness.reference` `"recording"`, the span is moved so the
recording's loudest frame is the ceiling, but the ceiling is never below
`floor_db`: a recording quieter throughout than the floor is drawn dark, not
at full lightness.

## Settings that are part of the key

Every parameter is in the entry's key, the preview's size included, so a new
size makes a new entry and recomputes the colours too. The colours take
under a second for an hour of `frames`; the decode for the peaks grid is the
cost, and splitting the preview into an extractor of its own would decode
again rather than save it. The preview is at most 8,192 x 1,024 pixels
(32 MiB as RGBA in memory).
