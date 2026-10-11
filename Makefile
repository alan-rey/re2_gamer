# RE: Code Veronica X - pipeline entry points (docs/04-bast-modding-pipeline.md).
# One target per stage. Recipe lines must be TAB-indented (make requirement).
PY  ?= python
ISO ?= $(firstword $(wildcard .game_iso/*.iso))

OUT      ?= data/raw
MANIFEST ?= data/manifest.json

.PHONY: help unpack dry-run verify test

help:
	@echo "Phase 1 targets:"
	@echo "  make unpack    ISO -> $(OUT)/ + $(MANIFEST)"
	@echo "  make dry-run   catalogue the ISO without writing any files"
	@echo "  make verify    re-hash $(OUT)/ against $(MANIFEST)"
	@echo "  make test      run the offline test suite"

unpack:
	$(PY) -m tools.unpack.extract --iso "$(ISO)" --out "$(OUT)" --manifest "$(MANIFEST)"

dry-run:
	$(PY) -m tools.unpack.extract --iso "$(ISO)" --dry-run

verify:
	$(PY) -m tools.unpack.extract --verify --out "$(OUT)" --manifest "$(MANIFEST)"

test:
	$(PY) tests/run_tests.py
