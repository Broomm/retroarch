# Broomm RetroArch packaging notices

The build/release scripts and original test probes in this repository are MIT licensed.
RetroArch is GPL-3.0-or-later; SDL 3.4.0 is Zlib licensed. Retain the upstream
licenses and third-party notices included in each binary package and in the
corresponding source archives published alongside it.

RetroArch source: https://github.com/libretro/RetroArch
SDL source: https://github.com/libsdl-org/SDL
Exact revisions, archive digests and license identifiers: sources.lock.json.

GNU compiler runtime code, when linked, uses the GCC Runtime Library Exception:
https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html
https://www.gnu.org/licenses/gcc-exception-3.1.html
The included exception notice comes from GCC 15.2.0's `COPYING.RUNTIME`:
https://github.com/gcc-mirror/gcc/blob/releases/gcc-15.2.0/COPYING.RUNTIME
SHA-256: `9d6b43ce4d8de0c878bf16b54d8e7a10d9bd42b75178153e3af6a815bdc90f74`.
Compiler toolchains are development-only. Linux uses pinned Nixpkgs GCC;
Windows uses w64devkit 2.4.0 (GCC 15.2.0 with static runtime components).
Apple framework dependencies are supplied by macOS.

These frontend packages include no libretro game cores, console firmware, BIOS,
keys, games, user configuration, saves or save states. Core versions and their
redistribution terms are managed separately by Broomm. A new frontend binary
does not automatically become a trusted build in Broomm.

