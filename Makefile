BIN := $(if $(wildcard .venv/bin),.venv/bin/,)

.PHONY: check test format
check:
	$(BIN)ruff check .
	$(BIN)ruff format --check .
	$(BIN)vulture src tests

test:
	$(BIN)python -m pytest

format:
	$(BIN)ruff format .
	$(BIN)ruff check --fix .
