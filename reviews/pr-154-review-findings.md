# Review v2: eclipse-score/itf#154 "Propose composing ITF targets from packs/plugin"

- PR: https://github.com/eclipse-score/itf/pull/154 (branch `ihsa_itf_platform_bc`, head `8a6c468`, base `33e1af4`)
- Scale: 669 files, +53,787/-5,303, 10 commits
- Round 1 (v1, previous commit of this file): 8 Sonnet agents. Round 2 (this file): 9 independent Opus reviewers plus 2 Opus meta-reviewers who checked every v1 finding against the code. Several v1 findings were wrong or overstated. See "Corrections to v1" at the end.
- Format: every finding has `severity`, `path:line` at head `8a6c468`, summary, why, fix, and the reviewers who reported it. Paths are repo-relative, so an agent can turn them into GitHub review comments. Line numbers refer to the PR head. Inline comments only work on lines inside the diff hunks.

## Verdict

Request changes. The layering is sound and enforced by tests: a stdlib-only planner (OCTO), the engine, packs that never import each other, and an isolated compat layer. The new OCTO and contracts code is well tested. The PR cannot merge as is, for four reasons:

1. It renames required CI checks, so the merge queue blocks. (verified)
2. "Original imports, plugins, fixtures, options and Bazel labels remain available" is false in several concrete places, and the promised "source-hash gate" does not exist.
3. The secret handling it promises leaks in reproducible cases.
4. At +53k lines in two commits of 20k and 17k, mostly optional features from a single contributor, maintainers cannot review or own it as one unit. The split proposed below makes it mergeable in steps.

## Must fix before merge

### B1. [blocker] Required status checks are renamed
- `.github/workflows/itf.yml:19`, `.github/workflows/itf-examples.yml:19`
- The ruleset on `main` requires `itf-build-all`, `itf-examples-build-all` and `itf-qnx-qemu-tests-build-all` (`gh api repos/eclipse-score/itf/rules/branches/main`). The PR replaces the first two with `itf-targets`/`itf-build`/`itf-test` and `itf-examples-targets`/`itf-examples-test`, with no `name:` override. Those checks never report, so the PR and the merge queue block.
- Fix: keep the old job names (or add `name:`), or coordinate a ruleset update with the merge.
- Source: overall reviewer, re-verified by the orchestrator.

### B2. [major] The compatibility claim is false in concrete places
The original API does not remain available unchanged. Each item breaks downstream users silently or with an unclear error:
- `score/itf/compat/plugins/targets.py:43` and `test/integration/legacy/BUILD:118,140`: Docker SSH now needs a new `--docker-ssh` flag. Without it, `DockerTarget.ssh()` raises `NotImplementedError: needs an 'endpoint' role`. The PR had to edit the legacy BUILD args, so "legacy tests unchanged, BUILD files included" (`docs/how-to/migrate.md:18,48`) does not hold.
- `score/itf/compat/plugins/docker.py:85`: `docker_configuration` now rejects unknown keys (`ports`, `cap_add`, `devices`, `network_mode`), which main forwarded to `containers.run`.
- `score/itf/compat/legacy.py:57`: `DockerTarget.execute` returns a plain tuple instead of docker-py's `ExecResult`, so `result.exit_code` raises `AttributeError`.
- `score/itf/plugins/qemu/`: `boot.py`, `checks.py` and `qemu_process.py` were deleted with no shim. They were sources of the public `//score/itf/plugins/qemu:qemu` library. `qemu_target()`, `QemuAsyncProcess`, the session `config` fixture, `QemuTarget.guest_panicked` and `DockerTarget.network` are also gone.
- `score/itf/plugins/core.py:15`: the re-export drops `pytest_itf_session_created`, so `compat/targets/policy.py` (readiness check, console evidence) never runs.
- `score/itf/packs/docker/platform.py:121` (via compat): named Docker volumes now raise `FileNotFoundError`.
- `docs/how-to/migrate.md:98-104` already lists five more behavior differences.
- Fix: restore the behavior and add shims, or call the layer "mostly compatible" and list every break in the PR body and migrate.md with a deprecation path.
- Source: contracts/compat, architecture, integration testing and overall reviewers.

### B3. [major] The "source-hash gate" does not exist, and the old API surface is not guarded
- PR body ("A source-hash gate preserves the legacy test bodies"), `test/integration/legacy/check_shims.py:35`
- Every reviewer and both meta-reviewers searched `tools/`, `test/`, `scripts/`, `bazel/`, `.github/` and the commits. No hashing or diff of legacy tests exists. What does exist: all 11 `test/integration/legacy/test_*.py` files plus `conftest.py` are byte-identical to main (`git hash-object` matches) and run in CI. `check_shims.py` only checks that shim modules contain re-exports. It still passes if a re-export is deleted, so drift in the old API surface goes undetected. B2 shows the drift already happened in BUILD args and removed modules.
- Fix: correct the PR text to name the mechanisms that do exist. Add a test with pinned sha256 values for the legacy test files and BUILD args, plus a public-name snapshot test for each original module.
- Severity note: v1 called this a blocker. The meta-reviewer graded it minor (a description error). It is major here because B2 shows the missing guard has real consequences.

### B4. [major] Secrets leak despite masking
- `score/itf/core/engine/secrets.py:60-75`, `score/itf/core/artifacts.py:34`, `score/itf/core/engine/evidence.py:175,186`, `score/itf/core/adapters/pytest/run_logging.py:90`, `score/itf/core/engine/log.py:40`
- Masking runs after `json.dumps` or `html.escape`. A secret containing `"`, `\`, `&`, `<` or any non-ASCII character is written unmasked. For example, `pässw"rd1` appears as `pässw\"rd1` in the JSON output. The log record factory masks only `getMessage()`, not `extra={"itf_event": ...}`, tracebacks or `stack_info`. Two reviewers reproduced this independently.
- `score/itf/core/frontends/target_file.py:484,559`: a `with:` value of exactly `${NAME}` is parsed as YAML. `PASSWORD=123456` becomes an int, which the schema rejects and `Secrets.add` (`secrets.py:38`) never masks. `p@ss: [x` raises a raw `yaml.ParserError` that echoes the secret. A list or mapping makes `NodeClaim` unhashable and raises `TypeError` before validation.
- Fix: mask the data structure before serializing. Mask `record.__dict__["itf_event"]` and the final formatted text with a `logging.Filter`. Keep `${NAME}` values as strings unless the field's type needs coercion, and wrap `YAMLError` in `TargetFileError` without the secret text.
- Source: detailed design and core-engine coding reviewers, both reproduced.

### B5. [major] Session lifecycle has no state machine and broken error paths
- `score/itf/core/session.py:237,466,486,728-758`, `score/itf/core/engine/bringup.py:146`, `score/itf/core/adapters/pytest/plugin.py:208,242-251,369,385`
- Reproduced: `bring_up()` before `resolve()` dies with `AttributeError: 'NoneType' ... 'steps'`. After `close()`, `resolve()` and `bring_up()` run again, but `close()` is now a no-op, so what they build is never unwound. A second `resolve()` orphans every built instance.
- A factory failure raises `RealizationError`, which `bring_up` does not catch (it only catches `ExecutionError, ProbeFailed`). pytest then shows INTERNALERROR instead of a `NotReady` report, and `_failure_text` in the evidence stays empty.
- `close()` sets `_closed = True` before cleanup. An `OSError` in `_write_artifacts()` (`:756`) skips `_write_evidence` and `secrets.uninstall`, which leaves the process-wide log factory installed.
- The plugin calls `close()` only if bring-up happened. `--itf-plan`, `--collect-only`, blocked gating and resolve failures never write evidence or run teardown hooks.
- Fix: add a `Phase` enum (NEW → RESOLVED → BROUGHT_UP → CLOSED) with one guard per public method. Wrap `RealizationError` into `ExecutionError`. Put reap, deactivate and uninstall in `finally`. Always call `close()` in `pytest_sessionfinish`.
- v1 got the shape of this right, but its "describe() in a loop" scenario was invented (no caller) and its "no retry possible" claim was overstated.

### B6. [major] The default gating silently shrinks test coverage
- `score/itf/core/adapters/pytest/plugin.py:232,295`
- In the default "loose" mode, tests that are incompatible or unknown are deselected through `pytest_deselected`. They never reach JUnit. If a target loses a role, the matching tests disappear and CI stays green. On main, `requires_capabilities` skipped tests visibly. No CI job uses `--itf-strict-gating` or asserts a selection count.
- Fix: make strict gating the default, report gated tests as skips with a reason, or assert an expected selection count per `py_itf_test`.
- Source: architecture and integration testing reviewers.

### B7. [major] "Tests run unchanged across backends" breaks on timeouts and fakes
- `exec.command` (`score/itf/contracts/definitions/exec.command.yaml`) says a timeout "is an error of the factory". The implementations disagree:
  - `score/itf/packs/ssh/command.py:44-49` ignores `timeout`, waits a hard-coded 180 s and returns -1. It also corrupts binary output with `errors="replace"`.
  - `score/itf/packs/docker/command.py:40-51` never times out, because `exec_client.py:61` disables the socket timeout. This was already true on main.
  - `score/itf/packs/serial/console.py:175` and `score/itf/packs/qemu/console.py:102` raise `TimeoutError`, which matches the contract.
- `score/itf/packs/sim/docker/platform.py:78`: `SimExec.exec` always returns exit code 0, even when the platform is stopped. That fails the repo's own conformance check `a_failing_command_reports_its_exit_code_without_raising`.
- `score/itf/packs/sim/dlt/logs.py:55` returns strings where the contract and `DltCapture` return `Message` objects. Tests pass on sim and fail on real backends.
- Fix: make SSH and Docker raise `TimeoutError` at `timeout`, add a timeout conformance check, and make sim honor `exec.command` and `observe.logs` (or drop those contracts from sim).
- Severity note: the transport and observability reviewers rated this major. The meta-reviewer rated the timeout part minor, because the contract wording is loose and Docker's behavior is pre-existing.
- v1 claimed the contract says "-1 on timeout, never raised". That wording is not in the contract.

### B8. [major] Process and signal handling in `ProcessWrapper`
- `score/itf/core/process/process_wrapper.py:64`: `_uninstall_handler` calls `signal.signal` from whatever thread stops the last process, which raises `ValueError` there (reproduced).
- `:42`: the SIGTERM handler calls `reap()`, which takes the non-reentrant `_LOCK`. If Bazel's SIGTERM arrives while the lock is held, the run deadlocks and Bazel SIGKILLs it, orphaning children.
- `:306`: a KeyboardInterrupt during `communicate` leaves the child running and untracked.
- `:253-261`: `ContextVar.set` without restoring the token. When session B closes, session A's ownership is lost, and processes started from test threads have no owner.
- Fix: guard handler changes to the main thread. Use an `RLock`. Kill on `BaseException`. Keep and `reset()` the token.
- Source: core-engine coding and detailed design reviewers.

### B9. [major] OCTO state planning silently drops a step
- `score/itf/core/octo/std/state/resolve.py:265-268`
- Collapsed steps are keyed by `min(delivers)`. Two groups can share a key, and the later one overwrites the earlier one with no diagnostic, even when the target file pinned the state with `by:`. Reproduced.
- Fix: emit `STATE_CONFLICT` on a key collision, or key steps by instance.

### B10. [major] Pack regressions with user-visible impact
- `score/itf/packs/dlt/receive/capture.py:115,124`: the PR's own reconnect patch (`third_party/dlt/custom/0005-...patch`) keeps `dlt-receive` alive forever. `is_active()` therefore stays True after the daemon moves to a new port, and the receiver is never restarted. The patch itself retries without limit (`:40`).
- `score/itf/packs/dlt/receive/capture.py:138`: `lines()` never raises on a dead stream (the contract requires `RuntimeError`) and re-parses the whole file on every call, so memory is unbounded.
- `score/itf/packs/docker/platform.py:115-144`: `start()` cannot run a second time, because the fixed container and network names cause 409 conflicts. A died container can never recover. The QEMU pack handles this case.
- `score/itf/packs/ssh/process.py:90` with `score/itf/packs/ssh/session.py:104`: spawned processes share the shell's connection, so re-reaching `shell.open` kills them. Main used one connection per process.
- `score/itf/packs/core_dump/collector.py:24`: every collect downloads every dump in full into system temp, only to hash it. A 2 GB dump is re-transferred each time and can fill tmpfs.
- `score/itf/packs/pcap/capture.py:69-75`: `start()` neither waits for tcpdump to be ready nor keeps its stderr (`keep_output`), so a missing CAP_NET_RAW surfaces later as a generic error. `window()` in the same file handles this correctly.

## Decisions for maintainers (questions to the author)

- **Scope and split.** Python under `score/` grows from about 4.1k to about 22.6k lines. One contributor wrote all of it, so the bus factor is 1 for OCTO, the engine and 10 packs. Commits `3e54dd5` (+20k) and `0eea458` (+17.5k) delete and then restore `score/itf/plugins/*`, so the history is not bisectable. Proposed order:
  1. `702c28f` QNX timer/network fix.
  2. `928ad23` Pages fix, pushed from an upstream branch so the docs jobs actually run (they skip on fork PRs).
  3. The DLT reconnect patch, with a retry limit.
  4. DR-002 and the concept docs, design only, to be agreed first.
  5. Build groundwork: pinned dependencies, the toolchain change with its rationale, plugin attributes.
  6. Core (OCTO, session, frontends, adapters) plus `itf_target`/`py_itf_test`, purely additive.
  7. Contracts, compat and 2 reference packs, with the plugins turned into shims, the hash gate added, and the CI job renames coordinated with the ruleset.
  8. Remaining packs.
  9. CLI.
  10. Examples and docs.
  11. Optional extras: conformance, benchmark, catalog, vocabulary gates, skill, contract PR template.
- **Contract ownership.** DR-002 decision 1 says "ITF knows no contracts" (`docs/decisions/DR-002-arch-target-composition.md:44`), but the PR ships 21 canonical definitions and gates on their majors. Downstream projects will treat them as a standard. Decide whether they are a stable, versioned API. DR-002 is still "Proposed" (`:19`).
- **Format versioning.** The target YAML is the main public API, yet it has no schema version and rejects unknown keys (`score/itf/core/frontends/target_file.py:103`). Add `itf: 1` or similar before the first release.
- **MVP.** Versioning (two separate major systems), release-tag checks, a 1,135-line HTML report, cross-run evidence and `itf up` could all be deferred until a downstream project asks for them.
- **Conformance.** Commit `9206c18` is not cleanly droppable, because it also edits the CLI, the contracts module, all shared definitions and DR-002. Its rules also contradict the contracts README ("a pack may not import them", but `score/itf/packs/ping/conformance_net.py:15` does).

## Further findings (Opus round)

### Build, CI, docs (not reviewed in v1)
- [major] `bazel/py_itf_test.bzl:45,191,217`: the public macro changes behavior silently. `test_binary` moves from `cfg="exec"` to a host transition, `env` values now go through `$(location)` expansion, and the core plugin is injected only when there is no `target`. Ship these separately with a migration note.
- [major] Rebase needed: current main (`118287d`, after the Python 3.12-only change #156) no longer merges cleanly with the PR (`git merge-tree` reports conflicts). Note that the dead `tomli` and 3.10 branches, and the missing `tomli` in the 3.10 lock (`score/itf/core/frontends/target_source.py:159-162`), only matter while the PR base still declares 3.10.
- [minor] `MODULE.bazel:195`: `tcpdump` is a non-dev `bazel_dep`, so every consumer pulls in libpcap for an optional pack.
- [minor] `MODULE.bazel:206-243`, `.bazelrc:4`, `examples/MODULE.bazel`: the hermetic GCC 12.2 toolchain and the removal of `toolchains_llvm` are repo-wide build policy changes and need their own PR.
- [minor] `docs/_generated/catalog.html` (52 KB minified) and `docs/reference/contracts.md` are committed generated files, each guarded by two gates. Generate them at docs build time instead.
- [minor] `test/integration/README.md:199`: a contributor's personal path (`/home/iheb/.qnx`) appears in upstream docs. Replace it with `$HOME/.qnx`.
- [minor] `.github/workflows/itf.yml:20,65,69`: `itf-targets` duplicates `bazel build //...`, and the localhost coverage target runs twice.
- [minor] `tools/benchmark/native-qemu-2026-11-10.md`: a dated, machine-specific snapshot that will go stale.
- [nit] `.github/workflows/docs.yml:97-107`, `itf.yml:76`: actions referenced by tag while `download-artifact` in the same job is pinned to a SHA. Pin all of them.
- [nit] Missing license headers: `tools/contract_rules.yaml:1-11` (closing line), `score/itf/cli/versions_cmd.py`, `score/itf/compat/__init__.py`, `score/itf/packs/core_dump/collector.py`, `score/itf/packs/dlt/receive/{window,process}.py`.
- Verified clean: `bazel run //:docs` builds with no warnings. The new pip dependencies are hash-pinned and license-compatible. `.env.example` holds only the demo login `score`/`score`. All public Bazel labels survive.

### Design
- [major] `score/itf/core/artifacts.py`, `score/itf/core/engine/evidence.py`: about 25 `getattr(session, "_x", default)` reads of private fields. `artifacts.py:185` reads `_pre_observation`, which nothing ever sets, so `run.json` always shows `[]`. Fix: have `Session` publish one frozen `RunRecord`.
- [major] `score/itf/core/session.py:134-227`: `Session` is an 840-line object with about 45 fields that mixes planning checks, retry, drift detection, metering, taint propagation, secrets and artifact writing. Split these into collaborators.
- [major] `score/itf/core/adapters/pytest/plugin.py:208,385`: there is no xdist story. Every worker brings up the same target and writes to the same `itf-run`, and with `reuse=probe` one worker can unwind a target another is using. Refuse xdist or bring up on the controller only.
- [minor] `score/itf/core/engine/close.py:91-116`: `unwind` duplicates `Realization.discard` and leaves dismantled instances reachable.
- [minor] `score/itf/core/engine/publish.py:97`, `score/itf/core/engine/bringup.py:15-17`: actions may declare `config` but never receive it (they get a `TypeError`), and the docstring claims otherwise.
- [minor] 14 unrelated exception classes, while `plugin.py:249` catches `TypeError`/`ValueError` wholesale. Introduce `ItfUserError`/`ItfRuntimeError` bases.
- [minor] `score/itf/core/engine/registry.py:82,111-185`: the hookwrapper rejection misses `pytest_impl` wrappers, and an AST lint runs on every session at runtime.
- [minor] `score/itf/contracts/usage.py:31`: contracts import engine internals while the engine imports contracts, a package-level cycle.
- [minor] `score/itf/packs/*/contracts/*.yaml`: 65 one-line `$ref` manifests duplicate what the decorators already declare.
- [minor] `score/itf/core/octo/model/kinds.py:23-34`: five `Kind` fields that nothing reads.
- [minor] `score/itf/core/engine/evidence.py:145`: `outcome` stays "ok" despite teardown failures or leaked processes.

### Contracts, CLI
- [minor] `score/itf/compat/plugins/targets.py:41`: option values go into the process-wide `os.environ`, and stale values from CI are not cleared.
- [minor] `score/itf/compat/plugins/docker.py:76`: `target_init` never tears the container down, and the module-global `_applied` is pointless.
- [minor] `score/itf/compat/tools.py:176`: `re.sub` uses the user value as its replacement string, so a backslash breaks it and `: ` corrupts the YAML.
- [minor] `score/itf/contracts/rules.py:72`: a mistyped `--rules` path is parsed as YAML text.
- [minor] `score/itf/contracts/__init__.py:128,133,161`: list fields given as strings are split into characters.
- [minor] `score/itf/contracts/inventory.py:225,304`: broken conformance modules are swallowed silently.
- [minor] `score/itf/cli/contracts_cmd.py:188`: `conformance: true` with no target files passes.
- [minor] `score/itf/contracts/pytest_conformance.py:66`: `reach()` in `finally` masks the check's own result.
- [strength] Contract loading uses a safe YAML loader, detects `$ref` cycles and gives clear errors. HTML output is escaped. The core_dump path guard got stricter (`is_relative_to`).

### Packs (minor and pre-existing)
- [minor, pre-existing] `score/itf/packs/ssh/client.py:66`: `AutoAddPolicy` with no known_hosts, same as main. Consider an opt-in strict mode. Not a regression.
- [minor, pre-existing] `score/itf/packs/ping/ping.py:22-33`: `sh -c` with an unvalidated host, as with main's `os.system`. The value comes from the test author's own target file. Route it through `checked_host`.
- [minor] `score/itf/packs/ping/ping.py:88`: `wait_up(None)` probes once instead of polling, so the `ping.ping` label fails `platform.up` while the target boots.
- [minor, pre-existing] `score/itf/packs/qemu/qemu.py:221`: `hostfwd` binds all interfaces while guest SSH defaults to root with an empty password. Bind to 127.0.0.1.
- [minor] `score/itf/packs/ssh/files.py:159`: unquoted `mkdir`, and its check is an `assert` (dropped under `python -O`).
- [minor] `score/itf/packs/ssh/process.py:93`, `score/itf/packs/localhost/process.py:68`: the metacharacter check scans quoted arguments, which drops `exec`, so the reported PID is the shell's and `stop()` orphans the program.
- [minor] `score/itf/packs/ssh/process.py:96-102`, `score/itf/packs/serial/console.py:85-88`, `score/itf/packs/serial/pty.py:48-63`: resource leaks on error paths.
- [minor] `score/itf/packs/serial/pty.py:66-78`: when the shell exits, the read loop spins at 100% CPU instead of signalling EOF.
- [minor] `score/itf/packs/qemu/console.py:37,47-56`: unbounded buffer, and swallowed reader errors.
- [minor] `score/itf/packs/docker/files.py:31`: downloading a directory now silently writes the first file in the archive (main raised).
- [minor] `score/itf/packs/ssh/process.py:112`, `score/itf/packs/docker/process.py:103`: `get_output` splits lines differently per backend.
- [minor] `score/itf/packs/localhost/files.py:40`: relative paths resolve against cwd, not `platform.root`.
- [minor] `score/itf/packs/dlt/on_target/receiver.py:77`, `score/itf/packs/dlt/receive/process.py:48`: unchecked `chmod`, and a shared default `.dlt` file between roles.
- [nit, pre-existing] `score/itf/packs/dlt/receive/window.py:30-37`: `UnboundLocalError` on a malformed timestamp.
- [nit] `score/itf/packs/pcap/capture.py:91`: `window(name)` accepts path separators.

### Testing
- [major] `test/integration/BUILD:134-190,554`: no observability target (Docker DLT host-network, QNX ± serial ± pcap, specialist) runs in any workflow, so "QNX observability validated" rests on local runs. The plain Docker `test_command`/`test_console` do run via `:docker` (v1 got this wrong).
- [major] `test/core/itf/test_session.py`, `score/itf/core/engine/close.py:104-113`: no real-session test for a raising finalizer, a double `close()`, or `close()` after a failed bring-up (`:88-93` asserts nothing).
- [minor] Unit tests dropped with no replacement: `--qemu-boot-attempts` through to the re-plan (`test/unit/test_qemu_boot_retry.py`), and the `resolve_ssh_auth` negative case (`score/itf/packs/qemu/descriptors.py:49`).
- [minor] Secrets tests miss `${ENV}` values and `target.html`, `diagnostics.txt` and the event log.
- [minor] No OCTO diamond test (shared provider built once, closed once). `toposort_kahn`/`find_cycle` have no direct tests.
- [minor] `test/integration/specific/test_override_values.py:28`: skips instead of failing when the override is missing.
- [minor] `test/core/itf/test_process_owner.py:52`: depends on a global registry, so the result depends on test order.
- [minor] Fixed ports: `overrides/qemu_serial.itf.yaml` (4444) can collide within one `bazel test` invocation, since `.bazelrc` sets no network sandboxing. Also `dlt_receive` (13490).
- [minor] `test/integration/suite/test_logs.py:164`, `test/integration/specific/test_dlt_on_target.py:41`: need multicast routing, which rootless Podman lacks.
- [nit] `docs/decisions/DR-001-infra.md:128` still points to `test/unit/`.
- [strength] OCTO determinism is tested over 200 shuffles and across `PYTHONHASHSEED` values. The attribute-plugin test now actually runs. Mismatch tests assert real behavior. One Protocol-typed `test_exec.py` runs against container, SSH, serial and serial_device in CI. Workflows add no `pull_request_target`, no `continue-on-error`, and leak no secrets to forks. All 8 core test targets pass locally (run by the core-testing reviewer).

## Strengths (confirmed by several reviewers)

- Layer rules are enforced by tests, not just documented (`test/core/gates/`): OCTO imports only the stdlib and contains no domain words, packs never import each other, capability modules never import pytest. A runtime `CrossContributorImport` check backs this up.
- OCTO is a pure planner over frozen claims with one precedence resolver and a closed set of diagnostic codes. `test/core/kinds/test_third_kind.py` proves a third kind plugs in.
- The compat layer is isolated: the core never imports it, and it delegates instead of reimplementing (1,184 LOC in 16 files).
- The legacy test bodies are byte-identical to main and run in CI.

## Corrections to v1 (verified by the Opus meta-reviewers)

Refuted, remove:
- The localhost pack leaks spawned processes. `ProcessWrapper` tracks and reaps every process.
- Plain Docker observability is CI-dark. `:docker` runs `suite/test_command.py`.
- A third OCTO kind is unproven. `test_third_kind.py` exists.
- The SIGTERM handler has a race between threads. `signal.signal` only works on the main thread.
- `process_wrapper.py:157` logs secrets. The masked record factory covers that log line.
- Delete the 3.10 shims. The PR base still declares 3.10.
- `CONSOLE_WIDTH` is missing from `utils/__init__.py`. Main exported only `padder`, so the shim is faithful.
- `compat/utils` is dead code. It backs public shims.
- The legacy suite "conflates serial". The PR text refers to the new suite.

Wrong or overstated:
- exec.command "-1 on timeout, never raised". That wording is not in the contract (corrected in B7).
- ssh `AutoAddPolicy`, the ping shell path, QEMU `stop()` raising, and sftp traversal are pre-existing on main. Downgraded.
- Secrets as "global state". Masking across several sessions is intended and tested (`test/core/itf/test_secrets.py:81`). The real leaks are in B4.
- QNX fork-PR gate as a "CI-trust gap". It is identical on main, and QNX runs in the merge queue before merge.
- `close()`/`describe()` scenarios. `describe()` has no internal caller, and the real gap is `session.py:756-758` (see B5).
- Location fixes: version checks live in `score/itf/contracts/usage.py:176,214`. `load_policy` is at `target_source.py:278-299`. Compat has 16 files.
- v1's own prompt said the DLT reconnect fix was on main. It is this PR's head commit `8a6c468`.

## Method and caveats

- Worktree `~/ws/eclipse-score/itf/pr-154` at `8a6c468`. A graphify graph of `score/itf/` (2906 nodes, 5823 edges) is at `graphify-out/` in that worktree.
- Opus round:
  - 9 independent reviewers: architecture, detailed design, 4 coding slices, 2 testing slices, and an overall reviewer covering build, CI, docs and examples.
  - 2 meta-reviewers who checked every v1 finding.
  - The reviewers were not given v1. One reviewer (integration testing) saw the untracked v1 file in the worktree, so its agreement on B3 is not fully independent.
- Findings marked "reproduced" were executed by a reviewer. Everything else comes from reading the code at `8a6c468`.
- Where reviewers disagreed on severity, this file states both views.
