# Selection only: execution belongs to unittest-parallel and unittest.
set -euo pipefail
pattern='test_*.py'
args=()
selection_args=()
while (($#)); do
  case "$1" in
    -p|--pattern)
      if (($# < 2)); then echo "$1 requires a pattern" >&2; exit 2; fi
      pattern=$2; shift 2 ;;
    --pattern=*) pattern=${1#*=}; shift ;;
    -k)
      if (($# < 2)); then echo '-k requires a test name pattern' >&2; exit 2; fi
      args+=("$1" "$2"); selection_args+=("$1" "$2"); shift 2 ;;
    -v|--verbose|-q|--quiet|-f|--failfast|-b|--buffer)
      args+=("$1"); shift ;;
    -h|--help)
      echo 'Usage: enrich-tests [-p PATTERN] [-k NAME] [-v|-q] [-f] [-b]'
      echo 'Runs the current Archive checkout with six parallel workers, then exclusive tests.'
      exit 0 ;;
    *) echo "Unsupported enrich-tests argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
if [[ ! -f scripts/ci_tests.py ]]; then
  echo 'Run enrich-tests from the Archive checkout root.' >&2; exit 2
fi
export CI_TEST_SUITE=enrich CI_TEST_PATTERN="$pattern" SHARD_INDEX=0 SHARD_COUNT=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 BLIS_NUM_THREADS=1
# Only standard unittest uses exit 5 for no tests; the parallel runner uses
# failure counts as exit codes. Check the union first
# so a legitimate empty phase passes while a wholly unmatched selection fails.
"$ENRICH_TEST_PYTHON" scripts/ci_tests.py "${selection_args[@]}"
status=0
CI_TEST_PHASE=parallel "$ENRICH_TEST_PYTHON" -m unittest_parallel \
  -j 6 --level class --disable-process-pooling -s scripts -p ci_tests.py "${args[@]}" || status=1
CI_TEST_PHASE=serial "$ENRICH_TEST_PYTHON" -m unittest discover \
  -s scripts -p ci_tests.py "${args[@]}" || { code=$?; if ((code != 5)); then status=1; fi; }
exit "$status"
