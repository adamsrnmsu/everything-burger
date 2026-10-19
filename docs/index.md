# burger

<!-- mascot -->

**burger** is a live smoke test for open `sceptre-phenix-apps` and
`sceptre-bennu` pull requests, run on a real phēnix host. It answers one
question: *with my PRs merged, do the SCEPTRE topologies still come up, and do
the SCORCH components still do what they promise?*

``` text
switchboard ──▶ merge PRs onto main ──▶ build + install ──▶ topology + SCORCH pipeline ──▶ checks ──▶ pass/fail table + report
```

- A **[switchboard](switchboards.md)** picks a **route** (waterway, soap, or your
  own rig), the **PRs** to merge, and which **items** are on.
- An **[item](items.md)** is one YAML file per SCORCH component, phēnix app or
  bennu behavior: its configuration, the routes it supports, and the
  **[checks](checks.md)** that say what a pass means. Items double as
  documentation: read one to learn how the component is set up and what it
  leaves behind.
- `burger run <switchboard>` ends with a pass/fail table and
  `work/<switchboard>/report.md`, with paths to every output a check read.

## Quick start

```bash
git clone https://github.com/adamsrnmsu/everything-burger.git && cd everything-burger
python3 -m venv .venv && .venv/bin/pip install -e .
./burger preflight          # can this host run burger?
./burger list               # switchboards and items
./burger run baseline       # the control: if this fails, the rig is broken, not a PR
```

Then see the [run book](run-book.md), or [add a test](items.md#adding-a-test).
