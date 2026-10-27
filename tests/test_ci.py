"""Observable archive, release/version and publication failure boundaries."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
import socket
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import ci


class Commands(unittest.TestCase):
    def test_configure_failure_and_timeout_retain_upstream_details(self):
        errors = (RuntimeError("configure failed"), subprocess.TimeoutExpired(["sh"], 10))
        for error in errors:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as folder:
                source, log = Path(folder) / "source", Path(folder) / "logs"
                source.mkdir()
                log.mkdir()
                (source / "config.log").write_text("compiler diagnostic not printed by configure\n")
                with patch.object(ci, "run", side_effect=error), redirect_stderr(io.StringIO()) as output:
                    with self.assertRaises(type(error)) as result:
                        ci.configure_frontend(source, [], {}, log)
                self.assertIs(result.exception, error)
                self.assertEqual((log / "retroarch-configure-details.log").read_bytes(), (source / "config.log").read_bytes())
                self.assertIn("compiler diagnostic", output.getvalue())

    def test_unavailable_configure_details_never_replace_the_build_result(self):
        with tempfile.TemporaryDirectory() as folder:
            source, log = Path(folder) / "source", Path(folder) / "logs"
            source.mkdir()
            log.mkdir()
            with patch.object(ci, "run", return_value="configured"):
                self.assertEqual(ci.configure_frontend(source, [], {}, log), "configured")
            (source / "config.log").write_text("upstream details")
            error = RuntimeError("original configure failure")
            with patch.object(ci, "run", side_effect=error), patch.object(ci.shutil, "copy2", side_effect=OSError("log unavailable")), redirect_stderr(io.StringIO()) as output:
                with self.assertRaises(RuntimeError) as result:
                    ci.configure_frontend(source, [], {}, log)
            self.assertIs(result.exception, error)
            self.assertIn("log unavailable", output.getvalue())

    def test_failed_phase_surfaces_bounded_diagnostics_and_preserves_full_log(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "configure.log"
            captured = io.StringIO()
            payload = "early diagnostic\n" + "context\n" * 5000 + "\x1b[31mrequired dependency missing\x1b[0m\n"
            payload_file = Path(folder) / "diagnostic payload.txt"
            payload_file.write_bytes(payload.encode("utf-8"))
            args = [sys.executable, "-c",
                    "import pathlib, sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes()); sys.exit(7)",
                    str(payload_file)]
            # Enforce CreateProcessW's UTF-16 limit on every host, including NUL.
            self.assertLessEqual(len(subprocess.list2cmdline(args).encode("utf-16-le")) // 2 + 1, 32767)
            with redirect_stdout(io.StringIO()), redirect_stderr(captured):
                with self.assertRaisesRegex(RuntimeError, "Command failed \\(7\\)"):
                    ci.run(args, log=log, timeout=10)
            self.assertIn("required dependency missing", captured.getvalue())
            self.assertNotIn("early diagnostic", captured.getvalue())
            self.assertNotIn("\x1b", captured.getvalue())
            self.assertLess(len(captured.getvalue()), 18000)
            self.assertTrue(log.read_text().endswith(payload))

    def test_timed_out_phase_keeps_and_reports_its_last_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "build.log"
            captured = io.StringIO()
            def interrupted(args, **kwargs):
                kwargs["stdout"].write("compiler stopped responding\n")
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            with patch.object(ci.subprocess, "run", side_effect=interrupted), redirect_stdout(io.StringIO()), redirect_stderr(captured):
                with self.assertRaises(subprocess.TimeoutExpired):
                    ci.run(["compiler"], log=log, timeout=10)
            self.assertIn("compiler stopped responding", captured.getvalue())
            self.assertIn("compiler stopped responding", log.read_text())

    def test_successful_phase_returns_full_log_without_failure_output(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "success.log"
            captured = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(captured):
                output = ci.run([sys.executable, "-c", "print('completed')"], log=log, timeout=10)
            self.assertEqual(output, log.read_text())
            self.assertIn("completed", output)
            self.assertEqual(captured.getvalue(), "")


class WindowsTools(unittest.TestCase):
    def test_prepared_toolchain_does_not_shadow_the_runner_bash(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ci, "CACHE", Path(folder)), patch.object(ci.sys, "platform", "win32"):
            for directory in ci.windows_tool_paths():
                directory.mkdir(parents=True)
            (ci.windows_tool_paths()[0] / "gcc.exe").touch()
            (ci.windows_tool_paths()[0] / "bash.exe").touch()
            github_path = Path(folder) / "github-path"
            github_path.write_text("existing runner tools\n")
            with patch.dict(os.environ, {"GITHUB_PATH": str(github_path), "PATH": "runner-shell-path"}), patch.object(ci, "download", return_value=Path(folder) / "cached-toolchain"), patch.object(ci, "run") as execute, redirect_stdout(io.StringIO()):
                ci.windows_tools()
                self.assertEqual(os.environ["PATH"], "runner-shell-path")
                self.assertEqual(github_path.read_text(), "existing runner tools\n")
                execute.assert_not_called()

    def test_child_tool_lookup_uses_private_path_without_changing_the_parent(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ci, "CACHE", Path(folder)), patch.dict(os.environ, {"PATH": "runner-shell-path"}):
            tool_directory = ci.windows_tool_paths()[0]
            tool_directory.mkdir(parents=True)
            tools = {"CC": "gcc.exe", "CXX": "g++.exe", "WINDRES": "windres.exe", "PKG_CONF_PATH": "pkg-config.exe"}
            for name in tools.values():
                (tool_directory / name).touch()
            env = ci.environment("windows-x64")
            self.assertTrue(env["PATH"].startswith(str(ci.windows_tool_paths()[0]) + os.pathsep))
            self.assertEqual(os.environ["PATH"], "runner-shell-path")
            for key, name in tools.items():
                self.assertEqual(env[key], (tool_directory / name).as_posix())
                self.assertTrue(Path(env[key]).is_absolute())
                self.assertNotIn("\\", env[key])
            for tool in ("gcc", "cmake", "sh"):
                with self.subTest(tool=tool):
                    executable = str(ci.windows_tool_paths()[tool == "cmake"] / (tool + ".exe"))
                    with patch.object(ci.sys, "platform", "win32"), patch.object(ci.shutil, "which", return_value=executable) as lookup, patch.object(ci.subprocess, "check_output", return_value="private tool") as execute, redirect_stdout(io.StringIO()):
                        self.assertEqual(ci.run([tool, "literal argument with spaces"], env=env), "private tool")
                        lookup.assert_called_once_with(tool, path=env["PATH"])
                        self.assertEqual(execute.call_args.args[0], [executable, "literal argument with spaces"])
                        self.assertIs(execute.call_args.kwargs["env"], env)

    def test_missing_private_tool_never_falls_back_to_a_host_compiler(self):
        tools = ("gcc.exe", "g++.exe", "windres.exe", "pkg-config.exe")
        for missing in tools:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as folder, patch.object(ci, "CACHE", Path(folder)):
                directory = ci.windows_tool_paths()[0]
                directory.mkdir(parents=True)
                for tool in tools:
                    if tool != missing:
                        (directory / tool).touch()
                with patch.object(ci.shutil, "which", return_value="unrelated host tool") as lookup:
                    with self.assertRaisesRegex(ValueError, "Missing private Windows tool") as error:
                        ci.environment("windows-x64")
                self.assertIn(missing, str(error.exception))
                lookup.assert_not_called()


class FrontendDiagnostics(unittest.TestCase):
    def test_hung_native_child_is_sampled_before_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "frontend.log").write_text("last completed startup phase\n")
            child = Mock(pid=12345)
            child.poll.return_value = None
            output = io.StringIO()
            with patch.object(ci, "run") as execute, redirect_stderr(output):
                ci.frontend_diagnostics(child, directory, "macos-arm64")
            self.assertIn("last completed startup phase", output.getvalue())
            self.assertEqual(execute.call_args.args[0][:4], ["/usr/bin/sample", "12345", "1", "1"])
            self.assertEqual(execute.call_args_list[0].args[0], ["/usr/sbin/lsof", "-nP", "-a", "-p", "12345", "-iUDP"])
            self.assertEqual(ci.read(directory / "failed-process.json")["returncode"], None)
            child.terminate.assert_not_called()

    def test_exited_child_is_not_sampled_through_a_reused_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            child = Mock(pid=12345)
            child.poll.return_value = 7
            with patch.object(ci, "run") as execute:
                ci.frontend_diagnostics(child, directory, "macos-arm64")
            execute.assert_not_called()
            self.assertEqual(ci.read(directory / "failed-process.json")["returncode"], 7)

    def test_unavailable_sampler_does_not_replace_native_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            child = Mock(pid=12345)
            child.poll.return_value = None
            with patch.object(ci, "run", side_effect=OSError("sampler unavailable")), redirect_stderr(io.StringIO()) as output:
                ci.frontend_diagnostics(child, directory, "macos-arm64")
            self.assertIn("sampler unavailable", output.getvalue())


class LinuxDependencies(unittest.TestCase):
    def test_headless_egl_fixture_is_private_audited_and_does_not_modify_the_sdk(self):
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder)
            source = private / "locked-sdk-library"
            source.write_bytes(b"original SDK library")
            source_hash = ci.digest(source)
            binary = private / "retroarch"
            env = {"PATH": "locked tools"}
            calls = []
            def execute(args, **kwargs):
                calls.append((args, kwargs))
                if args == ["/usr/bin/ldd", binary]:
                    return "libEGL.so.1 => not found\nlibc.so.6 => /lib/libc.so.6\n"
                if args[0] == "pkg-config":
                    return "/nix/store/locked-libglvnd/lib\n"
                if args[0] == "patchelf":
                    Path(args[-1]).write_bytes(b"relocated library")
                    return ""
                if args[0] == "readelf":
                    return "(NEEDED) Shared library: [libGLdispatch.so.0]\n"
                return "libGLdispatch.so.0 => /lib/libGLdispatch.so.0\n"
            with patch.object(ci, "run", side_effect=execute), patch.object(Path, "resolve", return_value=source), redirect_stdout(io.StringIO()):
                child = ci.linux_test_environment(binary, private, env, private)
            self.assertEqual(env, {"PATH": "locked tools"})
            self.assertEqual(ci.digest(source), source_hash)
            fixture = Path(child["LD_LIBRARY_PATH"]) / "libEGL.so.1"
            self.assertTrue(fixture.is_relative_to(private))
            self.assertEqual(fixture.read_bytes(), b"relocated library")
            self.assertNotIn("/nix/store/", child["LD_LIBRARY_PATH"])
            self.assertEqual(ci.read(private / "runtime-fixture.json")["source_sha256"], source_hash)
            self.assertEqual(ci.read(private / "runtime-fixture.json")["fixture_sha256"], ci.digest(fixture))
            self.assertTrue(any(args[0] == "ldd" or args[0] == "/usr/bin/ldd" for args, _ in calls[2:]))

    def test_existing_host_egl_is_used_and_other_missing_libraries_still_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            private, env = Path(folder), {"PATH": "locked tools"}
            with patch.object(ci, "run", return_value="libEGL.so.1 => /lib/libEGL.so.1\n") as execute:
                self.assertIs(ci.linux_test_environment(private / "retroarch", private, env, private), env)
                self.assertEqual(execute.call_count, 1)
            for missing in ("libGL.so.1 => not found\n", "libEGL.so.1 => not found\nlibGL.so.1 => not found\n"):
                with self.subTest(missing=missing), patch.object(ci, "run", return_value=missing) as execute:
                    with self.assertRaisesRegex(ValueError, "unresolved runtime libraries"):
                        ci.linux_test_environment(private / "retroarch", private, env, private)
                    self.assertEqual(execute.call_count, 1)

    def test_egl_fixture_cannot_hide_sdk_runtime_leaks_or_unresolved_dependencies(self):
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder)
            source = private / "locked-sdk-library"
            source.write_bytes(b"fixture")
            for dynamic, dependencies in (("(RUNPATH) Library runpath: [/nix/store/sdk/lib]", ""),
                                           ("", "libc.so.6 => /nix/store/glibc/lib/libc.so.6"),
                                           ("", "libGLdispatch.so.0 => not found")):
                with self.subTest(dynamic=dynamic, dependencies=dependencies), patch.object(ci, "run", side_effect=["libEGL.so.1 => not found\n", "/nix/store/locked-libglvnd/lib\n", "", dynamic, dependencies]), patch.object(Path, "resolve", return_value=source):
                    with self.assertRaises(ValueError):
                        ci.linux_test_environment(private / "retroarch", private, {}, private)

    def test_pulse_audio_remains_available_through_sdl_without_a_hard_dependency(self):
        for target in ("linux-x64", "linux-arm64"):
            self.assertIn("--enable-sdl3", ci.configure_flags(target))
            self.assertIn("--disable-pulse", ci.configure_flags(target))
        for target in ("macos-arm64", "macos-x64", "windows-x64"):
            self.assertNotIn("--disable-pulse", ci.configure_flags(target))
        with self.assertRaisesRegex(ValueError, "SDL3 dynamic loading"):
            ci.validate_linux_dependencies("(NEEDED) Shared library: [libpulse.so.0]", "libpulse.so.0 => /lib/libpulse.so.0")

    def test_missing_libraries_and_sdk_leaks_are_actionable_failures(self):
        with self.assertRaises(ValueError) as error:
            ci.validate_linux_dependencies("", "libpulse.so.0 => not found\nlibXss.so.1 => not found\n")
        self.assertIn("libpulse.so.0", str(error.exception))
        self.assertIn("libXss.so.1", str(error.exception))
        with self.assertRaisesRegex(ValueError, "build SDK"):
            ci.validate_linux_dependencies("", "libc.so.6 => /nix/store/fixture-glibc/lib/libc.so.6")

    def test_host_and_bundle_libraries_are_accepted(self):
        ci.validate_linux_dependencies("(NEEDED) Shared library: [libSDL3.so.0]",
                                       "libSDL3.so.0 => /tmp/package/lib/libSDL3.so.0\nlibc.so.6 => /lib/libc.so.6\n")


class ControlProtocol(unittest.TestCase):
    def test_windows_port_unreachable_waits_for_an_actual_acknowledgment(self):
        error = ConnectionResetError(10054, "ICMP Port Unreachable")
        error.winerror = 10054
        for operation in ("send", "receive"):
            with self.subTest(operation=operation):
                client = Mock()
                client.getsockname.return_value = ("127.0.0.1", 12345)
                reply = (b"GET_STATUS PLAYING Broomm CI,probe", ("127.0.0.1", 23456))
                if operation == "send":
                    client.sendto.side_effect = [error, None]
                    client.recvfrom.return_value = reply
                else:
                    client.recvfrom.side_effect = [error, reply]
                child = Mock()
                child.poll.return_value = None
                protocol = []
                with patch.object(ci.sys, "platform", "win32"), patch.object(ci.time, "monotonic", side_effect=[0, 0, 1]), patch.object(ci.time, "sleep") as pause:
                    result = ci.query_control(client, child, 23456, "GET_STATUS", "PLAYING", protocol)
                self.assertEqual(result, reply[0].decode())
                self.assertEqual(protocol[0]["port_unreachable"], 1)
                self.assertEqual(protocol[0]["attempts"], 2)
                self.assertEqual(len(protocol[0]["replies"]), 1)
                pause.assert_called_once_with(.25)
                self.assertEqual(child.poll.call_count, 2)

    def test_unreachable_port_still_times_out_and_child_exit_is_not_retried(self):
        error = ConnectionResetError(10054, "ICMP Port Unreachable")
        error.winerror = 10054
        for exit_code in (None, 7):
            with self.subTest(exit_code=exit_code):
                client = Mock()
                client.recvfrom.side_effect = error
                child = Mock()
                child.poll.side_effect = [None, exit_code]
                protocol = []
                message = "timed out" if exit_code is None else "exited before command acknowledgment"
                times = [0, 0, 21] if exit_code is None else [0, 0, 1]
                with patch.object(ci.sys, "platform", "win32"), patch.object(ci.time, "monotonic", side_effect=times), patch.object(ci.time, "sleep"), self.assertRaisesRegex(ValueError, message):
                    ci.query_control(client, child, 23456, "GET_STATUS", "PLAYING", protocol)
                self.assertEqual(protocol[0]["port_unreachable"], 1)
                self.assertEqual(protocol[0]["attempts"], 1)
                self.assertNotIn("reply", protocol[0])

    def test_other_socket_errors_are_not_treated_as_emulator_startup(self):
        for host, code in (("win32", 10053), ("darwin", 10054)):
            with self.subTest(host=host, code=code):
                error = ConnectionResetError(code, "unrelated socket failure")
                error.winerror = code
                client = Mock()
                client.recvfrom.side_effect = error
                child = Mock()
                child.poll.return_value = None
                with patch.object(ci.sys, "platform", host), patch.object(ci.time, "sleep") as pause:
                    with self.assertRaises(ConnectionResetError) as result:
                        ci.query_control(client, child, 23456, "GET_STATUS", "PLAYING", [])
                self.assertIs(result.exception, error)
                pause.assert_not_called()

    def test_real_udp_listener_can_start_after_the_first_query(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            client.settimeout(.1)
            server.settimeout(2)
            port = ci.prepare_control_client(client)
            original_receive = client.recvfrom
            first = True
            def receive(size):
                nonlocal first
                if first:
                    first = False
                    try:
                        # No server is bound for this datagram: native Windows
                        # may report 10054; Unix normally reaches its timeout.
                        return original_receive(size)
                    finally:
                        server.bind(("127.0.0.1", port))
                data, peer = server.recvfrom(4096)
                self.assertEqual(peer, client.getsockname())
                server.sendto(data + b" PLAYING Broomm CI,probe", peer)
                return original_receive(size)
            proxy = Mock(wraps=client)
            proxy.recvfrom.side_effect = receive
            child = Mock()
            child.poll.return_value = None
            protocol = []
            result = ci.query_control(proxy, child, port, "GET_STATUS", "PLAYING", protocol)
            self.assertIn("PLAYING", result)
            self.assertEqual(protocol[0]["attempts"], 2)
            self.assertEqual(len(protocol[0]["replies"]), 1)

    def test_real_control_client_is_loopback_bound_and_separate_from_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            client.settimeout(2)
            server.settimeout(2)
            port = ci.prepare_control_client(client)
            self.assertEqual(client.getsockname()[0], "127.0.0.1")
            self.assertNotEqual(client.getsockname()[1], port)
            server.bind(("127.0.0.1", port))
            client.sendto(b"GET_STATUS", ("127.0.0.1", port))
            data, peer = server.recvfrom(4096)
            self.assertEqual(peer, client.getsockname())
            server.sendto(data + b" PLAYING", peer)
            self.assertEqual(client.recvfrom(4096), (b"GET_STATUS PLAYING", ("127.0.0.1", port)))

    def test_wrong_peer_and_unexpected_states_are_recorded_before_acknowledgment(self):
        client = Mock()
        client.getsockname.return_value = ("127.0.0.1", 12345)
        client.recvfrom.side_effect = [(b"GET_STATUS PLAYING", ("127.0.0.1", 9999)),
                                      (b"GET_STATUS CONTENTLESS", ("127.0.0.1", 23456)),
                                      (b"GET_STATUS PLAYING Broomm CI,probe", ("127.0.0.1", 23456))]
        child = Mock()
        child.poll.return_value = None
        protocol = []
        with patch.object(ci.time, "monotonic", side_effect=[0, 0, 1, 2]):
            reply = ci.query_control(client, child, 23456, "GET_STATUS", "PLAYING", protocol)
        self.assertEqual(reply, "GET_STATUS PLAYING Broomm CI,probe")
        self.assertEqual(protocol[0]["attempts"], 3)
        self.assertEqual(len(protocol[0]["replies"]), 3)

    def test_timeout_distinguishes_silence_from_an_unexpected_reply(self):
        for response in (socket.timeout(), (b"GET_STATUS CONTENTLESS", ("127.0.0.1", 23456))):
            with self.subTest(response=response):
                client = Mock()
                client.getsockname.return_value = ("127.0.0.1", 12345)
                if isinstance(response, Exception):
                    client.recvfrom.side_effect = response
                else:
                    client.recvfrom.return_value = response
                child = Mock()
                child.poll.return_value = None
                protocol = []
                with patch.object(ci.time, "monotonic", side_effect=[0, 0, 21]), self.assertRaisesRegex(ValueError, "timed out"):
                    ci.query_control(client, child, 23456, "GET_STATUS", "PLAYING", protocol)
                self.assertEqual(protocol[0]["attempts"], 1)
                self.assertEqual(len(protocol[0]["replies"]), 0 if isinstance(response, Exception) else 1)
                child.terminate.assert_not_called()


class Versions(unittest.TestCase):
    def test_release_train_promotes_candidate_and_then_increments(self):
        tags = ["v0.1.0", "v0.1.1-rc1", "v0.1.1-rc2", "v8.0.99", "garbage"]
        self.assertEqual(ci.next_version("0.1", "main", tags), "0.1.1")
        self.assertEqual(ci.next_version("0.1", "develop", tags), "0.1.1-rc3")
        self.assertEqual(ci.next_version("0.1", "develop", tags + ["v0.1.1"]), "0.1.2-rc1")

    def test_first_release_and_manual_minor_bump(self):
        self.assertEqual(ci.next_version("0.1", "main", []), "0.1.0")
        self.assertEqual(ci.next_version("0.2", "develop", ["v0.1.99"]), "0.2.0-rc1")
        for base in ("0.1.0", "01.1", "0.1\nanything", "latest"):
            with self.assertRaises(ValueError):
                ci.next_version(base, "main", [])

    def test_pr_never_publishes_or_uses_a_release_version(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ci, "run", return_value="v0.1.2\n"):
            root = Path(folder)
            (root / "version.txt").write_text("0.1\n")
            output = root / "output"
            with patch.object(ci, "ROOT", root), patch.dict(os.environ, {
                "GITHUB_REF_NAME": "23/merge", "GITHUB_EVENT_NAME": "pull_request",
                "PR_NUMBER": "23", "GITHUB_RUN_NUMBER": "5", "GITHUB_OUTPUT": str(output),
            }):
                ci.plan()
            fields = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(fields["version"], "0.1.3-pr23.5")
            self.assertEqual(fields["publish"], "false")


class Archives(unittest.TestCase):
    def test_traversal_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "input.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("allowed", "safe")
                output.writestr("../escape", "bad")
            with self.assertRaises(ValueError):
                ci.extract(archive, root / "staging", zipped=True)
            self.assertFalse((root / "staging").exists())
            self.assertFalse((root / "escape").exists())

    def test_case_collisions_and_linked_source_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "input.tar"
            for names in (("A", "a"), ("link",)):
                with tarfile.open(archive, "w") as output:
                    for name in names:
                        item = tarfile.TarInfo(name)
                        if name == "link":
                            item.type, item.linkname = tarfile.SYMTYPE, "../../outside"
                        else:
                            item.size = 1
                        output.addfile(item, io.BytesIO(b"x") if item.isfile() else None)
                with self.assertRaises(ValueError):
                    ci.extract(archive, root / "staging")
                self.assertFalse((root / "staging").exists())

    def test_existing_destination_and_changed_download_cache_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "staging"
            destination.mkdir()
            (destination / "owner").write_text("keep")
            with self.assertRaises(ValueError):
                ci.extract(root / "missing", destination)
            self.assertEqual((destination / "owner").read_text(), "keep")
            spec = {"sha256": hashlib.sha256(b"expected").hexdigest(), "url": "https://invalid.local/source"}
            cached = root / spec["sha256"]
            cached.write_bytes(b"changed")
            with patch.object(ci.urllib.request, "urlopen") as fetch:
                with self.assertRaises(ValueError):
                    ci.download(spec, root)
                fetch.assert_not_called()
            self.assertEqual(cached.read_bytes(), b"changed")


class Packages(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patch_root = patch.object(ci, "ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.patch_cache = patch.object(ci, "CACHE", self.root / ".tool-cache")
        self.patch_cache.start()
        self.addCleanup(self.patch_cache.stop)

    def package(self, target="macos-arm64", paths=None):
        base = self.root / "build" / target
        bundle = base / "bundle"
        bundle.mkdir(parents=True)
        if target.startswith("macos"):
            binary = bundle / "RetroArch.app/Contents/MacOS/RetroArch"
            library = bundle / "RetroArch.app/Contents/Frameworks/libSDL3.0.dylib"
        else:
            binary = bundle / "RetroArch" / ("retroarch.exe" if target.startswith("windows") else "retroarch")
            library = bundle / "RetroArch" / ("SDL3.dll" if target.startswith("windows") else "lib/libSDL3.so.0")
        binary.parent.mkdir(parents=True, exist_ok=True)
        library.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"native frontend fixture")
        binary.chmod(0o755)
        library.write_bytes(b"native SDL fixture")
        record = {"target": target, "version": "0.1.0", "sources": ci.LOCK["sources"],
                  "recipe_sha": "a" * 40, "upstream_version": ci.LOCK["upstream_version"],
                  "requirements": ci.TARGETS[target], **(paths or ci.runtime_paths(bundle, binary, library))}
        ci.write(base / "build.json", record)
        ci.write(base / "sdl-test.json", {"passed": True, "library_sha256": ci.digest(library)})
        ci.write(base / "frontend-test.json", {"passed": True, "binary_sha256": ci.digest(binary)})
        ci.package(target)
        return next((self.root / "dist").glob(f"*-{target}.json"))

    def test_windows_metadata_roundtrips_through_zip_and_manifest_on_every_host(self):
        bundle = PureWindowsPath("D:/build area/bundle")
        paths = ci.runtime_paths(bundle, bundle / "RetroArch/retroarch.exe", bundle / "RetroArch/SDL3.dll")
        manifest_path = self.package("windows-x64", paths=paths)
        manifest = ci.verify_manifest(manifest_path)
        with zipfile.ZipFile(manifest_path.with_suffix(".zip")) as archive:
            for key, check, hash_key in (("binary", "frontend", "binary_sha256"), ("sdl", "sdl", "library_sha256")):
                reference = manifest[key]
                self.assertNotIn("\\", reference)
                self.assertIn(reference, archive.namelist())
                self.assertEqual(manifest["checks"][check][hash_key], hashlib.sha256(archive.read(reference)).hexdigest())
        with self.assertRaises(ValueError):
            ci.runtime_paths(bundle, PureWindowsPath("C:/unrelated/retroarch.exe"), bundle / "RetroArch/SDL3.dll")

    def test_invalid_or_missing_runtime_references_fail_without_changing_the_archive(self):
        path = self.package("windows-x64")
        original = ci.read(path)
        archive_hash = ci.digest(path.with_suffix(".zip"))
        for key in ("binary", "sdl"):
            for reference in (r"RetroArch\SDL3.dll", "../outside.dll", "C:/outside.dll", "/outside.dll", "RetroArch//SDL3.dll", "RetroArch/SDL3.dll/", "RetroArch/missing.dll", None):
                with self.subTest(key=key, reference=reference):
                    manifest = copy.deepcopy(original)
                    manifest[key] = reference
                    ci.write(path, manifest)
                    with self.assertRaises(ValueError):
                        ci.verify_manifest(path)
                    self.assertEqual(ci.digest(path.with_suffix(".zip")), archive_hash)

    def test_nonportable_build_metadata_is_rejected_before_native_path_access(self):
        self.package("windows-x64")
        base = self.root / "build/windows-x64"
        record = ci.read(base / "build.json")
        record["sdl"] = r"RetroArch\SDL3.dll"
        ci.write(base / "build.json", record)
        before = ci.inventory(self.root / "dist")
        with self.assertRaises(ValueError):
            ci.package("windows-x64")
        self.assertEqual(ci.inventory(self.root / "dist"), before)

    def test_roundtrip_checks_executable_bits_and_exact_inventory(self):
        path = self.package()
        manifest = ci.verify_manifest(path)
        self.assertEqual(manifest["files"][manifest["binary"]]["mode"], (self.root / "build/macos-arm64/bundle" / manifest["binary"]).stat().st_mode & 0o777)
        with zipfile.ZipFile(path.with_suffix(".zip")) as archive:
            self.assertEqual(archive.getinfo(manifest["binary"]).create_system, 3)
            self.assertEqual(archive.read(manifest["binary"]), b"native frontend fixture")

    def test_modified_archive_or_missing_native_acknowledgment_stops_release(self):
        path = self.package()
        manifest = ci.read(path)
        manifest["checks"]["sdl"]["passed"] = False
        ci.write(path, manifest)
        with self.assertRaisesRegex(ValueError, "native checks"):
            ci.verify_manifest(path)
        manifest["checks"]["sdl"]["passed"] = True
        ci.write(path, manifest)
        with path.with_suffix(".zip").open("ab") as archive:
            archive.write(b"changed")
        with self.assertRaisesRegex(ValueError, "archive changed"):
            ci.verify_manifest(path)

    def test_stale_test_receipt_and_existing_package_are_preserved(self):
        path = self.package()
        original = path.with_suffix(".zip").read_bytes()
        with self.assertRaisesRegex(ValueError, "Existing release"):
            ci.package("macos-arm64")
        self.assertEqual(original, path.with_suffix(".zip").read_bytes())
        binary = self.root / "build/macos-arm64/bundle" / ci.read(path)["binary"]
        binary.write_bytes(b"new untested frontend")
        with self.assertRaisesRegex(ValueError, "different binaries"):
            ci.package("macos-arm64")

    def test_incomplete_matrix_cannot_publish(self):
        self.package()
        with self.assertRaisesRegex(ValueError, "all five"):
            ci.verify_release("0.1.0")

    def test_complete_release_verifies_exact_sources_recipes_and_assets(self):
        lock = copy.deepcopy(ci.LOCK)
        source_bytes = b"original source fixture"
        for spec in lock["sources"].values():
            spec["sha256"] = hashlib.sha256(source_bytes).hexdigest()
        with patch.object(ci, "LOCK", lock):
            for target in ci.TARGETS:
                self.package(target)
            ci.run(["git", "init", "-q", self.root])
            (self.root / "recipe.txt").write_text("test packaging recipe\n")
            ci.run(["git", "add", "recipe.txt"], cwd=self.root)
            ci.run(["git", "-c", "user.name=Build Test", "-c", "user.email=test@invalid.local", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], cwd=self.root)
            sha = ci.run(["git", "rev-parse", "HEAD"], cwd=self.root).strip()
            dist = self.root / "dist"
            for path in dist.glob("*.json"):
                manifest = ci.read(path)
                manifest["recipe_sha"] = sha
                ci.write(path, manifest)
            ci.run(["git", "archive", "--format=tar.gz", f"--output={dist / 'source-broomm-retroarch-recipes.tar.gz'}", "HEAD"], cwd=self.root)
            for name, spec in lock["sources"].items():
                (dist / f"source-{name}-{spec['revision']}.tar.gz").write_bytes(source_bytes)
            with patch.dict(os.environ, {"GITHUB_SHA": sha}):
                self.assertEqual(len(ci.verify_release("0.1.0")), 5)
                (dist / "unexpected.zip").write_bytes(b"extra")
                with self.assertRaisesRegex(ValueError, "unexpected"):
                    ci.verify_release("0.1.0")
                (dist / "unexpected.zip").unlink()
                (dist / "source-broomm-retroarch-recipes.tar.gz").write_bytes(b"wrong recipes")
                with self.assertRaisesRegex(ValueError, "packaging commit"):
                    ci.verify_release("0.1.0")

    def test_native_config_keeps_writable_paths_inside_fixture(self):
        private = self.root / "profile"
        private.mkdir()
        configuration = ci.test_configuration(private, 12345)
        values = dict((key.strip(), json.loads(value.strip())) for key, value in
                      (line.split("=", 1) for line in configuration.splitlines()))
        for key, value in values.items():
            if key.endswith(("directory", "_path", "_dir")):
                self.assertTrue(Path(value).resolve().is_relative_to(private.resolve()), key)
        self.assertEqual(values["history_list_enable"], "false")
        self.assertEqual(values["confirm_quit"], "false")


class GitHubFailures(unittest.TestCase):
    def response(self, code, body="{}", returncode=0):
        return subprocess.CompletedProcess([], returncode, f"HTTP/2.0 {code}\nContent-Type: application/json\n\n{body}", "")

    def test_only_confirmed_404_is_absence(self):
        with patch.object(ci.subprocess, "run", return_value=self.response(404, returncode=1)):
            self.assertIsNone(ci.github_request("repos/owner/project/releases/tags/v1"))
        for result in (self.response(401, returncode=1), self.response(500, returncode=1), subprocess.CompletedProcess([], 1, "", "network failed")):
            with patch.object(ci.subprocess, "run", return_value=result), self.assertRaises(RuntimeError):
                ci.github_request("repos/owner/project/releases/tags/v1")

    def test_drafts_on_later_pages_are_not_treated_as_absent(self):
        page = [{"tag_name": f"v1.0.{number}"} for number in range(100)]
        draft = {"tag_name": "v0.1.0", "draft": True}
        with patch.object(ci, "github_request", side_effect=[page, [draft]]) as request:
            self.assertEqual(ci.find_release("owner/project", "v0.1.0"), draft)
            self.assertEqual(request.call_count, 2)

    def test_missing_repository_or_unknown_list_is_not_release_absence(self):
        for result in (None, {"message": "failed"}):
            with patch.object(ci, "github_request", return_value=result), self.assertRaises(ValueError):
                ci.find_release("owner/project", "v0.1.0")

    def test_existing_tag_identity_is_dereferenced_and_preserved(self):
        with patch.object(ci, "github_request", side_effect=[
            {"object": {"type": "tag", "sha": "b" * 40}},
            {"object": {"type": "commit", "sha": "a" * 40}},
        ]) as request:
            self.assertEqual(ci.tag_commit("owner/project", "v0.1.0"), "a" * 40)
            self.assertEqual(request.call_count, 2)
        with tempfile.TemporaryDirectory() as folder, patch.object(ci, "CACHE", Path(folder)), patch.object(ci, "ROOT", Path(folder)):
            (Path(folder) / "dist").mkdir()
            with patch.object(ci, "verify_release", return_value=[]), patch.object(ci, "find_release", return_value=None), patch.object(ci, "github_request", return_value=None) as request, patch.object(ci, "tag_commit", return_value="b" * 40), patch.object(ci, "run") as mutate, patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/project", "GITHUB_SHA": "a" * 40}):
                with self.assertRaisesRegex(ValueError, "different commit"):
                    ci.publish("0.1.0")
                mutate.assert_not_called()
                self.assertTrue(all(call.kwargs.get("payload") is None for call in request.call_args_list))

    def test_upload_failure_never_publishes_draft(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ci, "CACHE", Path(folder)), patch.object(ci, "ROOT", Path(folder)):
            (Path(folder) / "dist").mkdir()
            with patch.object(ci, "verify_release", return_value=[]), patch.object(ci, "find_release", return_value=None), patch.object(ci, "github_request", return_value=None), patch.object(ci, "tag_commit", return_value="a" * 40), patch.object(ci, "run", side_effect=RuntimeError("upload failed")) as mutate, patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/project", "GITHUB_SHA": "a" * 40}):
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    ci.publish("0.1.0")
                self.assertEqual(mutate.call_count, 1)
                self.assertIn("--draft", mutate.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
