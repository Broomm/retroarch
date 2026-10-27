# Engineering guidance

Read PROJECT.md, IMPLEMENTATION.md and README.md before changing behavior. This is an independent build/packaging project; references to Broomm, PGCS and Teddy are evidence, not inherited product instructions or dependency approvals.

- Keep all five native desktop targets in the workflow; Android is explicitly excluded. Distinguish compilation, native probe execution, real Broomm integration and owner gameplay acceptance.
- Use locked Devbox tools. Keep Windows tools pinned/checksummed inside the workspace. The existing Apple SDK/compiler is approved; do not change machine-wide SDKs, permissions or shell profiles or install OS packages.
- Preserve exact upstream pins, licenses and corresponding sources. Treat source/configure changes as compatibility changes. Shared SDL3 is required by the existing injection design. Do not ship toolchains, game cores, personal settings or copyrighted game/firmware payloads.
- Pass paths and arguments as literal process arguments. Bound downloads, response sizes, subprocesses and shutdown. Retain full phase logs and separate independent native checks so one failure does not hide another.
- Preflight archives before writing into private staging; reject traversal, case collisions, unsupported links and oversized archives. Preserve existing outputs, failed build stages and unknown or concurrent state. Do not turn a failed lookup into absence.
- Protect host data in native tests. Cocoa's Documents directory is independent of a HOME override: explicitly configure every writable emulator path. Terminate only the child created by the test and never delete a fixture while its child is alive.
- Keep token permissions minimal and publishing gated on the complete checked artifact/source set. Never log credentials, overwrite release assets/tags or publish from a PR. Version selection through publication must stay serialized.
- Use meaningful regression cases for archive safety, artifact identity and interruption/publication boundaries. Do not hide failures with retries, skipped native checks, relaxed assertions or unexplained timeout changes.
- Record exact tested source/toolchain/host, complete logs, fingerprints, limitations and reproduction commands. Update implementation status alongside recipe changes. Do not claim cross-platform runtime compatibility from one host's build.
- Keep new work reviewable; do not commit, push or publish as an incidental validation step. Do not use computer-control tools without the owner's explicit request.
