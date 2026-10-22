# Integration branch notes (phenix-apps-8bl)

Branch `integration` = upstream/main + 20 PR merges in order A
(159, 161, 192, 191, 194, 155, 164, 165, 166, 167, 168, 169, 170, 171, 172, 174, 175, 176, 177, 195).
CHANGELOG.md merges cleanly with `merge=union` (use it, or expect a CHANGELOG conflict at every merge).
No fix commits: all fixes are inside the merge commits. Merges 159-166, 169, 174-177 were clean.
Suite at the end: 1747 passed; ruff check and format clean.

Common theme: #155 hardens shell/host paths in the old `self.metadata.get(...)` code; the model PRs
replace that code. Each resolution keeps the model access and expresses #155's check as a model type.

## #167 pcap (pcap.py) vs #155
- #155: commands as argv lists (`_tshark_split`, mergecap/editcap lists), filter quoted via
  `utils._validate_mm_filter`. #167: model, timeouts from `tshark_timeout`/`merge_timeout`, string commands,
  per-VM filter quoting that doubled backslashes.
- Resolved: model config + #155's list-form commands with `timeout=self.config.*_timeout`. `Bpf` type now also
  rejects `" \ #` (was only `' \r \n`), matching `_validate_mm_filter`; `_set_capture_options` quotes through
  `_validate_mm_filter` (no backslash doubling, since #155 shows minimega re-lexes it and refuses backslashes).
- pcap test that used `ip proto \tcp` changed to `ip proto tcp`; added a rejection test; README filter line updated.

## #168 tcpdump (tcpdump.py) vs #155
- #155: `_validate_iface` / IFACE_REGEX. #168: model with `Interface` type.
- Resolved: took the model; `_validate_iface` dropped (`Interface` is stricter). `vms[].filter` typed `FilterExpr`
  (the #155 allowlist, allows BPF parentheses) instead of #168's `Args`; `options` stay `Args`.

## #170 ssh (README.md) vs #155
- #155 added a `known_hosts` option (RejectPolicy when set) in code + README; #170 rewrote the README and made the
  model forbid unknown keys.
- Resolved: README = #170's, plus a `known_hosts` row. `known_hosts` is a field on `SSHMetadata`; `_connect` reads
  `self.config.known_hosts` (and the warning uses `self.config.ip`; `self.ip` no longer exists). No new test added
  for known_hosts.

## #171 erspan (erspan.py) vs #155
- #155 validated fields with `validate_ovs_name`/`validate_ip`/session_key/excluded_vlans in the old parse method;
  #171 deletes that method for a model.
- Resolved: took #171; the model's `Interface`, `Address` (no IPv6 scope), strict int `session_key`, `Text` list
  cover those checks. Removed the leftover validate tail of the old method.

## #172 mm (README.md, mm.py) vs #155
- #155: `_validate_filename`, `_validate_snaplen`, `_quoted_filter`, `_convert_pcap`, `_validate_mm_token` calls,
  `utils._validate_mm_filter`. #172: whole-file model rewrite.
- Resolved: took #172's mm.py and README. #155's checks as types: `Filter` refuses `' " \ #` and line breaks and is
  quoted via `utils._validate_mm_filter` (no backslash doubling); new `Target` type for VM hostnames (one word, no
  whitespace/quotes/`#`/backslash; keeps `vm[1-3]`, `a,b`, `*` working, which the `Token` type would reject);
  connect `bridge` still checked with `_validate_mm_token`.
- Fixed a stale `if bridge:` (variable from the bridge branch) to `conn.bridge`.
- Tests: #155's mm tests in `common/tests/test_command_helpers.py` rewritten to build `MMMetadata` (bad values now
  fail at parse time); `_validate_snaplen`/`_convert_pcap` tests removed (superseded by the model and #172's own
  conversion tests); `test_mm.py::test_filter_backslashes_are_escaped` became a refusal test.

## #195 shared helpers vs #164/#165/#175
- disruption.py (#164): kept the model's `_attacker_extra_interfaces`/`_attacker_iface_cmd` (windows aware) and
  swapped `extract_node` for `require_node`.
- iperf.py (#165): kept `config.iperf_paths` checks, but `_get_node_info` uses `extract_node_os_type`, `_interface`
  uses `require_node`, mapping uses `extract_node_os_type` for the exe. Removed the "iperf hosts not in the topology"
  check (a missing node now raises "node X does not exist"); unset os_type means linux. Tests updated.
- vmstats/README.md (#175): kept #175's table, added "a VM without os_type counts as linux".
- Non-conflicting break: #195 dropped `import re` from scorch/app.py, but #155's `extract_run_name` needs it
  (NameError). Restored `import re`. When merging the real PRs, #195 needs this line or must be rebased on #155.

## Unsure
- `*` as an mm VM target was kept working via `Target`; that is the old behavior but not obviously intended.
- ssh `known_hosts` has no direct test.
