
# Review: eclipse-score/itf PR #154 — "Propose composing ITF targets from packs/plugin"

- PR: https://github.com/eclipse-score/itf/pull/154
- Branch: `ihsa_itf_platform_bc` → `main`
- Scale: 669 changed files, +53,787/-5,303 lines
- Reviewed at commit: `8a6c468` (worktree HEAD at review time)
- Method: git worktree off `main`, a graphify knowledge graph built over the new `score/itf/` subsystem (2906 nodes, 5823 edges, 195 communities), and 9 subagents split across 5 review dimensions (architecture, detailed design, 4×coding by subsystem, 2×testing by subsystem, overall synthesis). Full methodology at the bottom.

This file is meant to double as (a) a human-readable summary for whoever is deciding on this PR, and (b) a structured source another agent can walk to post review comments to GitHub — each finding below has a consistent `File / Severity / Summary / Fix` shape for that purpose.

---

## Executive summary

The proposal is architecturally more disciplined than its size suggests: pack/OCTO module boundaries are **mechanically test-enforced** (not just documented), OCTO is verifiably stdlib-only, and the compat layer is a thin delegating facade rather than a parallel reimplementation. Test quality for the new OCTO/contracts logic is good — real cycle/ambiguity cases, meaningful assertions, not over-mocked.

But three things should block merging this as a single unit in its current state:

1. **The PR's central compatibility safety claim does not exist in the code.** Three independent reviewers (architecture, contracts/compat coding, core-test coverage) each searched the full tree for the described "source-hash gate preserving legacy test bodies" and found nothing. What actually exists (moved-verbatim legacy tests + an AST purity check on shim modules) is reasonable, but it is not what the PR describes, and nothing guards against future drift.
2. **Session lifecycle is not exception-safe or idempotent**, found independently by both the detailed-design and core-engine coding reviews: `bring_up()` can run before `resolve()`, `resolve()`/`bring_up()` can double-run, and `close()` marks itself done *before* cleanup — so a failure mid-cleanup leaks processes and leaves secret-masking installed as global process state, with no retry possible.
3. **The PR undersells its own validation gaps.** It admits pcap/Docker-observability and QNX are untested; the integration-test review found the gap is wider (non-pcap Docker observability is *also* `manual`-tagged for no technical reason, QNX is structurally excluded for every fork PR not just this one, and the factory/goal override path — central to the composition model — has one real CI assertion).

None of these are reasons the architecture is wrong; they're reasons a maintainer needs to force specific answers before merge. See "Decisions needed before merge" below.

**Process note:** at 669 files / +53,787 lines, and with the author's own words ("the current commit split is not prescribed... maintainers may choose staged reviews"), this should almost certainly be merged as separable units (compat layer / pack+contract system / QNX+Pages fixes), not as one commit.

---

## Cross-cutting findings (corroborated by 2+ independent reviewers — highest confidence)

### 1. "Source-hash gate" does not exist — PR description overstates the compat safety net
- **Severity:** blocker
- **Corroborated by:** architecture, coding (contracts/compat), testing (core/contracts/unit) — three independent searches of the full tree for `hashlib`/`sha256`/`checksum`/etc., all turning up nothing related to legacy-test preservation.
- **What actually exists:** `test/integration/legacy/` contains the original `test_{ssh,docker,qemu}.py` files moved verbatim (byte-identical to `main`) and run in CI, plus `test/integration/legacy/check_shims.py`, an AST check that the re-export shim modules (`score.itf.plugins`, `score.itf.core.com`, etc.) contain nothing but re-exports.
- **Fix:** Either implement the literal mechanism described (hash/diff legacy test bodies against a pinned reference, fail CI on drift) or correct the PR description to describe what's actually enforced. This is a one-sentence fix to the PR body plus a decision on whether the stronger mechanism is worth building.

### 2. Session lifecycle (`resolve`/`bring_up`/`close`/`describe`) is not exception-safe or idempotent
- **Severity:** major
- **Corroborated by:** detailed design, coding (core engine)
- **Details:**
  - `score/itf/core/session.py` `bring_up()` can be called before `resolve()` completes (crashes with an unhelpful `AttributeError`, not a domain error); nothing prevents double-`resolve()`/double-`bring_up()`.
  - `close()` (session.py:728-758) sets `self._closed = True` **before** cleanup runs, with no try/finally around the actual teardown steps. If anything raises mid-cleanup, `ProcessWrapper.reap()`, `.deactivate()`, and `secrets.uninstall()` never run, processes are never reaped, and secrets stay masked globally — but a retried `close()` is now a silent no-op because `_closed` is already `True`.
  - `describe()` (session.py:825-829) has no cleanup path at all — unlike `compose()`, it never closes the session, so secrets/logging state installed by `resolve()` is never undone. Repeated calls (e.g. `itf plan` in a loop) leak global state indefinitely.
- **Fix:** Add explicit state-ordering guards (raise a dedicated `SessionError` on out-of-order calls, mirroring the existing idempotency guard already on `close()`), and restructure `close()`/`describe()` so cleanup runs in `finally` and `_closed` is only set after cleanup succeeds.

### 3. Secrets masking is global, process-wide mutable state, not session-scoped
- **Severity:** major
- **Corroborated by:** detailed design, coding (core engine)
- **Details:** `score/itf/core/engine/secrets.py` (`_active: list[Secrets]`, `_factory`) is a flat module-level global, mutated by `Secrets.install()`/`uninstall()`, which also monkeypatches `logging.setLogRecordFactory` process-wide. Two sessions running concurrently (or finding #2's broken cleanup leaving one session's secrets permanently installed) share this state with no lock — one session's output can be masked against another's secrets, or lose masking entirely.
- **Fix:** Either assert/document single-session-per-process as a hard constraint, or scope the log record factory via a contextvar instead of a flat global list.

### 4. Admitted validation gaps are wider than the PR states
- **Severity:** major
- **Found by:** testing (integration/system)
- **Details:** The PR says pcap/Docker-observability is blocked locally by packet-capture permissions and QNX is skipped for fork PRs. In fact:
  - Non-pcap Docker observability (`test_command.py`/`test_console.py`) is *also* tagged `manual` in `test/integration/BUILD`, for no technical reason — it needs nothing beyond the Docker daemon the `itf-test` CI job already sets up.
  - QNX's `workflow-approval` gate (`head.repo.full_name == github.repository`) is false for *every* fork PR, not just this one — this is a standing CI-trust gap for the whole repo.
  - The factory/goal override path (`overrides/qemu_serial.itf.yaml` — overriding factories and bring-up goals, not just a scalar value) is only wired into the `manual`/QEMU-only observability suite; CI only asserts a single scalar override (`platform.root`).
- **Fix (cheap, before merge):** Untag non-pcap observability as `manual` so it runs in CI. Add a non-QEMU target exercising a factory/goal override with an assertion the override actually took effect. Correct the PR description's framing of the gap. QNX's structural fork-PR exclusion is a repo-level issue to track separately, not a blocker for this PR specifically.

---

## Decisions needed from maintainers before merge (not code fixes)

- **Conformance layer governance** (architecture finding): the author explicitly calls conformance "a suggestion," yet it's already wired into shipped packs (ssh, net, ping, docker) and the vendor example. Decide now whether conformance is mandatory for new packs, or stays optional — leaving it ambiguous means pack authors will diverge.
- **Commit/PR split**: given 669 files and the author's own "not prescribed" framing, split into separable units (compat layer / pack+contract system / QNX+Pages fixes) so a revert of one doesn't revert all three.
- **Contract vocabulary ownership**: DR-002 deliberately rejects a fixed contract registry ("ITF knows no contracts"), which is defensible but means contract-name collisions across unrelated projects are entirely the integrator's problem. Confirm maintainers are comfortable owning only the ~20 contracts in `docs/reference/contracts.md` as "supported, not standard" long-term.

---

## Architecture

Full agent report findings, in priority order:

1. **[minor]** "Source-hash gate" is a misnomer — see cross-cutting #1.
2. **[strength, no action]** Component boundaries are mechanically enforced: `test/core/gates/test_pack_gates.py` AST-walks every pack asserting no cross-pack imports and no `pytest` import in capability/action modules; `test/core/gates/test_octo_gates.py` asserts OCTO is stdlib-only with no domain words. Independently verified by grep across all 11 packs — zero violations found.
3. **[strength, no action]** Compat layer delegates rather than duplicates: `score/itf/compat/legacy.py`'s `LegacyTarget`/`DockerTarget` are one-line delegations into the new role-based Facade; `compat/` totals only 1184 LOC across 15 files, too small to be a parallel reimplementation.
4. **[strength, no action]** OCTO's scope is bounded (~2,900 LOC vs ~4,000 for core engine, ~5,800 across all packs), stdlib-only imports are test-enforced, and the two-gate design (what-must-exist / what-must-happen) keeps it a pure function over immutable claims.
5. **[major]** Versioning/contract-disagreement story (`docs/how-to/versioning.md`) is coherent per-rule but combinatorially large (2 gating modes × 2 version-declaration states × 2 claim origins) — ask the author for a single decision table instead of scattered prose, to rule out subtle interaction bugs a design-level read can't catch.
6. **[major]** Conformance layer governance — see "Decisions needed" above.
7. **[minor]** Contract vocabulary ownership — see "Decisions needed" above.
8. **[major]** Commit/review-strategy ambiguity at this scale should be forced to a decision — see "Decisions needed" above.
9. **[verified, no action]** `descriptor.credentials.yaml` files (flagged "sensitive" by graphify's filename heuristic) are schema definitions (`user`/`password`/`key_path` fields with `sensitive: true` metadata), not embedded secrets. Confirmed across all 5 occurrences.
10. **[minor]** The `Facade` escape-hatch pattern (`docs/how-to/migrate.md`) is flagged as a risk in the manifesto's own "abuses" table, but nothing mechanically prevents a facade method from growing into "a second, untested framework" the way the pack-import and OCTO-purity gates prevent similar drift elsewhere. Enforced by review only — consider a similar AST gate if this becomes a real pattern in practice.

**Verdict (architecture agent):** Sound proposal with verifiable structural discipline. Not ready to merge as one unit at this scale — needs the conformance/commit-split decisions and the source-hash gate correction first.

---

## Detailed design (core subsystem internals)

1. **[major]** Session lifecycle ordering not enforced — see cross-cutting #2.
2. **[major]** Secrets masking as global state — see cross-cutting #3.
3. **[minor]** `TargetFileError` (`score/itf/core/frontends/target_file.py:109`, ~45 raise sites) is one undifferentiated exception for every target-file failure mode (bad YAML, schema violations, include cycles, etc.), with no error code/field. No caller currently catches it (verified via grep), so today it only serves "stop and print a message" — but forecloses future structured error handling without a brittle string-match. Add a `code`/`kind` field or subclasses if this needs to surface anywhere beyond stderr.
4. **[minor]** `resolve_contract_to_node()` / `resolve_condition_to_producer()` (`core/octo/std/capability/resolve.py`, `core/octo/std/state/resolve.py`) are ~380-line single functions — internally cohesive (map 1:1 to the README's documented planning steps) rather than accidentally coupled, but worth extracting named helpers per numbered stage for testability.
5. **[minor]** `core/octo/std/state/resolve.py:49` imports the private `_group_by` from `std/capability/resolve.py`, breaking OCTO's own "independent kinds" boundary one level down (Gate 2 depends on Gate 1 internals). Move `_group_by` to `planner/` and import from there in both kinds.
6. **[minor]** `Session._write_artifacts()` is manually called from ~8 separate points across `resolve`/`bring_up`/`select`/`close`/`_reach` rather than centralized — a missing call at a future call site is an easy silent regression (stale artifacts after failure). Consider a decorator/context-manager that writes artifacts on exit regardless of outcome.
7. **[strength, no action]** OCTO genuinely stays stdlib-only (verified by grep across the whole `octo/` tree) and claims are structurally immutable (`@dataclass(frozen=True)`) with precedence centralized in one `planner/precedence.py` — both match the README's claims and are enforced, not just documented.
8. **[minor]** Extensibility for new *contracts* is good (no core/octo changes needed), but extensibility for new *kinds* (a third planning dimension) is unproven — the two existing kinds already couple (finding #5) and no third kind has ever been exercised.
9. **[nit]** `Realization.get()` (`core/engine/realize.py:83-107`) has no cycle guard of its own (would surface as `RecursionError` rather than a planner diagnostic) — defense-in-depth gap, not a live bug since Gate 1 already refuses construction-graph cycles upstream.

**Verdict (detailed-design agent):** The decomposition itself is sound; the gaps are between what the READMEs promise and what the code actually guards against (lifecycle ordering, secrets scoping, exception typing).

---

## Coding: core engine & OCTO (`score/itf/core/`, ~96 files)

1. **[major]** `describe()` never closes the session — see cross-cutting #2.
2. **[major]** `close()` marks itself done before cleanup runs, not exception-safe — see cross-cutting #2.
3. **[major]** `score/itf/core/process/process_wrapper.py:306-316` (`ProcessWrapper.run`) — on `TimeoutExpired`, kills the process and re-raises without a follow-up `communicate()` call, which stdlib docs require after killing a timed-out child to avoid leaked pipe-reader threads/fds. Realistic leak given how many short-lived commands this framework runs. **Fix:** call `wrapper.process.communicate()` once more after `kill_process()` before re-raising.
4. **[minor]** `engine/close.py:52-66,71-80` (`run_startup_checks`/`run_teardown_hooks`) catch only `Exception`, not `BaseException` — a `KeyboardInterrupt` mid-teardown aborts remaining hooks and feeds into finding #2.
5. **[minor]** `process/process_wrapper.py:217-221` — `except OSError` ordered before `except ProcessLookupError` (a subclass), so a process-already-dead race logs via `logger.exception` (scary traceback) instead of the intended `logger.warning`. Reorder the handlers.
6. **[minor]** `process/console.py:182-189` (`PipeConsole.reader`) drops the final unterminated line on EOF — loses exactly the kind of final crash/assert output useful in CI logs. Flush the pending partial line before returning `None`.
7. **[minor]** `process/process_wrapper.py:53-68` — `_HANDLER_INSTALLED`/`_PREVIOUS_SIGTERM` read-modify-write without the file's own `_LOCK`; two threads racing first-time `start_process()` can clobber the saved SIGTERM handler.
8. **[minor]** Secrets global state — see cross-cutting #3.
9. **[minor]** `session.py:781` (`_write_evidence`) swallows failures as bare `repr(exc)`, no traceback — use `logging.exception(...)`.
10. **[minor]** `process/process_wrapper.py:157` — full command line logged at INFO with no secret masking, inconsistent with masking applied elsewhere. Route through `engine.secrets.mask()`.
11. **[minor]** `frontends/target_source.py:263-299` (`load_policy`) — unsynchronized check-then-act on a module-level cache; concurrent loads of the same policy could double-execute import-time side effects. Low impact today (no concurrent callers found).
12. **[nit]** Dead Python-3.10-compat shims now unreachable since the repo targets 3.12 only: `frontends/target_source.py:159-162` (`tomli` fallback — not even a dependency) and `octo/model/shapes.py:265-267` (unreachable `typing.Protocol` fallback). Delete both.
13. **[nit]** `utils/__init__.py` omits `CONSOLE_WIDTH`, which `utils/utils.py` exports — likely-accidental inconsistent public surface.
14. **[nit]** `octo/std/capability/resolve.py:65-78` (`_dedupe_conflicts`) resolves conflicts by raw sort key, ignoring `precedence` unlike every other identity-resolution path — likely intentional, undocumented; add a comment so a future "consistency" refactor doesn't silently change behavior.
15. **[nit]** `octo/model/plans.py` / `planner/resolve.py:108-121` — `Diagnostic.gate` values set at construction are always overwritten by `Octo.resolve`; harmless, worth a comment.
16. **[nit]** `octo/model/shapes.py:104-196` (`_accepts_calls`) is the most complex, least-tested logic in this slice (brute-force signature binding) — flagged for test coverage, no confirmed bug.

**Verdict (coding agent):** The planner is unusually disciplined — pure, deterministic, exception-safe by design. Process layer is solid on the happy path, with real gaps on exception/timeout edges. Session lifecycle in `engine`/`session.py` is the one area needing a real fix before merge.

---

## Coding: contracts & compat layer (`score/itf/contracts/`, `score/itf/compat/`, ~42 files)

1. **[blocker]** PR's central compatibility claim ("source-hash gate") not implemented — see cross-cutting #1. The compat layer is instead a hand-written reimplementation (`legacy.py`/`target.py`) delegating to the new role-based Facade; without an automated drift check, correctness rests entirely on manual review.
2. **[minor, security-adjacent]** `score/itf/compat/plugins/docker.py:64-65` — hardcoded fallback SSH credentials `score`/`score` when env vars are unset. Reproduces original-plugin behavior (not a regression) but is now written in cleartext to env vars; worth documenting as a known weak default.
3. **[minor]** `score/itf/compat/plugins/qemu.py:61` — `--qemu-boot-attempts` semantics changed: merged via `max(config.option.itf_bring_up_attempts, qemu_boot_attempts)` into the session's generic bring-up attempt budget, which changes retry behavior for *other* goals on targets composing more than `platform.up`. Add a regression test comparing attempt counts against the original plugin for a multi-goal target.
4. **[minor]** `score/itf/compat/utils/utils.py` / `compat/utils/bazel.py` — `padder` and `bazel.py` helpers appear to be unreferenced 2025-dated carryovers within `compat/` (only `__init__.py` imports `padder`); verify nothing outside `compat/` still needs `bazel.py`'s functions before treating as dead weight.
5. **[minor]** `score/itf/compat/tools.py:176` — `re.sub(r"\$\{" + key + r"(:-[^}]*)?\}", str(value), text)` passes `value` directly as the replacement string; `re.sub` treats backslashes specially (`\1`, `\g<name>`), so a value containing a backslash (plausible for a Windows-hosted path, e.g. `--qemu-kernel`) could raise `re.error` or substitute wrong text. **Fix:** use `re.sub(pattern, lambda m: str(value), text)`.
6. **[verified, no action]** `$ref` resolution (`score/itf/contracts/__init__.py:152-163`) is sound: cycle detection, clear `ContractError` on missing files, single-reference-per-file enforced.
7. **[verified, no action]** Contract definition schema is consistent across all 21 files; every `conformance:` module reference resolves to a real importable module with a matching `CHECKS` dict.
8. **[minor]** `score/itf/compat/plugins/core_dump.py:60-61` — broad `except Exception` reduces any core-dump extraction failure to a `logger.warning`, including programming errors, not just "no core file found." Likely intentional (evidence collection shouldn't fail the test) but worth narrowing to expected I/O/exec errors.
9. **[verified, no action]** Version-aware contract tooling is real: `contracts/run.py`'s `_ahead_of_version`/`_unoffered_majors` actually compare declared `@major` versions and produce `major-mismatch` diagnostics — substantiates the PR's versioning claim.

**Verdict (coding agent):** The contracts system itself is well-built with no bugs found. The compat layer is a reasonable, mostly-faithful delegation — but the claimed automated safety net protecting it from drift does not exist.

---

## Coding: transport packs (ssh, docker, qemu, serial, net, pcap — ~92 files)

1. **[major, security]** `score/itf/packs/ssh/client.py:66` — `client.set_missing_host_key_policy(paramiko.AutoAddPolicy())` set unconditionally, no opt-in flag for a strict/known-hosts policy. Every SSH connection silently trusts an unknown host key — MITM risk on a shared/contended test network. **Fix:** add a `host_key_policy` field to `ShellConfig`/`connect()`, default can stay permissive for lab convenience but make it explicit/overridable, or at least log a warning once per process.
2. **[major]** `exec.command`'s documented guarantee ("nonzero exit code returned, not raised") is implemented three different ways across packs sharing the contract: ssh (`client.py:98-124`) conforms (returns -1 on timeout); **docker** (`docker/command.py:40-51`) silently ignores the `timeout` parameter entirely (docstring admits it) — a hung command blocks the test run forever; **serial** (`serial/command.py:42-47`→`serial/console.py:166-178`) raises `TimeoutError` instead of returning -1, directly violating the contract. This breaks the PR's core "backend-agnostic, drop-in-replaceable transports" promise. **Fix:** make all three conform to the documented "-1 on timeout, never raised" behavior — docker needs an actual exec-inspect/polling loop with a deadline, serial needs to catch and translate `TimeoutError`.
3. **[major]** `score/itf/packs/pcap/capture.py:69-75` (`TcpdumpCapture.start`) doesn't check the tcpdump process survived startup or inspect its exit code/stderr. If `CAP_NET_RAW` is missing, tcpdump exits immediately but `start()` returns successfully — the failure only surfaces later as a generic "state not reached," hiding the real permissions problem. Contrast with `window()` in the same file, which does this check correctly. **Fix:** mirror `window()`'s readiness check in `start()`.
4. **[minor]** `score/itf/packs/serial/pty.py:46-65` (`PtyPort.__init__`) — if `start_process()` raises after `pty.openpty()`, both `master`/`slave` fds leak (no try/except around the body). Wrap and close both fds on failure.
5. **[minor]** `score/itf/packs/ssh/files.py:227-243` (`Sftp.upload_dir`/`download_dir`) joins remote-reported filenames into local paths with no path-traversal containment check — unlike `docker/platform.py:231-240` (`_extract_coverage`), which does check via `os.path.realpath(...).startswith(...)`. Add the same containment check to ssh's recursive transfer helpers.
6. **[minor]** `score/itf/packs/qemu/qemu.py:123-129` (`Qemu.stop`) raises a bare `Exception` if the process's exit code is nonzero after `kill_process()` — a clean kill commonly reports a signal-derived nonzero code, so every normal teardown risks raising from inside `close()`, potentially masking the real test failure. Only treat unexpected codes as errors, or log instead of raising from teardown.
7. **[nit]** Inconsistent fixture-cleanup pattern: `docker/providers.py:27-31`, `qemu/providers.py:24-28,41-45`, `pcap/providers.py:22-26` use bare `yield x; x.cleanup()` while `serial/providers.py:23-36` wraps in `try/finally`. Functionally equivalent under pytest fixture teardown, but worth normalizing.
8. **[verified, no action]** `descriptor.credentials.yaml` files in `ssh`/`net`/`qemu` packs (and the shared definition they `$ref`) are benign schema stubs, not leaked secrets.

**Verdict (coding agent):** Packs are generally solid (QEMU and serial show careful timeout/lifecycle handling), but the unconditional ssh `AutoAddPolicy` and the three divergent `exec.command` timeout behaviors are real problems that should block merge — they undermine the PR's core value proposition of backend-agnostic transports.

---

## Coding: observability packs & plugins (sim, localhost, dlt, ping, core_dump, plugins — ~121 files)

1. **[major, security]** `score/itf/packs/ping/ping.py:22-33` (`_execute_command`, the legacy `"ping"` capability label wired in `providers.py:22`) runs `ProcessWrapper.run("/bin/sh", ["-c", cmd])` where `cmd` is an f-string built from `address` with no validation or quoting — a shell injection point if `endpoint.host` is ever populated from less-trusted config/discovery data. The newer `icmp.monitor.Pinger` path in the same pack validates via `checked_host()` first. **Fix:** route `address` through `checked_host()` before building the command, or drop the shell entirely (`subprocess.run(["ping", "-c", "1", "-W", "1", address], ...)`).
2. **[major]** `score/itf/packs/sim/ssh/session.py:53-54` (`SimSession.connect()`) only sets `self._gen = self.platform.gen` — never checks `platform.running`. The corresponding `open_session` action (`sim/ssh/steps.py:35-37`) therefore always succeeds, so `shell.open`/`network.reachable` report delivered even if `platform.up` was never run — a target file omitting that relation passes against `sim` but would fail against a real transport. The pack's own README acknowledges the ordering requirement but nothing in code enforces it. **Fix:** check `platform.running` in `connect()`/`open_session` and fail when false, matching real-transport failure behavior.
3. **[major]** `score/itf/packs/localhost/providers.py:40-42` (`make_process`) is a plain (non-generator) capability with no teardown, unlike `make_platform` which yields and stops the platform. `LocalProcess.spawn()` (`localhost/process.py:60-74`) returns a handle the caller must remember to stop; nothing tracks/kills it at teardown. Since localhost runs directly on the CI host (no container/VM boundary), a test erroring before cleanup leaves a real process running on the shared runner indefinitely. **Fix:** track spawned handles in `LocalPlatform` and kill any still-running ones at platform teardown.
4. **[minor, pre-existing]** `score/itf/packs/dlt/receive/window.py:30-37` (`_normalize_timestamp_precision`) can raise `UnboundLocalError` on a malformed timestamp (the except branch only logs, then an unconditional return references unbound names) — one bad record aborts the entire DLT log scan. Carried over unchanged from `main`'s `score/itf/plugins/dlt/dlt_window.py`, but being copied forward into the new pack location, so worth fixing now. **Fix:** bind `seconds, microseconds` before the `try`, or `return` inside the `except`.
5. **[minor]** Two divergent `net.ping` implementations under the same contract (`make_ping` — legacy, unvalidated, see #1; `make_pinger` — validated, socket-based) create a drift risk: callers picking `"ping"` get materially weaker input handling than `"pinger"`. Consider validating both paths or deprecating the legacy one.
6. **[nit]** `score/itf/packs/localhost/process.py:65-69` (`LocalProcess.spawn`) concatenates `binary_path` into the shell command unquoted while `args` are `shlex.quote`d — allowed by the `exec.process` contract's "plain command line" semantics, so not a bug, just a readability nit.
7. **[nit]** `process.py:69` — `binary_path.split()[0]` raises `IndexError` on an empty/whitespace `binary_path`, no guard.
8. **[verified, no action]** `score/itf/packs/sim/contracts/descriptor.credentials.yaml` is a benign `$ref` stub, not a secret leak. `core_dump` and the `plugins/` layer are solid — proper path-traversal guards, streaming hashing, clean delegation with no logic duplication.

**Verdict (coding agent):** Sim pack has a real fidelity gap that can mask boot-ordering mistakes in test authors' target files; ping pack has an actual shell-injection path in its legacy implementation sitting next to a correctly-hardened one; localhost's process capability has no teardown story, which matters more here than in container/VM-backed packs since leaks land directly on the host.

---

## Testing: core, contracts, unit (`test/core`, `test/contracts`, `test/unit`, `test/cli`, `test/pytest_plugins`, `test/plans` — ~48 files)

1. **[blocker]** The "source-hash gate" has no corresponding test anywhere — see cross-cutting #1 (same exhaustive search, independently performed).
2. **[major]** OCTO's raw graph primitives (`toposort_kahn`, `find_cycle` in `core/octo/planner/graph.py`) have zero direct unit tests — only exercised indirectly through higher-level tests. The module's own docstring claims determinism guarantees (lexicographic tie-breaking) that nothing pins down at the unit level. **Fix:** add `test/core/octo/test_graph.py` with direct diamond-dependency and multi-cycle cases.
3. **[minor]** Despite #2, cycle detection *is* well tested at the semantic layers that matter: capability-cycle and state-ordering-ambiguity tests exist and assert on rotation-normalized reporting / `ORDER_AMBIGUOUS`.
4. **[major]** No single test exercises the full session lifecycle with non-trivial structure. The closest (`test/core/itf/test_session.py:48`) is solid but uses a trivial two-node fixture — given `Session` is the most-connected class in the new subsystem, a richer lifecycle test (3+ scopes, shared dependency, evidence-dir assertion) would better match its centrality.
5. **[minor]** Test-to-code proportionality looks reasonable (~1:3 ratio), not thin, modulo finding #2.
6. **[minor]** No flakiness risk found — no real-timing (`sleep`-based) waits anywhere in this test scope.
7. **[nit]** Test quality is generally high: real diagnostic-code/field/evidence assertions (not loose "didn't raise" checks), real fixture modules rather than over-mocking, both happy and failure paths covered for contract validation.

**Verdict (testing agent):** Would not merge on the strength of this slice alone until the source-hash gate claim is substantiated or withdrawn; otherwise strong test quality for genuinely new logic.

---

## Testing: integration & system (`test/integration`, `test/packs`, `test/resources`, `test/tools`, `test/catalog` — ~74 files)

1. **[major]** Non-pcap Docker observability also CI-dark — see cross-cutting #4.
2. **[major]** QNX gate structurally excludes all fork PRs, not just this one — see cross-cutting #4.
3. **[major]** Overrides feature (factory/goal overrides, central to the composition model) has only one real CI assertion (a single scalar) — see cross-cutting #4.
4. **[minor]** `test/integration/legacy/`'s "unchanged across Docker/SSH/serial" framing is imprecise — there's no serial test in `legacy/` at all (correct, since serial didn't exist in the old API), so three-transport parity is actually proven by `test/integration/suite/` (the new engine), not by `legacy/`. Tighten the PR description so reviewers don't conflate the two.
5. **[minor]** No conformance test for `dlt_receive` or `core_dump` packs, despite conformance existing for localhost/serial/docker/docker_ssh, and these two having the most external-process/signal-parsing surface. Add `conformance_dlt_receive`/`conformance_core_dump` mirroring the existing pattern.
6. **[minor]** `test/packs/test_pcap.py` only covers the failed-start teardown path, no happy-path (mocked) start/stop/file-written cycle, despite pcap being the PR's own named validation gap.
7. **[nit]** Fixed ports in fixtures (`qemu_serial.itf.yaml`, `dlt_receive.itf.yaml`) rely on Bazel's per-test network sandboxing to avoid collisions — fine today, worth a comment for future portability.
8. **[nit]** Legacy's `test_dlt` depends on multicast routing a rootless Docker engine doesn't route, per its own README — pre-existing, undocumented CI-runner assumption, worth a BUILD comment given how much this PR touches the Docker pack.
9. **[verified, no action]** `test/tools/test_plan_bench.py` is a genuine regression benchmark (thresholds, `--check` exit code, scale parametrization), not a smoke test. `suite/`/`specific/` tests are real per-capability contract tests with proper teardown.

**Verdict (testing agent):** Mergeable in terms of what CI actually runs being well-covered (localhost, Docker-exec, Docker-SSH, serial, dlt, core_dump) and the compat layer credibly exercised — but the PR description should be corrected to reflect the true gap surface, and untagging non-pcap observability from `manual` is cheap enough to do before merge.

---

## Methodology appendix

- Worktree: `~/ws/eclipse-score/itf/pr-154/` (git worktree off `main`, checked out to `pull/154/head`).
- Knowledge graph: built with `graphify` over `score/itf/` (AST structural extraction for 217 code files — free, deterministic — plus LLM semantic extraction over 116 doc/YAML files via 6 parallel subagents). Result: 2906 nodes, 5823 edges, 195 communities. God nodes (most-connected): `Session` (78 edges), `capability()` (40), `ProcessWrapper` (37), `TargetFileError` (36), `resolve_contract_to_node()` (31), `TargetPlan` (28), `resolve_condition_to_producer()` (27). Full report at `graphify-out/GRAPH_REPORT.md` in the worktree; graph is queryable via `graphify query "<question>"` from that directory.
- Review subagents (9 total, general-purpose, run in parallel per dimension then synthesized): architecture (design docs + graph cross-check), detailed design (core/octo/engine internals), 4× coding (core engine & OCTO / contracts & compat / transport packs / observability packs & plugins), 2× testing (core-contracts-unit / integration-system), plus this synthesis.
- Graphify flagged 5 files repo-wide as containing likely-sensitive content by filename pattern (`descriptor.credentials.yaml` under `contracts/definitions` and 4 packs) and skipped them from graph extraction. All 5 were manually read by the coding agents covering their respective areas and confirmed to be benign contract schemas, not embedded secrets.
