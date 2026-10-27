#!/usr/bin/env python3
"""Pinned RetroArch/SDL build and release tooling; Python standard library only."""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".tool-cache"
LOCK = json.loads((ROOT / "sources.lock.json").read_text())
TARGETS = LOCK["targets"]
VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc[1-9]\d*|-pr[1-9]\d*\.[1-9]\d*|-local)?$")
CONFIGURE = [
    "--enable-sdl3", "--disable-sdl2", "--disable-sdl", "--enable-opengl_core",
    "--enable-slang", "--enable-networking", "--enable-command",
    "--disable-qt", "--disable-ffmpeg", "--disable-mcp",
]
REQUIRED = ("SDL3", "COMMAND", "NETWORKING", "OPENGL_CORE", "SLANG")
ARCHES = {"arm64": "arm64", "aarch64": "arm64", "amd64": "x64", "x86_64": "x64"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(args, *, cwd=None, env=None, log=None, timeout=1800):
    args = list(map(str, args))
    # CreateProcess does not use a supplied child PATH to locate argv[0].
    # Resolve private Windows tools before spawning; their children inherit PATH.
    if sys.platform == "win32" and env is not None:
        args[0] = shutil.which(args[0], path=env.get("PATH", "")) or args[0]
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), " ".join(args), flush=True)
    if log:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as output:
            output.write(f"UTC {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
            output.flush()
            try:
                result = subprocess.run(args, cwd=cwd, env=env, stdout=output,
                                        stderr=subprocess.STDOUT, timeout=timeout, check=False)
            except subprocess.TimeoutExpired:
                output.flush()
                print_log_tail(log)
                raise
        if result.returncode:
            print_log_tail(log)
            raise RuntimeError(f"Command failed ({result.returncode}); complete log: {log}")
        return log.read_text(encoding="utf-8", errors="replace")
    return subprocess.check_output(list(map(str, args)), cwd=cwd, env=env,
                                   stderr=subprocess.STDOUT, timeout=timeout, text=True)


def print_log_tail(log):
    # Bound console output while retaining the complete original artifact.
    with Path(log).open("rb") as stream:
        stream.seek(max(0, os.fstat(stream.fileno()).st_size - 16384))
        tail = stream.read().decode("utf-8", errors="replace")
    tail = "\n".join(tail.splitlines()[-80:])
    tail = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", tail)
    print(f"Last diagnostic output from {log}:\n{tail}", file=sys.stderr, flush=True)


def download(spec, directory=None):
    directory = directory or CACHE / "downloads"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / spec["sha256"]
    if target.exists():
        if digest(target) != spec["sha256"]:
            raise ValueError(f"Changed cached source preserved: {target}")
        return target
    request = urllib.request.Request(spec["url"], headers={"User-Agent": "Broomm-RetroArch-build"})
    descriptor, temporary = tempfile.mkstemp(prefix=f".{spec['sha256']}-", suffix=".part", dir=directory)
    os.close(descriptor)
    stage = Path(temporary)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response, stage.open("wb") as out:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise ValueError("Source download exceeds 512 MiB")
                if time.monotonic() - started > 600:
                    raise ValueError("Source download exceeded ten minutes")
                out.write(chunk)
        if digest(stage) != spec["sha256"]:
            raise ValueError("Source archive checksum mismatch; nothing extracted")
        try:
            os.link(stage, target)
        except FileExistsError:
            if digest(target) != spec["sha256"]:
                raise ValueError("Concurrent cached source differs and was preserved")
        return target
    finally:
        stage.unlink(missing_ok=True)


def safe_name(name):
    path = PurePosixPath(name.rstrip("/"))
    if not name or "\\" in name or path.is_absolute() or any(p in ("", ".", "..") for p in name.rstrip("/").split("/")):
        raise ValueError(f"Unsafe archive path: {name!r}")
    if ":" in name or any(ord(c) < 32 for c in name):
        raise ValueError("Unsupported archive path")
    return path


def extract(archive, destination, *, zipped=False):
    """Preflight the complete archive before writing in a new private directory."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Extraction destination already exists")
    seen, total = set(), 0
    if zipped:
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                path = safe_name(item.filename)
                key = str(path).casefold()
                if key in seen or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Duplicate or linked ZIP member")
                seen.add(key)
                total += item.file_size
            if total > 4 * 1024**3 or len(seen) > 200000:
                raise ValueError("Oversized source archive")
            destination.mkdir(parents=True)
            source.extractall(destination)
    else:
        with tarfile.open(archive, "r:*") as source:
            members = source.getmembers()
            for item in members:
                path = safe_name(item.name)
                key = str(path).casefold()
                if key in seen or not (item.isfile() or item.isdir() or item.issym()):
                    raise ValueError("Duplicate or unsupported TAR member")
                seen.add(key)
                total += item.size
                if item.issym():
                    # Source links may point internally; never through an absolute/traversing target.
                    safe_name(item.linkname)
                    if str(path.parent / item.linkname).startswith("../"):
                        raise ValueError("Escaping source link")
            if total > 4 * 1024**3 or len(members) > 200000:
                raise ValueError("Oversized source archive")
            destination.mkdir(parents=True)
            source.extractall(destination, filter="data")


def next_version(base, branch, tags):
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)", base):
        raise ValueError("version.txt must contain MAJOR.MINOR")
    parsed = [re.fullmatch(r"v(\d+\.\d+)\.(\d+)(?:-rc([1-9]\d*))?", tag) for tag in tags]
    parsed = [m for m in parsed if m and m[1] == base]
    patch = max([int(m[2]) + 1 for m in parsed if m[3] is None] + [0])
    core = f"{base}.{patch}"
    if branch == "main":
        return core
    if branch == "develop":
        rc = max([int(m[3]) + 1 for m in parsed if int(m[2]) == patch and m[3]] + [1])
        return f"{core}-rc{rc}"
    raise ValueError("Only main/develop can publish")


def plan():
    tags = run(["git", "tag", "--list"], cwd=ROOT).splitlines()
    branch = os.getenv("GITHUB_REF_NAME", "main")
    event = os.getenv("GITHUB_EVENT_NAME", "local")
    publish = event == "push" and branch in ("main", "develop")
    if event == "workflow_dispatch":
        publish = os.getenv("REQUEST_PUBLISH") == "true" and branch in ("main", "develop")
    version = next_version((ROOT / "version.txt").read_text().strip(), branch if branch in ("main", "develop") else "main", tags)
    if event == "pull_request":
        version = version.split("-rc")[0] + f"-pr{os.environ['PR_NUMBER']}.{os.environ['GITHUB_RUN_NUMBER']}"
    result = {"version": version, "tag": f"v{version}", "publish": str(publish).lower(),
              "prerelease": str(branch == "develop").lower()}
    if output := os.getenv("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write("".join(f"{k}={v}\n" for k, v in result.items()))
    print(json.dumps(result))


def native_target(target):
    if target not in TARGETS:
        raise ValueError("Unknown desktop target")
    host = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}[platform.system()]
    if target != f"{host}-{ARCHES[platform.machine().lower()]}":
        raise ValueError(f"Native runner required for {target}")


def windows_tools():
    if sys.platform != "win32":
        raise ValueError("Windows tooling is installed only on Windows")
    tools = CACHE / "windows-tools"
    tools.mkdir(parents=True, exist_ok=True)
    toolkit = download(LOCK["windows_tools"]["w64devkit"])
    if not (tools / "w64devkit/bin/gcc.exe").exists():
        executable = tools / "w64devkit-installer.exe"
        shutil.copy2(toolkit, executable)
        run([executable, "-y", f"-o{tools}"], timeout=300)
    cmake_dir = tools / "cmake"
    if not cmake_dir.exists():
        extract(download(LOCK["windows_tools"]["cmake"]), cmake_dir, zipped=True)
    paths = windows_tool_paths()
    for path in paths:
        if not path.is_dir():
            raise ValueError("Incomplete private Windows toolchain")
    print("Private Windows toolchain ready; no system installation")
    for path in paths:
        print(f"Build child PATH entry: {path}")


def windows_tool_paths():
    tools = CACHE / "windows-tools"
    return [tools / "w64devkit/bin", tools / "cmake/cmake-3.31.7-windows-x86_64/bin"]


def environment(target):
    env = dict(os.environ)
    env["GCC_COLORS"] = ""
    env["CMAKE_COLOR_DIAGNOSTICS"] = "OFF"
    if target.startswith("macos"):
        for key in ("CC", "CXX", "LD", "AR", "SDKROOT", "DEVELOPER_DIR", "NIX_LDFLAGS", "NIX_CFLAGS_COMPILE", "NIX_CC", "CPATH", "LIBRARY_PATH"):
            env.pop(key, None)
        env["CC"] = run(["/usr/bin/xcrun", "--find", "clang"], env=env).strip()
        env["CXX"] = run(["/usr/bin/xcrun", "--find", "clang++"], env=env).strip()
        env["SDKROOT"] = run(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"], env=env).strip()
        env["MACOSX_DEPLOYMENT_TARGET"] = "13.0"
    elif target.startswith("linux"):
        env["CC"], env["CXX"] = "gcc", "g++"
    else:
        # Do not export these to GITHUB_PATH: w64devkit's bash.exe is BusyBox
        # and cannot run Actions' Git Bash --noprofile/--norc command template.
        env["PATH"] = os.pathsep.join(map(str, windows_tool_paths())) + os.pathsep + env.get("PATH", "")
        # qb's exists() splits PATH on ':' and tests exact filenames: neither
        # Windows' semicolon PATH nor implicit .exe lookup works there. Supply
        # each required private tool explicitly, including the resource compiler.
        for key, name in (("CC", "gcc.exe"), ("CXX", "g++.exe"),
                          ("WINDRES", "windres.exe"), ("PKG_CONF_PATH", "pkg-config.exe")):
            executable = windows_tool_paths()[0] / name
            if not executable.is_file():
                raise ValueError(f"Missing private Windows tool: {executable}; run windows-tools first")
            env[key] = executable.as_posix()
        # Avoid invoking Visual Studio/Cygwin tools discovered on the runner.
        env["CMAKE_GENERATOR"] = "MinGW Makefiles"
    return env


def configure_flags(target):
    flags = list(CONFIGURE)
    if target.startswith("macos"):
        flags += ["--disable-metal"]
    elif target.startswith("linux"):
        # SDL3 loads the host PulseAudio client dynamically; RetroArch's direct
        # backend otherwise makes libpulse mandatory even for --features.
        flags += ["--disable-pulse"]
    return flags


def configure_frontend(source, flags, env, log):
    failed = True
    try:
        result = run(["sh", "./configure", *flags], cwd=source, env=env,
                     log=log / "retroarch-configure.log")
        failed = False
        return result
    finally:
        # Upstream writes most dependency/compiler diagnostics separately.
        # Preserve those even when configure fails; logging cannot replace it.
        try:
            details = source / "config.log"
            if details.is_file():
                destination = log / "retroarch-configure-details.log"
                shutil.copy2(details, destination)
                if failed:
                    print_log_tail(destination)
        except OSError as error:
            print(f"Could not retain configure details: {error}", file=sys.stderr)


def build(target, version):
    native_target(target)
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid packaging version")
    output = ROOT / "build" / target
    if output.exists():
        raise ValueError(f"Existing build preserved: {output}. Move it aside before rebuilding.")
    output.parent.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target}-", dir=output.parent))
    log = CACHE / "logs" / target
    source_root = stage / "sources"
    for name, spec in LOCK["sources"].items():
        extract(download(spec), source_root / name)
    source = source_root / "retroarch" / LOCK["sources"]["retroarch"]["directory"]
    sdl = source_root / "sdl" / LOCK["sources"]["sdl"]["directory"]
    prefix = stage / "sdk"
    env = environment(target)
    jobs = str(int(os.getenv("BUILD_JOBS", str(min(os.cpu_count() or 2, 4)))))
    cmake = ["cmake", "-S", sdl, "-B", stage / "sdl-build", f"-DCMAKE_INSTALL_PREFIX={prefix}",
             "-DCMAKE_INSTALL_LIBDIR=lib", "-DCMAKE_BUILD_TYPE=Release", "-DSDL_SHARED=ON",
             "-DSDL_STATIC=OFF", "-DSDL_TESTS=OFF", "-DSDL_TEST_LIBRARY=OFF", "-DSDL_EXAMPLES=OFF"]
    if target.startswith("macos"):
        cmake += ["-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0", f"-DCMAKE_OSX_ARCHITECTURES={TARGETS[target]['arch']}"]
    if target.startswith("windows"):
        cmake += ["-G", "MinGW Makefiles"]
    else:
        cmake += ["-G", "Ninja"]
    run(cmake, env=env, log=log / "sdl-configure.log")
    run(["cmake", "--build", stage / "sdl-build", "--parallel", jobs], env=env, log=log / "sdl-build.log")
    run(["cmake", "--install", stage / "sdl-build"], env=env, log=log / "sdl-install.log")
    for suffix in ("", "_FOR_BUILD", "_FOR_TARGET"):
        key = "PKG_CONFIG_PATH" + suffix
        env[key] = str(prefix / "lib/pkgconfig") + os.pathsep + env.get(key, "")
    # A Devbox shell may have already accumulated Nix's role-specific paths.
    # Recompute those wrapper flags for this child's newly installed SDL SDK.
    for key in list(env):
        if key.startswith("NIX_PKG_CONFIG_WRAPPER_FLAGS_SET_"):
            env.pop(key)
    if not target.startswith("windows"):
        env["PKG_CONF_PATH"] = shutil.which("pkg-config", path=env["PATH"]) or "pkg-config"
    cflags = run(["pkg-config", "--cflags", "sdl3"], env=env).strip()
    libs = run(["pkg-config", "--libs", "sdl3"], env=env).strip()
    flags = configure_flags(target)
    if target.startswith("macos"):
        env["LDFLAGS"] = "-framework QuartzCore"
    elif target.startswith("linux"):
        env["LDFLAGS"] = "-static-libgcc -static-libstdc++"
    else:
        env["LDFLAGS"] = "-static-libgcc -static-libstdc++"
    configure_frontend(source, flags, env, log)
    config = (source / "config.h").read_text()
    for feature in REQUIRED:
        if not re.search(rf"^#define HAVE_{feature} 1$", config, re.M):
            raise ValueError(f"Required frontend capability missing: {feature}")
    if re.search(r"^#define HAVE_SDL2 1$", config, re.M):
        raise ValueError("SDL2 must not replace the tested SDL3 backend")
    # Override parent-repository discovery without changing upstream sources.
    make_args = ["make", f"-j{jobs}", f"SDL3_CFLAGS={cflags}", f"SDL3_LIBS={libs}",
                 f"GIT_VERSION={LOCK['sources']['retroarch']['revision'][:8]}"]
    if target.startswith("macos"):
        make_args += ["MINVERFLAGS=-mmacosx-version-min=13.0", f"ARCH={TARGETS[target]['arch']}"]
    run(make_args, cwd=source, env=env, log=log / "retroarch-build.log")
    bundle = stage / "bundle"
    if target.startswith("macos"):
        app = bundle / "RetroArch.app/Contents"
        binary = app / "MacOS/RetroArch"
        library = app / "Frameworks/libSDL3.0.dylib"
        binary.parent.mkdir(parents=True)
        library.parent.mkdir(parents=True)
        shutil.copy2(source / "retroarch", binary)
        shutil.copy2((prefix / "lib/libSDL3.0.dylib").resolve(), library)
        old_id = run(["/usr/bin/otool", "-D", library]).splitlines()[1].strip()
        run(["/usr/bin/install_name_tool", "-id", "@rpath/libSDL3.0.dylib", library])
        run(["/usr/bin/install_name_tool", "-change", old_id, "@executable_path/../Frameworks/libSDL3.0.dylib", binary])
        commands = run(["/usr/bin/otool", "-l", binary])
        for path in re.findall(r"cmd LC_RPATH\n\s+cmdsize \d+\n\s+path (.+?) \(offset", commands):
            if path.startswith("/"):
                run(["/usr/bin/install_name_tool", "-delete_rpath", path, binary])
        import plistlib
        with (app / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleExecutable": "RetroArch", "CFBundleIdentifier": "org.libretro.RetroArch.broomm",
                           "CFBundleName": "RetroArch", "CFBundlePackageType": "APPL",
                           "CFBundleShortVersionString": LOCK["upstream_version"], "CFBundleVersion": LOCK["upstream_version"],
                           "LSMinimumSystemVersion": "13.0", "CFBundleIconFile": "retroarch.icns",
                           "NSHighResolutionCapable": True}, stream)
        resources = app / "Resources"
        resources.mkdir()
        shutil.copy2(source / "media/retroarch.icns", resources / "retroarch.icns")
        license_dir = resources / "licenses"
    else:
        root = bundle / "RetroArch"
        root.mkdir(parents=True)
        binary = root / ("retroarch.exe" if target.startswith("windows") else "retroarch")
        shutil.copy2(source / ("retroarch.exe" if target.startswith("windows") else "retroarch"), binary)
        if target.startswith("windows"):
            library = root / "SDL3.dll"
            shutil.copy2(prefix / "bin/SDL3.dll", library)
        else:
            library = root / "lib/libSDL3.so.0"
            library.parent.mkdir()
            shutil.copy2((prefix / "lib/libSDL3.so.0").resolve(), library)
            loader = "/lib64/ld-linux-x86-64.so.2" if target.endswith("x64") else "/lib/ld-linux-aarch64.so.1"
            run(["patchelf", "--set-interpreter", loader, "--set-rpath", "$ORIGIN/lib", binary])
            run(["patchelf", "--set-rpath", "$ORIGIN", library])
        license_dir = root / "licenses"
    license_dir.mkdir(parents=True)
    for notice in source.rglob("*"):
        if notice.is_file() and re.search(r"(?:^|[-_.])(copying|licen[cs]es?|copyright|notices?)(?:$|[-_.])", notice.name, re.I):
            destination = license_dir / "RetroArch" / notice.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(notice, destination)
    shutil.copy2(sdl / "LICENSE.txt", license_dir / "SDL3-LICENSE.txt")
    shutil.copy2(ROOT / "NOTICE.md", license_dir / "Broomm-NOTICE.md")
    if not target.startswith("macos"):
        shutil.copy2(ROOT / "notices/GCC-Runtime-Library-Exception.txt", license_dir / "GCC-Runtime-Library-Exception.txt")
    if target.startswith("windows"):
        shutil.copy2(CACHE / "windows-tools/w64devkit/COPYING.MinGW-w64-runtime.txt", license_dir / "COPYING.MinGW-w64-runtime.txt")
    if target.startswith("macos"):
        run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", library])
        run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", binary.parent.parent.parent])
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", binary.parent.parent.parent])
    record = {"schema_version": 1, "target": target, "version": version,
              "upstream_version": LOCK["upstream_version"], "sources": LOCK["sources"],
              "recipe_sha": os.getenv("GITHUB_SHA", "local"), "configure": flags,
              **runtime_paths(bundle, binary, library),
              "requirements": TARGETS[target], "toolchain": {c: run([c, "--version"], env=env).splitlines()[0] for c in ("cmake", "make", "pkg-config")},
              "compiler": run([env["CC"], "--version"], env=env).splitlines()[0]}
    write(stage / "build.json", record)
    stage.rename(output)
    print(f"Built {target}: {output}; logs: {log}")


def runtime_paths(bundle, binary, library):
    # JSON references and ZIP inventory keys must agree across producer hosts
    # and the Linux publisher, independently of the native path separator.
    paths = {"binary": binary.relative_to(bundle).as_posix(),
             "sdl": library.relative_to(bundle).as_posix()}
    validate_runtime_paths(paths)
    return paths


def validate_runtime_paths(record, files=None):
    for key in ("binary", "sdl"):
        value = record.get(key)
        if not isinstance(value, str) or safe_name(value).as_posix() != value:
            raise ValueError(f"Runtime {key} must name a portable bundle path")
        if files is not None and value not in files:
            raise ValueError(f"Runtime {key} is not present in the bundle inventory: {value}")


def build_data(target):
    base = ROOT / "build" / target
    record = read(base / "build.json")
    if record["target"] != target:
        raise ValueError("Mismatched build identity")
    validate_runtime_paths(record)
    return base, record


def test_sdl(target):
    native_target(target)
    base, record = build_data(target)
    env = environment(target)
    output = base / ("sdl-probe.exe" if target.startswith("windows") else "sdl-probe")
    library = base / "bundle" / record["sdl"]
    headers = base / "sdk/include"
    args = [env["CC"], f"-I{headers}", ROOT / "tests/sdl_probe.c", str(library), "-o", output]
    if target.startswith("windows"):
        args[args.index(str(library))] = str(base / "sdk/lib/libSDL3.dll.a")
        shutil.copy2(library, output.parent / "SDL3.dll")
    elif target.startswith("linux"):
        args += ["-Wl,-rpath," + str(library.parent)]
    elif target.startswith("macos"):
        args += ["-Wl,-rpath," + str(library.parent)]
    run(args, env=env, log=CACHE / f"logs/{target}/probe-build.log", timeout=120)
    run([output], env=env, log=CACHE / f"logs/{target}/probe-run.log", timeout=30)
    write(base / "sdl-test.json", {"passed": True, "library_sha256": digest(library), "cases": ["SDL 3.4 ABI", "virtual gamepad discovery", "button route and neutral release", "SDL3 dummy audio stream"]})


def validate_linux_dependencies(dynamic, loaded):
    missing = re.findall(r"^\s*(\S+)\s+=>\s+not found\s*$", loaded, re.M)
    if missing:
        raise ValueError("Packaged ELF has unresolved runtime libraries: " + ", ".join(missing))
    if "/nix/store/" in loaded:
        raise ValueError("Packaged ELF resolves a build SDK runtime; see runtime-dependencies.log")
    if re.search(r"\(NEEDED\).*\[libpulse[^\]]*\]", dynamic):
        raise ValueError("Linux PulseAudio must use SDL3 dynamic loading, not a mandatory frontend dependency")


def linux_test_environment(binary, private, env, log):
    """Provision a headless runner's missing EGL loader for this probe only."""
    loaded = run(["/usr/bin/ldd", binary], env=env)
    (log / "runtime-dependencies-before-fixture.log").write_text(loaded)
    missing = re.findall(r"^\s*(\S+)\s+=>\s+not found\s*$", loaded, re.M)
    if not missing:
        validate_linux_dependencies("", loaded)
        return env
    if missing != ["libEGL.so.1"]:
        validate_linux_dependencies("", loaded)
    # The approved locked SDK already supplies libglvnd. Avoid installing OS
    # packages, disabling Wayland/EGL, or exposing the whole SDK at runtime.
    sdk = run(["pkg-config", "--variable=libdir", "egl"], env=env).strip()
    if not PurePosixPath(sdk).is_absolute() or not sdk.startswith("/nix/store/"):
        raise ValueError("EGL test fixture must come from the locked Devbox SDK")
    source = (Path(sdk) / "libEGL.so.1").resolve(strict=True)
    directory = Path(tempfile.mkdtemp(prefix="egl-runtime-", dir=private))
    fixture = directory / "libEGL.so.1"
    shutil.copy2(source, fixture)
    fixture.chmod(0o755)
    run(["patchelf", "--remove-rpath", fixture], env=env)
    child_env = dict(env, LD_LIBRARY_PATH=str(directory))
    dynamic = run(["readelf", "-d", fixture], env=child_env)
    dependencies = run(["/usr/bin/ldd", fixture], env=child_env)
    (log / "egl-fixture-dependencies.log").write_text(dynamic + "\n" + dependencies)
    if re.search(r"\((?:RUNPATH|RPATH)\)", dynamic):
        raise ValueError("EGL test fixture retains a build SDK search path")
    validate_linux_dependencies(dynamic, dependencies)
    write(log / "runtime-fixture.json", {
        "scope": "headless frontend probe only; not packaged",
        "source": str(source), "source_sha256": digest(source),
        "fixture": str(fixture), "fixture_sha256": digest(fixture),
    })
    print(f"Headless probe EGL loader: {fixture}; not part of the runtime package", flush=True)
    return child_env


def test_frontend(target):
    native_target(target)
    base, record = build_data(target)
    bundle = base / "bundle"
    binary = bundle / record["binary"]
    env = environment(target)
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES"):
        env.pop(key, None)
    private = base / "test-profile"
    private.mkdir(exist_ok=True)
    for key in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        env[key] = str(private)
    env.update({"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"})
    (CACHE / f"logs/{target}").mkdir(parents=True, exist_ok=True)
    if target.startswith("linux"):
        env = linux_test_environment(binary, private, env, CACHE / f"logs/{target}")
    library = bundle / record["sdl"]
    dependency_logs = []
    for runtime in (binary, library):
        args = (["/usr/bin/otool", "-L", runtime] if target.startswith("macos") else
                ["objdump", "-p", runtime] if target.startswith("windows") else ["readelf", "-d", runtime])
        output = run(args, env=env)
        # otool's first line names the inspected file, not a linked dependency.
        dependencies = "\n".join(output.splitlines()[1:]) if target.startswith("macos") else output
        dependency_logs.append(output)
        if "/nix/store/" in dependencies or "/Users/" in dependencies or "/opt/homebrew/" in dependencies:
            raise ValueError("Nonrelocatable packaged runtime dependency")
        if target.startswith("macos"):
            arch = run(["/usr/bin/lipo", "-archs", runtime], env=env).strip()
            if arch != TARGETS[target]["arch"]:
                raise ValueError("Packaged Mach-O architecture differs from target")
            commands = run(["/usr/bin/otool", "-l", runtime], env=env)
            paths = re.findall(r"cmd LC_RPATH\n\s+cmdsize \d+\n\s+path (.+?) \(offset", commands)
            if any(p.startswith("/") for p in paths):
                raise ValueError("Packaged Mach-O retains an absolute SDK search path")
            minimum = re.search(r"\n\s+minos (\S+)", commands)
            if minimum is None or tuple(map(int, minimum[1].split(".")[:2])) != (13, 0):
                raise ValueError("Packaged Mach-O minimum OS differs from the declared macOS 13.0")
        elif target.startswith("linux"):
            loader = run(["/usr/bin/ldd", runtime], env=env)
            dependency_logs.append(loader)
            # Keep all loader diagnostics before attempting native startup.
            (CACHE / f"logs/{target}/runtime-dependencies.log").parent.mkdir(parents=True, exist_ok=True)
            (CACHE / f"logs/{target}/runtime-dependencies.log").write_text("\n".join(dependency_logs))
            validate_linux_dependencies(dependencies, loader)
            expected = "X86-64" if target.endswith("x64") else "AArch64"
            if expected not in run(["readelf", "-h", runtime], env=env):
                raise ValueError("Packaged ELF architecture differs from target")
            symbols = run(["readelf", "--version-info", runtime], env=env)
            versions = [tuple(map(int, v.split("."))) for v in re.findall(r"GLIBC_(\d+\.\d+)", symbols)]
            if versions and max(versions) > (2, 39):
                raise ValueError("Packaged ELF exceeds the glibc 2.39 baseline")
        elif "pei-x86-64" not in run(["objdump", "-f", runtime], env=env):
            raise ValueError("Packaged PE architecture differs from target")
        if target.startswith("windows"):
            for dependency in re.findall(r"DLL Name:\s*(\S+)", dependencies):
                if re.match(r"lib(gcc|stdc\+\+|winpthread|ssp)", dependency, re.I):
                    raise ValueError("Packaged PE depends on an unbundled compiler runtime")
    (CACHE / f"logs/{target}/runtime-dependencies.log").write_text("\n".join(dependency_logs))
    if target.startswith("macos"):
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", bundle / "RetroArch.app"])
    features = run([binary, "--features"], cwd=private, env=env, log=CACHE / f"logs/{target}/features.log", timeout=30)
    if not re.search(r"SDL3[^\n]*yes", features, re.I):
        raise ValueError("Packaged frontend does not report SDL3 support")
    # A tiny original no-ROM core verifies the actual UDP control path.
    source = base / "sources/retroarch" / LOCK["sources"]["retroarch"]["directory"]
    core = private / ("probe.dll" if target.startswith("windows") else "probe.dylib" if target.startswith("macos") else "probe.so")
    compiler = [env["CC"], "-shared", "-fPIC", f"-I{source / 'libretro-common/include'}", ROOT / "tests/control_core.c", "-o", core]
    run(compiler, env=env, log=CACHE / f"logs/{target}/control-core-build.log", timeout=120)
    config = private / "retroarch.cfg"
    log = CACHE / f"logs/{target}/control-run.log"
    protocol = []
    with log.open("w") as output, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(.25)
        port = prepare_control_client(client)
        config.write_text(test_configuration(private, port))
        # GET_STATUS describes contentless cores differently. Supply original
        # inert test bytes so pause readback follows the same path as a game.
        content = private / "probe.bin"
        content.write_bytes(b"Broomm native command probe\n")
        # The macOS frontend prints to buffered stdout. Its explicit log file
        # flushes each line, retaining the last phase if the process stalls.
        process = subprocess.Popen([str(binary), "--verbose", "--log-file", str(log.parent / "frontend.log"),
                                    "--config", str(config), "-L", str(core), str(content)],
                                   cwd=private, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            query_control(client, process, port, "GET_STATUS", "PLAYING", protocol)
            client.sendto(b"PAUSE_TOGGLE", ("127.0.0.1", port))
            query_control(client, process, port, "GET_STATUS", "PAUSED", protocol)
            client.sendto(b"QUIT", ("127.0.0.1", port))
            if process.wait(timeout=20) != 0:
                raise ValueError("Frontend failed normal command shutdown")
        except Exception:
            output.flush()
            frontend_diagnostics(process, log.parent, target, protocol)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    for path in re.findall(r'(?:Redirecting save (?:file|state) to|Written to file:|Loading (?:favorites|history) file:) "([^"]+)"', log.read_text()):
        if not Path(path).resolve().is_relative_to(private.resolve()):
            raise ValueError("Native probe escaped its private configuration; see control-run.log")
    write(CACHE / f"logs/{target}/control-protocol.json", protocol)
    write(base / "frontend-test.json", {"passed": True, "binary_sha256": digest(binary), "cases": ["relocated runtime dependencies", "SDL3 feature readback", "original synthetic core/content", "UDP playing/pause acknowledgment", "normal QUIT", "explicit private writable paths"]})


def prepare_control_client(client):
    # Bind before reserving the server's port. Both endpoints stay on loopback,
    # with distinct ports even if the child has not initialized its socket yet.
    client.bind(("127.0.0.1", 0))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def query_control(client, process, port, command, expected, protocol):
    deadline = time.monotonic() + 20
    event = {"command": command, "expected": expected, "client": client.getsockname(),
             "server": ["127.0.0.1", port], "attempts": 0, "replies": [], "port_unreachable": 0}
    protocol.append(event)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError("Frontend exited before command acknowledgment; see control-protocol.json and frontend.log")
        event["attempts"] += 1
        try:
            client.sendto(command.encode(), ("127.0.0.1", port))
            data, peer = client.recvfrom(4096)
            reply = data.decode("utf-8", errors="replace")
            if len(event["replies"]) < 80:
                event["replies"].append({"peer": peer, "reply": reply[:512]})
            if peer == ("127.0.0.1", port) and reply.startswith(command + " ") and expected in reply:
                event["reply"] = reply.strip()
                return reply
        except socket.timeout:
            pass
        except ConnectionResetError as error:
            # Winsock reports ICMP Port Unreachable for a UDP packet sent
            # before the child binds its command socket. Keep waiting for a
            # real acknowledgment, with the same deadline and child checks.
            if sys.platform != "win32" or getattr(error, "winerror", error.errno) != 10054:
                raise
            event["port_unreachable"] += 1
            # An immediate ICMP error must not turn the startup wait into a
            # busy retransmit loop. Match the existing receive poll interval.
            time.sleep(.25)
    raise ValueError(f"Native command timed out: {command}; {len(event['replies'])} replies recorded in control-protocol.json")


def frontend_diagnostics(process, directory, target, protocol=None):
    """Read only the probe's own child, before its supervisor terminates it."""
    try:
        if protocol is not None:
            write(directory / "control-protocol.json", protocol)
        result = process.poll()
        write(directory / "failed-process.json", {"pid": process.pid, "returncode": result, "target": target})
        for filename in ("frontend.log", "control-run.log"):
            if (directory / filename).is_file():
                print_log_tail(directory / filename)
        if result is None and target.startswith("macos"):
            try:
                run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(process.pid), "-iUDP"],
                    log=directory / "frontend-sockets.log", timeout=5)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                print(f"Could not inspect frontend UDP sockets: {error}", file=sys.stderr)
            run(["/usr/bin/sample", str(process.pid), "1", "1", "-file", directory / "frontend-sample.txt"],
                log=directory / "frontend-sample.log", timeout=10)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        # Diagnostic failure cannot replace the original native test failure.
        print(f"Could not complete frontend diagnostics: {error}", file=sys.stderr)


def test_configuration(private, port):
    values = {
        "video_driver": "null", "audio_driver": "null", "audio_enable": "false",
        "input_driver": "null", "input_joypad_driver": "sdl3", "pause_nonactive": "false",
        "network_cmd_enable": "true", "network_cmd_bind_address": "127.0.0.1", "network_cmd_port": str(port),
        "config_save_on_exit": "false", "savestate_auto_save": "false", "confirm_quit": "false",
        "history_list_enable": "false", "content_runtime_log": "false", "content_runtime_log_aggregate": "false",
    }
    # Cocoa resolves Documents through its directory API, independently of HOME.
    # Explicit paths also protect local manual checks on Windows and Linux.
    for key in ("savefile_directory", "savestate_directory", "system_directory", "screenshot_directory",
                "cache_directory", "rgui_config_directory", "input_remapping_directory", "playlist_directory",
                "runtime_log_directory", "log_dir", "recording_output_directory", "recording_config_directory",
                "content_history_directory", "content_image_history_directory", "content_music_history_directory",
                "content_video_directory", "content_favorites_directory", "assets_directory", "joypad_autoconfig_dir"):
        directory = private / key
        directory.mkdir(exist_ok=True)
        values[key] = directory.as_posix()
    values["core_options_path"] = (private / "core-options.cfg").as_posix()
    return "".join(f'{key} = {json.dumps(value)}\n' for key, value in values.items())


def inventory(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise ValueError("Release bundles must contain regular files, not links")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {"sha256": digest(path), "bytes": path.stat().st_size,
                                                       "mode": path.stat().st_mode & 0o777}
    return result


def package(target):
    base, record = build_data(target)
    bundle = base / "bundle"
    for test, file in (("sdl", record["sdl"]), ("frontend", record["binary"])):
        result = read(base / f"{test}-test.json")
        if not result["passed"] or result["library_sha256" if test == "sdl" else "binary_sha256"] != digest(bundle / file):
            raise ValueError("Native checks are missing or refer to different binaries")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    name = f"RetroArch-{LOCK['upstream_version']}-broomm-{record['version']}-{target}"
    archive = dist / f"{name}.zip"
    if archive.exists():
        raise ValueError("Existing release archive preserved")
    files = inventory(bundle)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for relative, info in files.items():
            item = zipfile.ZipInfo(relative, (2026, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = (0o100000 | info["mode"]) << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(item, (bundle / relative).read_bytes())
    manifest = {**record, "files": files, "checks": {t: read(base / f"{t}-test.json") for t in ("sdl", "frontend")},
                "archive": {"file": archive.name, "sha256": digest(archive), "bytes": archive.stat().st_size}}
    write(dist / f"{name}.json", manifest)
    (dist / f"{name}.sha256").write_text(f"{manifest['archive']['sha256']}  {archive.name}\n")
    verify_manifest(dist / f"{name}.json")
    print(f"Packaged and verified: {archive}")


def verify_manifest(path):
    manifest = read(path)
    target = manifest["target"]
    if (target not in TARGETS or not VERSION.fullmatch(manifest["version"])
            or manifest["sources"] != LOCK["sources"] or manifest["requirements"] != TARGETS[target]
            or manifest["upstream_version"] != LOCK["upstream_version"]):
        raise ValueError("Release identity/source pins differ")
    validate_runtime_paths(manifest, manifest["files"])
    archive_info = manifest["archive"]
    name = f"RetroArch-{LOCK['upstream_version']}-broomm-{manifest['version']}-{target}.zip"
    if archive_info["file"] != name:
        raise ValueError("Unexpected release archive name")
    archive = Path(path).parent / name
    if archive.stat().st_size != archive_info["bytes"] or digest(archive) != archive_info["sha256"]:
        raise ValueError("Release archive changed")
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len(names) != len({n.casefold() for n in names}) or set(names) != set(manifest["files"]):
            raise ValueError("Release archive inventory differs")
        for item in source.infolist():
            safe_name(item.filename)
            data = source.read(item)
            info = manifest["files"][item.filename]
            if (hashlib.sha256(data).hexdigest() != info["sha256"] or len(data) != info["bytes"]
                    or (item.external_attr >> 16) & 0o777 != info["mode"]
                    or (item.external_attr >> 16) & 0o170000 != 0o100000):
                raise ValueError("Release file checksum differs")
    for check, field, file in (("sdl", "library_sha256", manifest["sdl"]),
                               ("frontend", "binary_sha256", manifest["binary"])):
        result = manifest["checks"][check]
        if result["passed"] is not True or result[field] != manifest["files"][file]["sha256"]:
            raise ValueError("Release native checks are missing or mismatched")
    checksum = archive.with_suffix(".sha256")
    if checksum.read_text().strip() != f"{archive_info['sha256']}  {archive.name}":
        raise ValueError("Published checksum differs")
    return manifest


def source_packages():
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    for name, spec in LOCK["sources"].items():
        source = download(spec)
        target = dist / f"source-{name}-{spec['revision']}.tar.gz"
        shutil.copy2(source, target)
    recipes = dist / "source-broomm-retroarch-recipes.tar.gz"
    if Path(run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT).strip()).resolve() != ROOT:
        raise ValueError("Corresponding recipes require this project's own Git checkout")
    run(["git", "archive", "--format=tar.gz", f"--output={recipes}", "HEAD"], cwd=ROOT)
    print("Corresponding upstream sources and exact packaging recipes prepared")


def verify_release(version):
    manifests = list((ROOT / "dist").glob("RetroArch-*.json"))
    checked = [verify_manifest(path) for path in manifests]
    if len(checked) != len(TARGETS) or {m["target"] for m in checked} != set(TARGETS) or any(m["version"] != version for m in checked):
        raise ValueError("Release requires all five native desktop packages at the same version")
    sha = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid packaging commit identity")
    if any(m["recipe_sha"] != sha for m in checked):
        raise ValueError("Artifacts belong to a different packaging commit")
    for name, spec in LOCK["sources"].items():
        if digest(ROOT / "dist" / f"source-{name}-{spec['revision']}.tar.gz") != spec["sha256"]:
            raise ValueError("Corresponding source archive changed")
    if not (ROOT / "dist/source-broomm-retroarch-recipes.tar.gz").is_file():
        raise ValueError("Corresponding build recipes are missing")
    expected_recipes = CACHE / "release-recipes-check.tar.gz"
    expected_recipes.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "archive", "--format=tar.gz", f"--output={expected_recipes}", sha], cwd=ROOT)
    if digest(expected_recipes) != digest(ROOT / "dist/source-broomm-retroarch-recipes.tar.gz"):
        raise ValueError("Corresponding build recipes do not match the packaging commit")
    expected = {"source-broomm-retroarch-recipes.tar.gz"}
    expected.update(f"source-{name}-{spec['revision']}.tar.gz" for name, spec in LOCK["sources"].items())
    for manifest in checked:
        expected.update(str(Path(manifest["archive"]["file"]).with_suffix(suffix)) for suffix in (".zip", ".json", ".sha256"))
    if {p.name for p in (ROOT / "dist").iterdir()} != expected:
        raise ValueError("Release contains unexpected or missing assets")
    return checked


def github_request(endpoint, *, payload=None):
    """Only an explicit 404 is absence; authentication/network errors stay errors."""
    args = ["gh", "api", "--include", endpoint]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    lines = re.split(r"\r?\n\r?\n", result.stdout, maxsplit=1)
    statuses = re.findall(r"^HTTP/\S+\s+(\d+)", lines[0], re.M)
    if not statuses:
        raise RuntimeError("GitHub request failed without an HTTP response; no release mutation attempted")
    status = int(statuses[-1])
    if status == 404 and payload is None:
        return None
    if result.returncode or not 200 <= status < 300 or len(lines) != 2:
        raise RuntimeError(f"GitHub request failed (HTTP {status}); existing state preserved")
    return json.loads(lines[1])


def tag_commit(repo, tag):
    result = github_request(f"repos/{repo}/git/ref/tags/{tag}")
    if result is None:
        return None
    obj = result["object"]
    for _ in range(4):
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag" or not re.fullmatch(r"[0-9a-f]{40}", obj["sha"]):
            break
        obj = github_request(f"repos/{repo}/git/tags/{obj['sha']}")["object"]
    raise ValueError("Release tag has an unsupported target; preserved")


def find_release(repo, tag):
    # Listing with the publish token also sees drafts that a by-tag lookup
    # may not expose. Never infer absence from an inaccessible repository.
    for page in range(1, 101):
        releases = github_request(f"repos/{repo}/releases?per_page=100&page={page}")
        if not isinstance(releases, list):
            raise ValueError("Could not enumerate releases/drafts; existing state preserved")
        for release in releases:
            if release["tag_name"] == tag:
                return release
        if len(releases) < 100:
            return None
    raise ValueError("Release enumeration exceeded its bound; absence is unconfirmed")


def publish(version):
    checked = verify_release(version)
    tag = f"v{version}"
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Invalid publishing identity")
    assets = sorted(p for p in (ROOT / "dist").iterdir() if p.is_file())
    notes = CACHE / "release-notes.md"
    notes.parent.mkdir(exist_ok=True)
    notes.write_text(f"RetroArch {LOCK['upstream_version']} / SDL 3.4.0 for Broomm.\n\n"
                     "SDL3, OpenGL Core, Slang and native network commands are verified in each package.\n"
                     "No cores, firmware, BIOS or games are included. These packages require explicit integration validation in Broomm.\n\n"
                     f"Packaging commit: {sha}\n\n"
                     + "\n".join(f"- {m['target']}: {m['archive']['file']}" for m in checked))
    # Never overwrite an earlier public release, tag or asset. An interrupted
    # upload remains a draft; retry only after checking its matching commit/hash.
    if find_release(repo, tag) is not None:
        raise ValueError("Release/draft already exists and was preserved. Review its assets before retrying; see README.")
    existing = tag_commit(repo, tag)
    if existing is None:
        github_request(f"repos/{repo}/git/refs", payload={"ref": f"refs/tags/{tag}", "sha": sha})
    elif existing != sha:
        raise ValueError("Release tag points to a different commit and was preserved")
    args = ["gh", "release", "create", tag, "--repo", repo, "--verify-tag", "--draft",
            "--title", f"Broomm RetroArch {version}", "--notes-file", str(notes), *map(str, assets)]
    run(args, timeout=1800)
    update = ["gh", "release", "edit", tag, "--repo", repo, "--draft=false"]
    if "-rc" in version:
        update += ["--prerelease"]
    if tag_commit(repo, tag) != sha:
        raise ValueError("Release tag changed; draft preserved without publishing")
    run(update, timeout=60)


def check():
    # Embedded Windows Python deliberately excludes cwd from sys.path.
    import unittest
    sys.path.insert(0, str(ROOT / "tool"))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "windows-tools", "check", "build", "test-sdl", "test-frontend", "package", "sources", "publish"])
    parser.add_argument("--target", choices=list(TARGETS))
    parser.add_argument("--version", default="0.1.0-local")
    args = parser.parse_args()
    if args.command in ("build", "test-sdl", "test-frontend", "package") and not args.target:
        parser.error("--target is required")
    commands = {"plan": plan, "windows-tools": windows_tools, "check": check, "build": lambda: build(args.target, args.version),
                "test-sdl": lambda: test_sdl(args.target), "test-frontend": lambda: test_frontend(args.target),
                "package": lambda: package(args.target), "sources": source_packages, "publish": lambda: publish(args.version)}
    commands[args.command]()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"Build/release failed: {error}", file=sys.stderr)
        sys.exit(1)

