# Checks

`verify` is a list of checks, each reading the SCORCH output copied back to
`work/<switchboard>/results/run-N/<component>/`. A check whose input is
missing fails with "no output found at …"; it never passes.
Every component's status files are judged without a `verify` entry: an error `return` fails, and a background component's `stop` entry carries the verdict ([sceptre-phenix#457](https://github.com/sandialabs/sceptre-phenix/issues/457)). Writing `status:` in `verify` is a load error.

| Check | Passes when | Example |
|---|---|---|
| `files` | a glob matches at least `min` files, each at least `min_bytes` | `- files: {glob: "*.pcap", min: 1}` |
| `pcap` | pcap/pcapng files parse and hold at least `min_packets` matching a TCP/UDP port (or protocol) | `- pcap: {glob: "*.pcap", tcp_port: 502, min_packets: 10}` |
| `json` | a JSON file's field exists, equals or matches | `- json: {glob: "*.json", field: "status", equals: "ok"}` |
| `csv` | a CSV has at least `min_rows` rows, or the named `columns` | `- csv: {glob: "*.csv", min_rows: 1}` |
| `log` | a log file contains or lacks a pattern | `- log: {glob: "*.log", lacks: "ERROR"}` |
| `vm` | a command run inside a VM over cc exits 0 | `- vm: {host: "{route.rtus}", run: "pgrep -f bennu-field-deviced"}` |
| `process` | shorthand for `vm`: a process named `name` runs on `host` (anchored `pgrep -f '(^\|/)NAME( \|$)'`) | `- process: {host: "{route.provider}", name: pybennu-power-solver}` |
| `port` | shorthand for `vm`: `port` is listening on `host`, or has an established connection with `state: established` (`ss`) | `- port: {host: "{route.rtus}", port: 502}` |
| `python` | a function in the item's folder returns pass/fail plus a message | `- python: check_totals` |

Every check reports a one-line reason and the paths it read. Items marked `advisory` report `advisory-FAIL` and do not fail the board.
`process` names may use only `[A-Za-z0-9._-]` and `port` must be an integer, so scenario metadata cannot inject shell. A pattern needing regex such as `bennu-field-deviced?` stays a raw `vm`. Keys a check does not know (`min_packet`) are a load error.
A check with `windows: true` (traffic that the Windows SCADA/OPC pollers make) is `skipped` under `--linux-only`.
