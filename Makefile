.PHONY: check claims test test-haskell test-python

check:
	python3 scripts/check.py

# Lists claims about the present for a person to recheck; never fails.
claims:
	python3 scripts/check.py --claims

# PYTHON must have world/requirements-test.txt and enrich/requirements.txt
# installed; the enrich suite also needs ffmpeg and ffprobe on PATH.
PYTHON ?= python3

test: test-haskell test-python

test-haskell:
	cabal test all

# The Python suites run the real Door and Archive built by cabal, and fail
# rather than skip when they cannot (ARCHIVE_REQUIRE_INTEGRATION).
test-python:
	cabal build exe:door exe:archive
	env ARCHIVE_TEST_DOOR="$$(cabal list-bin door)" ARCHIVE_TEST_ARCHIVE="$$(cabal list-bin archive)" \
	  ARCHIVE_GIT_EXECUTABLE="$$(command -v git)" ARCHIVE_GIT_ANNEX_EXECUTABLE="$$(command -v git-annex)" \
	  archive_datadir="$(CURDIR)" ARCHIVE_REQUIRE_INTEGRATION=1 sh -c '\
	  $(PYTHON) -m unittest discover -s world/tests && \
	  $(PYTHON) -m unittest discover -s relay/tests && \
	  $(PYTHON) -m unittest discover -s shadow -p "test_*.py" && \
	  $(PYTHON) -m unittest discover -s migration -p "test_*.py" && \
	  $(PYTHON) -m unittest discover -s fixture -p "test_*.py" && \
	  $(PYTHON) -m unittest discover -s enrich -p "test_*.py"'
