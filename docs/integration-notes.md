# Integration branch notes

Branch `integration` (adamsrnmsu fork, dcce244) = upstream/main (14d7b31, includes #158 #159 #161 #163 #164 #173 #192) + 16 PR merges in order
191, 194, 155, 165, 166, 167, 168, 169, 170, 171, 172, 174, 175, 176, 177, 195.
CHANGELOG.md merges cleanly with `merge=union` (use it, or expect a CHANGELOG conflict at every merge).
No fix commits: all fixes are inside the merge commits. Merges 191, 194, 155, 165, 166, 169, 174-177 had no textual conflict.
Suite at the end: 1747 passed; ruff check, ruff format --check and mypy clean (mypy baseline in pyproject untouched).

Conflicts were replayed from the old branch with git rerere (trained on the 20 old merges). Rerere only covers
conflict hunks, so edits the old merges made OUTSIDE conflict hunks (tests, READMEs, auto-merged files) were
re-applied by hand from the old merge commits (`git checkout <old merge> -- file`). Every replayed file was diffed
against the old merge: identical except where noted as "new".

Common theme: #155 hardens shell/host paths in the old `self.metadata.get(...)` code; the model PRs replace that code.
Each resolution keeps the model access and expresses #155's check as a model type.

mypy is new on main. The PR heads predate it, so each model module they add is checked for the first time here;
the fixes below (all NEW, all in the merge commit) are mechanical Optional/typing fixes, not behaviour changes.

## #167 pcap (pcap.py) vs #155  [replayed]
- #155: commands as argv lists, filter quoted via `utils._validate_mm_filter`. #167: model, timeouts from config,
  string commands, per-VM filter quoting that doubled backslashes.
- Resolved: model config + #155's list-form commands with `timeout=self.config.*_timeout`. `Bpf` type rejects
  `' " \ #` and line breaks; `_set_capture_options` quotes through `_validate_mm_filter` (no backslash doubling).
- Outside conflict hunks (re-applied from old merge): pcap README filter row; test_pcap.py (`ip proto tcp`, rejection test).
- NEW mypy fixes: `extract_app("scorch")` may be None (assert); `_loop_chain` variable typed `Any`.

## #168 tcpdump (tcpdump.py) vs #155  [replayed]
- Took the model; `_validate_iface` dropped (`Interface` is stricter). `vms[].filter` typed `FilterExpr` (the #155
  allowlist, allows BPF parentheses); `options` stay `Args`.
- NEW mypy fix: `extract_app("scorch")` may be None in `_check_stage_lists`.

## #170 ssh (README.md, ssh.py) vs #155  [replayed]
- #155 added `known_hosts` (RejectPolicy when set) in code + README; #170 rewrote the README and forbids unknown keys.
- Resolved: README = #170's plus a `known_hosts` row. `known_hosts` is a field on `SSHMetadata`; `_connect` reads
  `self.config.known_hosts`, warning uses `self.config.ip`.
- ssh.py itself did NOT conflict: it auto-merged to #155's old-style `self.metadata.get("known_hosts")` / `self.ip`
  and broke 2 tests plus mypy. Fixed by re-applying the old merge's ssh.py. No known_hosts-specific test.

## #171 erspan (erspan.py) vs #155  [replayed]
- Took #171's model (`Interface`, `Address`, strict int `session_key`, `Text` list cover #155's checks); removed #155's
  leftover validate tail.
- NEW mypy fixes: assert `remote_config` is set in the remote runner; tests build `ERSPANMetadata.model_validate({...})`
  instead of `ERSPANMetadata(**BASE, ...)`.

## #172 mm (README.md, mm.py) vs #155  [replayed]
- Took #172's mm.py and README. #155's checks as types: `Filter` refuses `' " \ #` and line breaks and is quoted via
  `utils._validate_mm_filter`; `Target` type for VM hostnames (keeps `vm[1-3]`, `a,b`, `*`); connect `bridge` still
  checked with `_validate_mm_token`; stale `if bridge:` became `conn.bridge`.
- Outside conflict hunks (re-applied from old merge): `mm/tests/test_mm.py` (filter refusal test instead of the
  backslash-escaping test); `common/tests/test_command_helpers.py` (#155's mm tests rebuilt on `MMMetadata`;
  `_validate_snaplen`/`_convert_pcap` tests removed).
- NEW: one `# type: ignore[union-attr]` in test_mm.py now flagged unused by mypy; removed.

## #174 hoststats  [no conflict; NEW mypy fix]
- test_hoststats.py: `HostStatsMetadata(pollPeriod=poll)` fails mypy (aliased fields); use `model_validate({...})`.
  Folded into the #174 merge (merge was redone onto the fixed commit so #175 sits on top).

## #195 shared helpers vs main(#164)/#165/#175  [replayed, 1 NEW fix set]
- disruption.py (now conflicts against main, which has #164, rather than against the #164 PR): same resolution as
  before: keep the model's `_attacker_extra_interfaces`/`_attacker_iface_cmd` (windows aware), `extract_node` ->
  `require_node`. Rerere replayed it cleanly and the result is byte-identical to the old resolution.
- iperf.py: kept `config.iperf_paths` checks; `_get_node_info` uses `extract_node_os_type`, `_interface` uses
  `require_node`; "iperf hosts not in the topology" check removed (missing node now raises "node X does not exist";
  unset os_type means linux). test_iperf.py re-applied from the old merge (not in #195's file list).
- vmstats/README.md: #175's table plus "a VM without os_type counts as linux".
- Non-conflicting break: #195 drops `import re` from scorch/app.py but #155's `extract_run_name` needs it; restored.
- NEW mypy fixes (these are #195 vs the new mypy gate; #195 itself was written before it):
  - `common/base.py`: `extract_node` returns `Box | list[Box] | None`; `require_node` and
    `extract_node_interface_ip` now `cast(Box, node)` after the truthiness check (an `isinstance(node, Box)` check
    was tried and broke vmstats tests, which use plain-dict nodes).
  - `otsim/protocols/protocol.py`: `port: int | str` -> inferred int (`split_host_port` already returns `int | None`;
    the old `int(port)` conversion #195 removed is why `int | str` leaked into the return type).
  - `disruption/tests/test_disruption.py`: the `lambda name, iface` stub (parameter names already match from #164)
    now fails mypy ("cannot infer type of lambda") against the typed `ExperimentBase.extract_node_ip`; replaced by a
    typed nested `def node_ip(name: str, iface: str | int | None = None) -> str`.

## Dropped from v1 (now on main)
#159, #161, #192, #164 and their entries. The #164 conflict with #195 is described above under #195.

## Unsure
- `*` as an mm VM target was kept working via `Target`; that is the old behavior but not obviously intended.
- ssh `known_hosts` has no direct test.
- The `cast(Box, node)` in base.py is a shortcut; proper fix is `@overload` on `extract_node` keyed on `wildcard`.
- PRs 132 and 179 are open but were not in the list, so they are not merged.
