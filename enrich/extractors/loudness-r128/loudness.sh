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
