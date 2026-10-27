# Product contract

This project builds the separately distributed RetroArch frontend required by Broomm's desktop SDL3 controller integration. It owns build recipes, source/toolchain pins, binary packaging, native smoke probes, notices and GitHub release automation. Broomm's Flutter workflows, user profiles, emulator setup decisions, game cores, saves and injection bridges remain in the sibling `broomm` project.

Build natively for macOS ARM64/Intel, Linux x64/ARM64 and Windows x64, matching the launcher's current desktop release architectures. Exclude Android because that platform uses external emulator applications. Require shared SDL3, OpenGL Core, Slang and network control commands. Do not replace SDL3 with official SDL2/Metal-only binaries or silently change upstream source versions. Pin and verify source archives before executing their build systems.

Keep development tooling in Devbox on supported hosts, with the approved installed Apple SDK/compiler. Windows uses pinned, hash-verified workspace-only tools because Devbox has no Windows backend. Do not install OS packages, write system settings, bundle compilers or require users to install build tools.

Publish a complete five-target release with exact sources, build recipes, per-file manifests and checksums only after all native checks succeed. Match Broomm's major/minor `version.txt`, tag-derived patch/candidate progression and queued publication. Publication must preserve existing tags/releases/assets and distinguish a confirmed 404 from failed GitHub access. Application/emulator automatic updates and Broomm trust-record changes are outside this project.

Source licenses remain upstream licenses. No libretro cores, ROMs, keys, BIOS, firmware or personal data belong in these release packages. Generated original test content is not shipped. See README.md for artifact layouts, commands and acceptance limits; IMPLEMENTATION.md records executed verification.
