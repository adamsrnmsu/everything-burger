# Integration branch notes

Branch `integration` (adamsrnmsu fork, d985de4) = upstream/main (14d7b31, includes #158 #159 #161 #163 #164 #173 #192) + 16 PR merges in order
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

## v3 rebuild (PR heads rebased on upstream/main 14d7b31 and made mypy-clean)
- Same base (14d7b31), same 16 merges/order; branch head d985de4 (local `integration-v3`). Suite 1747 passed; ruff, format, mypy clean.
- The "NEW mypy fix" items above are now inside the PR branches, not the merge commits. The only residual tree difference vs
  the dcce244 build: pcap.py `_loop_chain(run_config: Any)` (was `level: Any`) and tcpdump.py `assert app is not None; app.get("metadata") or {}`,
  both PR-side mypy fixes written differently from the old merge-side ones.
- Conflicts replayed by rerere: #167 pcap.py, #168 tcpdump.py, #170 ssh/README.md, #171 erspan.py, #172 mm.py+README.md, #195 iperf.py+vmstats/README.md.
  #195 disruption.py no longer conflicts (PRs rebased). Outside-hunk re-applications unchanged: pcap README+test_pcap, ssh.py, mm tests +
  test_command_helpers, scorch/app.py `import re`, test_iperf.py.
- CHANGELOG.md differs only in union-merge line order/duplicated "Changed" headings (rebased PR changelog contexts); same lines.

## v4 update (en18 findings, 10-22): four PR fixes merged on top of v3
- Head 045cb78 = d985de4 + merges of the new heads of #165 (a24d7ae), #167 (ba4818a), #168 (65aaacb), #172 (551447c).
  upstream/main is still 14d7b31 and no other PR moved, so the 16 merges were not replayed. Suite 1750 passed; ruff, mypy clean.
- #167 pcap.py vs #155: kept #155's `utils._validate_mm_filter` quoting; the reset is now `'len >= 0'` instead of `""`.
- #168 tcpdump.py vs #155: kept `GuestOptions`/`GuestFilter` (`FilterExpr` allows BPF parentheses) with #168's new comment;
  the launcher script shell-quotes both.
- #172: CHANGELOG only (union). #165: no conflict.

## v5 (10-23): #165 README only
- Head 4a2a126 = 045cb78 + merge of #165 582203d (README: iperf3 must be 3.10+). No code change, no conflict.
- The integrate iperf stop failure on v4 was not the branch: the `iperf` item ran jammy's iperf3 3.9, which has no
  server `--idle-timeout` and refuses client `--pidfile` ("some option you are trying to set is server only"), so the
  client exited before writing `/iperf_client-*.json`. Reproduced in a jammy container. The item now injects a static
  3.15 (burger 8da546c).

## Rule: no bare `$var` in an inline cc command
minimega expands a bare `$name` in an inline cc command (`cc exec`, `cc background`) before it reaches the VM: `$$`,
`$@`, `$pid`, `$p`, `$n` all arrive empty. Command substitution `$(...)` survives. Anything that needs shell variables
goes in a script file: write it under root_dir/exp_name, `cc send` it (`utils.mm_cc_send_wait`), then run
`bash /tmp/miniccc/files/<exp>/<name>` (as cc.py, tcpdump and iperf do).

## Rule: never reset a minimega setting with `""`
phenix runs commands under `namespace <ns>`; minimega rebuilds the subcommand's text with `inputItems.String()`, which
quotes only values containing whitespace, so an empty argument vanishes. `capture pcap filter ""` becomes
`capture pcap filter`, which only prints the current value. Reset the capture filter with the match-all `'len >= 0'`.
