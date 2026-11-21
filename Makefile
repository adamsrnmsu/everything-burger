BIN := $(if $(wildcard .venv/bin),.venv/bin/,)
BOARD ?= baseline
# Offline renders read topologies from the synced switchboard unless this is set;
# a checked-out clone under work/repos/ saves a sync for `make dry-run`.
export BURGER_TOPOLOGIES ?= $(wildcard $(CURDIR)/work/repos/topologies)
# Quality bars (unclebob's crapper, dryer, mutator; the ub-mode skill).
CRAP_MAX := 9.99
DRY_MIN := 0.8

.DEFAULT_GOAL := help
.PHONY: help install list dry-run run docs check test goldens format crap dry mutate

help: ## show these targets
	@awk -F':.*## ' '/^[a-z-]+:.*## /{printf "  make %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo "  BOARD=$(BOARD) (any switchboard from 'make list')"

install: ## create .venv and install burger with the dev tools
	test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -e '.[dev]'

list: ## switchboards and items
	./burger list

dry-run: ## resolve and render BOARD offline; no phēnix needed
	./burger check $(BOARD)

run: ## sync, build, install, up, scorch, verify, down for BOARD
	./burger run $(BOARD)

docs: ## serve the docs site locally
	.venv/bin/pip install -q -e '.[docs]'
	.venv/bin/mkdocs serve

check: ## lint: ruff and vulture
	$(BIN)ruff check .
	$(BIN)ruff format --check .
	$(BIN)vulture src tests

test: ## run the tests
	$(BIN)python -m pytest

goldens: ## rewrite tests/golden from the current render; BOARDS="a b" for some
	$(BIN)python scripts/regen_goldens.py $(BOARDS)

format: ## fix formatting and lint
	$(BIN)ruff format .
	$(BIN)ruff check --fix .

crap: ## CRAP per function (complexity and coverage); fails above CRAP_MAX
	crapper --threshold $(CRAP_MAX) -s src

dry: ## duplicate-code candidates; fails on any scoring DRY_MIN or more
	mkdir -p .metrics; dryer --threshold $(DRY_MIN) -s src | tee .metrics/dry.out; ! grep -q ^DUPLICATE .metrics/dry.out

mutate: ## mutation testing (slow; rewrites src/ while it runs: do not edit or test meanwhile)
	mutator -s src
