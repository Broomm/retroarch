# Implementation status — 2026-10-27

## Implemented

- Standalone five-target native GitHub Actions build/release workflow; no Android job. Main/develop/PR/manual behavior, queued publication and `version.txt`/tag-derived version progression are documented in README.md.
- Exact RetroArch 1.22.2 revision and SDL 3.4.0 source archives, SHA-256 verification, confined preflight extraction, full phase logs and preserved failed build stages. No upstream source patch or PGCS code was imported.
- Shared SDL3 plus SDL3/OpenGL Core/Slang/network command configuration. Each binary bundle carries its runtime, upstream license files and notices. macOS receives an ad-hoc-signed app; Linux strips SDK loader/search paths and uses the host glibc baseline; Windows uses private pinned Python/w64devkit/CMake tools and bundled runtime notices.
- Native SDL virtual-controller input and frontend feature/architecture/dependency/pause/quit probes. Both native checks are scheduled independently after the build, including when the other fails. Packages require receipts matching the exact executable/library hashes.
- ZIP file inventories, manifests, SHA-256 files, upstream corresponding source archives and an exact Git archive of the packaging recipes. Publication validates the complete same-version/same-commit set, then uses a draft before public visibility. Missing targets, changed artifacts, wrong tags, unknown GitHub responses and existing releases/drafts fail without overwriting them.

## Executed locally

Host: **macOS 27.0.1, Apple M1 Pro / arm64**. Apple Clang **21.0.0**, approved installed Command Line Tools SDK, Devbox **0.18.4**, CMake **3.29.2**, GNU Make **4.4.1**, pkg-config **0.29.2**, Python **3.11.10**.

- Both downloaded source archives match `sources.lock.json`.
- **17** standard-library regression tests pass. They cover stable/candidate/PR versions, unsafe paths/case collisions/links, preserved existing output and corrupt caches, executable permissions/inventory readback, stale native receipts, incomplete releases, exact source/recipe/asset sets, GitHub failure-vs-absence, tag identity and interrupted upload behavior.
- actionlint **1.7.12** and ShellCheck **0.10.0** pass. The lint script explicitly verifies the queued concurrency policy and suppresses only actionlint's obsolete `concurrency.queue` schema diagnostic.
- A complete native macOS ARM64 SDL/RetroArch build passes. Both original native probes pass on the final bundle: SDL ABI/virtual gamepad press-release, actual frontend feature output, Mach-O architecture/minimum OS/runtime dependency auditing, clean signatures, playing/paused command readback and clean QUIT.
- The **29-file ZIP** passes manifest/checksum verification. Fresh extraction preserves every file hash and permission, passes deep/strict codesign verification and runs `--features` without SDK library environment variables.
- Pinned Linux package metadata confirms GCC **13.2.0**, glibc **2.39**, and the required development/scanner outputs on both x64 and ARM64. This is metadata validation, not Linux compilation or execution.

Local artifact: `dist/RetroArch-1.22.2-broomm-0.1.0-local-macos-arm64.zip`, **8,119,704 bytes**, SHA-256 `15f9ced835ca428255952dac32fcc5d5e3d6664dabcaf64ed670f88382099f7c`. Its JSON contains individual executable/runtime hashes and native receipts. It is a local development package, not a published GitHub release. Full logs and manifests remain under `.tool-cache/` and `dist/` (ignored).

Initial failed probes identified the Nix pkg-config target-path wrapper, Devbox's child Apple SDK selection, RetroArch's old minimum-OS default, SDL's separately included main header, otool's inspected-file heading, and the frontend's quit confirmation setting. Each cause was corrected; no timeout or required assertion was relaxed. A preliminary probe also used Cocoa's real Documents defaults and wrote an original test history entry; that single entry was removed while preserving unrelated fields. Tests now explicitly set writable paths, disable history and assert native path confinement. The empty standard music/image playlists were preserved because their prior ownership could not be established.

## Pending acceptance

No remote repository, project commit, push, tag or GitHub release was created. The owner still needs to push this independent project; the first hosted matrix must validate macOS Intel, both Linux architectures and Windows natively. The workflow's actual GitHub permission/upload/publication flow has not run; publication regression tests use controlled fixture responses.

The headless probes do not establish rendered OpenGL/shaders, physical-controller routing, Broomm injection/overlay behavior, Steam/gamescope, saves or compatibility with existing core states. Validate those combinations before changing Broomm's trusted hashes. Game cores and asset packs remain separate. The main Flutter application, its setup manifest, current installed emulators and owner progress are unchanged by this packaging work.

Use README.md's commands to reproduce checks and package a native target. Move aside completed outputs before rebuilding, preserve diagnostic stages until reviewed, and clean generated directories only after their child processes have exited.
