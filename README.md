# Broomm RetroArch

Build and release the **SDL3 RetroArch frontend used by Broomm's desktop controller bridge**. This is an independent packaging repository; the Flutter application remains in `broomm`. Android uses external emulator applications and is deliberately excluded.

The source lock selects RetroArch **1.22.2**, revision `1b8917df23625bcf40b67ba6476d9bfb0d30dc65`, and **SDL 3.4.0**. Both source archives are checked against SHA-256 before extraction. Sources are unmodified. Configure/make options enable SDL3, OpenGL Core, Slang, networking and network commands; SDL1/2, Qt, FFmpeg and MCP are disabled. macOS uses OpenGL rather than Metal, matching the validated Broomm controller path. The build uses a shared, bundled SDL3 library so the frontend and injection bridge can use the same runtime.

## Release targets

| Package target | Native runner | Declared runtime baseline |
| --- | --- | --- |
| `macos-arm64` | `macos-26` | Apple Silicon, macOS 13+ |
| `macos-x64` | `macos-15-intel` | Intel, macOS 13+ |
| `linux-x64` | `ubuntu-24.04` | x86-64, glibc 2.39+ |
| `linux-arm64` | `ubuntu-24.04-arm` | AArch64, glibc 2.39+ |
| `windows-x64` | `windows-2025` | Windows 10+, x86-64 |

These cover Broomm's current desktop release architectures. macOS has two thin packages, rather than a cross-compiled universal binary. Native probes run on each architecture. Linux needs the host's graphics/window/audio/device libraries and drivers; Nix, compilers and build SDKs are not runtime requirements. The audit rejects unresolved libraries, SDK runtime paths, architecture mismatches and glibc requirements above 2.39. Native CI and physical controller/gameplay acceptance are different checks; see [IMPLEMENTATION.md](IMPLEMENTATION.md).

Linux disables RetroArch's directly linked PulseAudio backend, which otherwise prevents startup on hosts without `libpulse.so.0`. ALSA and SDL3 audio remain available. SDL3's PulseAudio backend loads the client library dynamically when the host supplies it; select the **sdl3** audio driver in RetroArch to use that route (including a host's PipeWire PulseAudio compatibility service). No PulseAudio client/toolchain library is bundled or installed into the operating system. Physical Linux sound and host-driver acceptance remain separate from the dummy-audio probe.

Linux packages retain native Wayland/EGL support and require the desktop host's `libEGL.so.1`, alongside its graphics drivers. GitHub's headless Ubuntu images lack that loader. If it is the only missing dependency, `test-frontend` copies just the existing locked Devbox **libglvnd 1.7.0** EGL loader into a fresh directory under `build/<target>/test-profile/`, removes that copy's SDK RPATH and supplies its directory only to the probe child's `LD_LIBRARY_PATH`. The original SDK and package remain unchanged. The fixture and actual frontend still undergo dependency auditing; other missing libraries, retained SDK search paths and Nix runtime resolution fail. Logs retain the before/after audits and source/fixture hashes. This provisions a headless test prerequisite without an OS install or graphics-driver bundle; it does not establish rendered graphics. Remove generated fixtures only after their owned child has exited.

Each target publishes:

```text
RetroArch-1.22.2-broomm-<version>-<target>.zip
RetroArch-1.22.2-broomm-<version>-<target>.json
RetroArch-1.22.2-broomm-<version>-<target>.sha256
```

The manifest records source revisions and hashes, packaging commit, compiler/tool versions, required features, native check results and every packaged file's checksum, size and permissions. ZIPs contain `RetroArch.app/` on macOS or `RetroArch/` on Linux/Windows, including the executable, its shared SDL3 runtime and license notices. macOS signatures are **ad hoc**, not Developer ID/notarized signatures. No signing secrets are required.

The build record's `binary` and `sdl` references use bundle-relative paths with forward slashes on every host, matching the ZIP inventory and Linux publisher. Native absolute paths stay inside process arguments. Build readers reject nonportable runtime references; manifest verification additionally requires both referenced files to exist in the inventory. It does not rewrite unsafe or missing references. Preserve an old failed build with Windows backslashes and rebuild into a fresh stage with the corrected recipe, following the existing-output rules below.

Every release also contains the exact RetroArch and SDL source archives and `source-broomm-retroarch-recipes.tar.gz` from the packaging commit. See [NOTICE.md](NOTICE.md) and the upstream notices inside each binary package. The build tools and original probes are MIT licensed; this does not change the upstream software's licenses.

**Game cores, menu asset packs, BIOS, firmware, games, settings and progress are not bundled.** Broomm supplies its own configuration and separately pinned cores. Installing this frontend alone does not install a playable system.

## GitHub workflow and versions

[The workflow](.github/workflows/release.yml) runs on PRs, pushes to `main`/`develop`, and manual dispatch. PRs build and test all five targets without publishing. Pushes publish only after workflow lint, packaging tests, both native probes on every target, and source packaging succeed. Build logs are uploaded independently, including on failure. Once setup/check execution is reached, a packaging regression failure does not prevent compilation and native probes from producing independent results; it still fails the job and prevents publication. A failed target prevents the entire release; `fail-fast` is disabled.

The lint job also checks every root Devbox package/output selector against the real pinned Nix metadata for Linux x64/ARM64 and macOS Intel/Apple Silicon, independently of installing the full SDK. The Linux SDK includes SDL's required XTest headers/runtime (`xorg.libXtst^out`; this pin has no separate `dev` output). Successful JSON results and complete failure diagnostics are retained in `retroarch-sdk-audit`. To run that same preflight with Nix already available:

```sh
nix --extra-experimental-features 'nix-command flakes' eval --json --impure \
  --file tool/check_sdk.nix --apply 'f: f {}'
```

Keep `version.txt` as `MAJOR.MINOR`, initially `0.1`. The workflow scans tags to select the next stable patch on `main` (`v0.1.0`, `v0.1.1`, ...), or the next candidate on `develop` (`v0.1.1-rc1`, `-rc2`, ...). Main promotes the pending patch after candidates. Failed builds consume no tag. PR packages use `-pr<number>.<run>` and are not release candidates. A manual run is build-only unless **publish** is selected, and can publish only from `main` or `develop`.

The complete release train is queued, including version selection. Only the publish job receives `contents: write` and `GITHUB_TOKEN`; no PAT or extra repository secrets are needed. It verifies all archives/manifests/checksums and exact corresponding sources/recipes, checks tag identity, creates a tag at the tested commit and uploads to a draft before making it public. It never overwrites a release, asset or tag. Authentication/transport errors are failures, not evidence that a release is absent.

To start using the workflow, create/push this directory as its own GitHub repository with `main` as its default branch and Actions enabled. This preparation does not create a remote repository or push any commits. The repository's normal Actions policy must allow its publish job's declared `contents: write` permission. No Android signing configuration is involved.

If publication is interrupted, inspect the draft/tag and uploaded hashes first. Do not enable an automatic overwrite/retry. A rerun stops if that release or draft already exists; remove only an inspected incomplete draft through GitHub before retrying its matching run. A tag pointing to a different commit is always refused. Later new runs use existing tags when selecting the next version.

## Local build and checks

On macOS/Linux, use the pinned Devbox environment. macOS uses the already installed Apple compiler/SDK without changing `xcode-select`. Linux dependencies, its compiler and the base Devbox shell are pinned to the final Nixpkgs 24.05 revision in `devbox.json` to retain the glibc 2.39 baseline. Lint tooling has its own locked Devbox environment. No `apt`, Homebrew, system-wide package install, shell-profile edit or global SDK switch is part of the recipe.

```sh
devbox install
devbox run check
devbox --config ci/lint run check
# Choose the native target from the table:
bash tool/run.sh build --target macos-arm64 --version 0.1.0-local
bash tool/run.sh test-sdl --target macos-arm64
bash tool/run.sh test-frontend --target macos-arm64
bash tool/run.sh package --target macos-arm64
```

Windows has no native Devbox backend. Its workflow bootstraps checksum-pinned embedded Python, w64devkit and CMake under `.tool-cache/`; no installer is run against the operating system and no global PATH/registry setting is changed. For a local Windows build, run `./tool/windows.ps1` in PowerShell, then `.tool-cache/python/python.exe tool/ci.py windows-tools`. Invoke the same `tool/ci.py` subcommands with private Python. Each build/probe process receives its private tool PATH and an explicitly resolved executable; no manual PATH edit is needed. The workflow's Git Bash remains separate from w64devkit's BusyBox `bash.exe`: these tools are never exported to `GITHUB_PATH`. Git and PowerShell are supplied by the build host. The native Windows probe validates the resulting PE architecture and runtime dependencies.

Compiler, C++ compiler, resource compiler and pkg-config variables use explicit private paths with forward slashes and `.exe` suffixes. RetroArch's configure helper manually splits PATH on colons and tests exact filenames, so a bare `gcc` can fail its lookup even after CMake successfully used that compiler. Missing private tools fail before configuration instead of selecting a different host tool. Upstream `config.log`, when produced, joins the complete configure output in the uploaded phase logs, including on failure.

Windows can report **WSAECONNRESET / 10054** when a UDP query reaches the emulator before its command port opens. The probe records these port-unreachable events and continues the same bounded startup wait, pacing them at its existing 250 ms receive interval. It still requires a reply from the expected port with the expected state, checks that the owned child remains alive and fails at the original 20-second deadline. Other socket errors remain failures. This changes the CI probe, not the emulator or Windows network settings.

The SDL probe initializes the packaged library, exercises a dummy audio stream, attaches a virtual gamepad, opens it through both controller APIs, and checks a button press/release. The frontend probe audits actual dependencies before executable startup, then checks features, architecture and signatures, compiles an original synthetic core, reads `GET_STATUS`, sends `PAUSE_TOGGLE`, confirms paused state and requests clean `QUIT`. The client binds explicitly to loopback before reserving a separate emulator port. The bounded protocol trace retains attempts, endpoints and unexpected replies as well as successful acknowledgments. No commercial ROM is required. Every writable test location is explicit and private; it does not rely on HOME alone to redirect Cocoa's Documents paths. These headless probes do not verify physical audio, graphics rendering, Steam/gamescope, physical controller routing, injection or save-state compatibility.

Outputs are in `build/<target>/bundle`, release files in `dist/`, and complete phase/probe logs in `.tool-cache/logs/<target>/`. A prior completed build/package is preserved and must be moved aside before another build of the same target. Failed stages remain as `build/.<target>-*` for diagnosis. Remove only this repository's `build/`, `dist/` and `.tool-cache/` to clean generated data after its child processes have exited. Devbox state is confined to `.devbox/` plus the existing Nix tool store.

Failed/timed-out build commands print at most the last 80 lines/16 KiB of diagnostics, preserving the full log artifact. Native probes run only after a successful build and remain independent of each other's result. The frontend uses an explicit, flushed `frontend.log`; on failure it retains its protocol trace and child status and, on macOS, inventories only that child's UDP sockets and takes a bounded stack sample before terminating it. Diagnostic failures never replace the native test failure. A compiled runtime that fails a later check is retained in `retroarch-failed-runtime-<target>` for diagnosis, without the test profile or user data. It is not a checked release package and cannot enter publication. See IMPLEMENTATION.md for current hosted results and verification limits.

If Devbox setup fails while resolving or downloading `cache.nixos.org`, compilation has not started. Preserve that job log and rerun the failed job after network service recovers. A package-cache DNS/connection timeout alone is not evidence of a bad lockfile or corrupt Actions cache; do not clear caches, change source pins, grant permissions or fall back to compiling the SDK from source on that evidence. The workflow does not automatically retry failed native checks.

## Using a release in Broomm

After the first successful hosted release, validate each frontend/core/bridge combination on its native host and use its immutable **versioned** asset URL, archive SHA-256 and per-file hashes in Broomm's reviewed setup/build records. Never use `latest` or trust this package because its version string matches an earlier build. The local macOS artifact here has different executable/runtime hashes from Broomm's old companion archive. Broomm's existing trust records are intentionally not rewritten by this packaging task. Recheck controller reconnect/pairing, pause/overlay, launch/quit, shader behavior and matched save states before adopting new hashes. Emulator installation/update remains an explicit Broomm setup action.

To update upstream software, edit `sources.lock.json` with an exact revision/tag, HTTPS archive URL and independently verified digest, review notices and host requirements, and run the complete matrix. Source/tag discovery never automatically advances these pins. Bump `version.txt` only when choosing a new packaging major/minor train.
