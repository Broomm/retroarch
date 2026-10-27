"""Observable archive, release/version and publication failure boundaries."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import ci


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

    def package(self, target="macos-arm64"):
        base = self.root / "build" / target
        bundle = base / "bundle"
        bundle.mkdir(parents=True)
        (bundle / "frontend").write_bytes(b"native frontend fixture")
        (bundle / "frontend").chmod(0o755)
        (bundle / "sdl").write_bytes(b"native SDL fixture")
        record = {"target": target, "version": "0.1.0", "sources": ci.LOCK["sources"],
                  "recipe_sha": "a" * 40, "upstream_version": ci.LOCK["upstream_version"],
                  "requirements": ci.TARGETS[target], "binary": "frontend", "sdl": "sdl"}
        ci.write(base / "build.json", record)
        ci.write(base / "sdl-test.json", {"passed": True, "library_sha256": ci.digest(bundle / "sdl")})
        ci.write(base / "frontend-test.json", {"passed": True, "binary_sha256": ci.digest(bundle / "frontend")})
        ci.package(target)
        return next((self.root / "dist").glob(f"*-{target}.json"))

    def test_roundtrip_checks_executable_bits_and_exact_inventory(self):
        path = self.package()
        manifest = ci.verify_manifest(path)
        self.assertEqual(manifest["files"]["frontend"]["mode"], (self.root / "build/macos-arm64/bundle/frontend").stat().st_mode & 0o777)
        with zipfile.ZipFile(path.with_suffix(".zip")) as archive:
            self.assertEqual(archive.getinfo("frontend").create_system, 3)
            self.assertEqual(archive.read("frontend"), b"native frontend fixture")

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
        binary = self.root / "build/macos-arm64/bundle/frontend"
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
