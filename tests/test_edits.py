"""Exercise batch edits, disk failures and recovery using real project files."""
from pathlib import Path
import json
import os
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from companion import edits as edits_module
from companion.edits import (EditTransactions, MAX_EDIT_BYTES, MAX_EDIT_FILES,
                             MAX_PLANS, PLAN_LIFETIME, RECOVERY_DIRECTORY)
from companion.projects import MAX_FILE_BYTES, ProjectError, ProjectGrant, revision


class EditTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        # Match ProjectGrant canonical paths: macOS /var aliases and Windows
        # 8.3 temporary directory names must compare to the same real folder.
        self.root = Path(self.directory.name).resolve(strict=True)
        (self.root / "src").mkdir()
        (self.root / "test").mkdir()
        (self.root / "src/a.sv").write_bytes(b"module old;\r\nendmodule\r\n")
        (self.root / "src/b.sv").write_bytes(b"module use; old dut(); endmodule\n")
        self.project = ProjectGrant(self.root)
        self.edits = EditTransactions(self.project)

    def tearDown(self):
        self.directory.cleanup()

    def proposal(self, paths=("src/a.sv", "src/b.sv")):
        return [{"path": path, "revision": self.project.read(path)["revision"],
                 "content": self.project.read(path)["content"].replace("old", "new"), "edit_count": 1}
                for path in paths]

    def plan(self):
        return self.edits.preview(self.proposal())

    def assert_error(self, status, function, *args):
        with self.assertRaises(ProjectError) as caught:
            function(*args)
        self.assertEqual(caught.exception.status, status, str(caught.exception))
        return caught.exception

    def test_preview_apply_undo_preserve_exact_bytes_modes_and_consume_ids(self):
        target = self.root / "src/a.sv"
        target.chmod(0o640)
        mode = stat.S_IMODE(target.stat().st_mode)
        before = {item["path"]: (self.root / item["path"]).read_bytes() for item in self.proposal()}
        plan = self.plan()
        self.assertEqual(set(plan), {"id", "files", "expires_in"})
        self.assertLessEqual(plan["expires_in"], 300)
        self.assertEqual(set(plan["files"][0]), {"path", "before", "after", "before_revision", "after_revision", "edit_count"})
        self.assertEqual(target.read_bytes(), before["src/a.sv"])
        self.assertFalse(self.project.build_path.exists())
        result = self.edits.apply(plan["id"])
        self.assertEqual(target.read_bytes(), b"module new;\r\nendmodule\r\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode)
        self.assertEqual(result["files"][0]["revision"], revision(target.read_bytes()))
        self.assert_error(404, self.edits.apply, plan["id"])
        self.assertFalse(self.edits.status()["recovery_required"])
        restored = self.edits.undo(result["undo_id"])
        for item in restored["files"]:
            self.assertEqual((self.root / item["path"]).read_bytes(), before[item["path"]])
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode)
        self.assert_error(404, self.edits.undo, result["undo_id"])
        self.assertEqual(list((self.root / "src").glob(".vwb-save-*")), [])

    def test_expiry_and_bounded_cached_plans(self):
        with patch("companion.edits.time.monotonic", return_value=10):
            plan = self.plan()
        with patch("companion.edits.time.monotonic", return_value=10 + PLAN_LIFETIME):
            self.assert_error(410, self.edits.apply, plan["id"])
        for _ in range(MAX_PLANS + 5):
            self.plan()
        self.assertLessEqual(len(self.edits._plans), MAX_PLANS)

    def test_preflight_conflict_changes_no_files_and_keeps_plan_reviewable(self):
        plan = self.plan()
        target = self.root / "src/b.sv"
        target.write_bytes(b"external edit\n")
        self.assert_error(409, self.edits.apply, plan["id"])
        self.assertEqual((self.root / "src/a.sv").read_bytes(), plan["files"][0]["before"].encode())
        self.assertEqual(target.read_bytes(), b"external edit\n")
        self.assertFalse(self.project.build_path.exists())
        self.assertFalse(self.edits.status()["recovery_required"])

    def test_failed_middle_write_rolls_back_and_cleans_journal(self):
        plan = self.plan()
        replace = self.edits._replace_file
        calls = []

        def injected(target, staged):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("injected disk error")
            replace(target, staged)

        with patch.object(self.edits, "_replace_file", side_effect=injected):
            self.assert_error(500, self.edits.apply, plan["id"])
        for item in plan["files"]:
            self.assertEqual((self.root / item["path"]).read_bytes(), item["before"].encode())
        self.assertFalse(self.edits.status()["recovery_required"])
        self.assertFalse(self.edits._pending.exists())
        self.assertEqual(list((self.root / "src").glob(".vwb-save-*")), [])

    def interrupted(self):
        plan = self.plan()
        replace = self.edits._replace_file
        count = 0

        def injected(target, staged):
            nonlocal count
            count += 1
            if count == 2:
                # BaseException models an abrupt exit: no ordinary rollback.
                raise SystemExit("injected process interruption")
            replace(target, staged)

        with patch.object(self.edits, "_replace_file", side_effect=injected):
            with self.assertRaises(SystemExit):
                self.edits.apply(plan["id"])
        return plan

    def test_restart_detects_interrupted_write_and_requires_explicit_recovery(self):
        plan = self.interrupted()
        self.assertEqual((self.root / "src/a.sv").read_bytes(), plan["files"][0]["after"].encode())
        restarted = EditTransactions(ProjectGrant(self.root))
        status = restarted.status()
        self.assertTrue(status["recovery_required"])
        self.assertEqual([item["path"] for item in status["files"]], ["src/a.sv", "src/b.sv"])
        self.assert_error(409, restarted.preview, self.proposal())
        self.assert_error(409, restarted.apply, "unknown")
        self.assert_error(409, restarted.undo, "unknown")
        result = restarted.recover()
        self.assertFalse(result["recovery_required"])
        self.assertFalse(restarted.status()["recovery_required"])
        self.assertFalse(restarted._pending.exists())
        for item in plan["files"]:
            self.assertEqual((self.root / item["path"]).read_bytes(), item["before"].encode())

    def test_failed_rollback_keeps_backups_and_restart_can_recover(self):
        plan = self.plan()
        replace = self.edits._replace_file
        count = 0

        def injected(target, staged):
            nonlocal count
            count += 1
            if count >= 2:
                raise OSError("disk unavailable during commit and rollback")
            replace(target, staged)

        with patch.object(self.edits, "_replace_file", side_effect=injected):
            self.assert_error(409, self.edits.apply, plan["id"])
        self.assertTrue(self.edits.status()["recovery_required"])
        self.assertTrue((self.edits._pending / "0.before").is_file())
        restarted = EditTransactions(ProjectGrant(self.root))
        restarted.recover()
        self.assertFalse(restarted.status()["recovery_required"])
        self.assertEqual((self.root / "src/a.sv").read_bytes(), plan["files"][0]["before"].encode())

    def test_external_change_during_commit_is_retained_and_recovery_refuses_to_clobber(self):
        plan = self.plan()
        replace = self.edits._replace_file
        count = 0

        def injected(target, staged):
            nonlocal count
            count += 1
            replace(target, staged)
            if count == 1:
                target.write_bytes(b"outside writer won\n")

        with patch.object(self.edits, "_replace_file", side_effect=injected):
            self.assert_error(409, self.edits.apply, plan["id"])
        self.assertEqual((self.root / "src/a.sv").read_bytes(), b"outside writer won\n")
        self.assertEqual((self.root / "src/b.sv").read_bytes(), plan["files"][1]["before"].encode())
        self.assertTrue(self.edits.status()["recovery_required"])
        restarted = EditTransactions(ProjectGrant(self.root))
        self.assert_error(409, restarted.recover)
        self.assertEqual((self.root / "src/a.sv").read_bytes(), b"outside writer won\n")
        self.assertTrue(restarted._pending.exists())
        (self.root / "src/a.sv").write_bytes(plan["files"][0]["after"].encode())
        restarted.recover()
        for item in plan["files"]:
            self.assertEqual((self.root / item["path"]).read_bytes(), item["before"].encode())

    def test_undo_checks_all_files_before_touching_any(self):
        plan = self.plan()
        result = self.edits.apply(plan["id"])
        target = self.root / "src/b.sv"
        target.write_bytes(b"new external work\n")
        self.assert_error(409, self.edits.undo, result["undo_id"])
        self.assertEqual((self.root / "src/a.sv").read_bytes(), plan["files"][0]["after"].encode())
        self.assertEqual(target.read_bytes(), b"new external work\n")
        self.assertFalse(self.edits.status()["recovery_required"])

    def test_external_race_on_next_file_restores_previous_writes_without_overwriting_it(self):
        plan = self.plan()
        replace = self.edits._replace_file
        count = 0

        def injected(target, staged):
            nonlocal count
            count += 1
            replace(target, staged)
            if count == 1:
                (self.root / "src/b.sv").write_bytes(b"outside next-file edit\n")

        with patch.object(self.edits, "_replace_file", side_effect=injected):
            self.assert_error(409, self.edits.apply, plan["id"])
        self.assertEqual((self.root / "src/a.sv").read_bytes(), plan["files"][0]["before"].encode())
        self.assertEqual((self.root / "src/b.sv").read_bytes(), b"outside next-file edit\n")
        self.assertTrue(self.edits.status()["recovery_required"])

    def test_expired_undo_and_restart_have_no_reusable_undo_authority(self):
        with patch("companion.edits.time.monotonic", return_value=10):
            result = self.edits.apply(self.plan()["id"])
        with patch("companion.edits.time.monotonic", return_value=10 + PLAN_LIFETIME):
            self.assert_error(410, self.edits.undo, result["undo_id"])
        self.assert_error(404, EditTransactions(ProjectGrant(self.root)).undo, result["undo_id"])

    def test_preparing_journal_rejects_unknown_files_without_deleting_them(self):
        self.edits._prepare_build()
        self.edits._recovery_directory(create=True)
        self.edits._pending.mkdir(mode=0o700)
        important = self.edits._pending / "important.txt"
        self.edits._write_private(important, b"keep")
        self.assertTrue(self.edits.status()["recovery_required"])
        self.assert_error(409, self.edits.recover)
        self.assertEqual(important.read_bytes(), b"keep")

    @unittest.skipIf(os.name == "nt", "POSIX private directory permissions")
    def test_recovery_refuses_nonprivate_directory_and_backup(self):
        self.interrupted()
        self.edits._directory.chmod(0o755)
        self.assertTrue(self.edits.status()["recovery_required"])
        self.assert_error(409, self.edits.recover)
        self.edits._directory.chmod(0o700)
        (self.edits._pending / "0.before").chmod(0o644)
        self.assertTrue(self.edits.status()["recovery_required"])
        self.assert_error(409, self.edits.recover)
        (self.edits._pending / "0.before").chmod(0o600)
        self.edits.recover()

    def test_paths_links_unknown_files_generated_files_and_invalid_entries(self):
        (self.root / ".git").mkdir()
        (self.root / ".git/config").write_text("private", encoding="utf-8")
        self.project.build_path.mkdir()
        (self.project.build_path / "result.sv").write_text("generated", encoding="utf-8")
        for path, status in (("../outside", 403), (".git/config", 403),
                             (".vwb/result.sv", 403), ("src/missing.sv", 404)):
            with self.subTest(path=path):
                self.assert_error(status, self.edits.preview, [{"path": path, "content": "x", "revision": "x"}])
        original = self.proposal(("src/a.sv",))[0]
        self.assert_error(400, self.edits.preview, [original, original])
        self.assert_error(400, self.edits.preview, [{**original, "edit_count": True}])
        self.assert_error(400, self.edits.preview, [{**original, "extra": "bad"}])
        self.assert_error(400, self.edits.preview, [{**original, "content": "\ud800"}])
        try:
            (self.root / "src/link.sv").symlink_to(self.root / "src/a.sv")
            os.link(self.root / "src/b.sv", self.root / "src/hard.sv")
        except (OSError, NotImplementedError):
            self.skipTest("host does not permit link creation")
        self.assert_error(403, self.edits.preview, [{**original, "path": "src/link.sv"}])
        self.assert_error(403, self.edits.preview, [{**original, "path": "src/hard.sv"}])

    def test_file_count_bytes_utf8_and_revision_limits(self):
        self.assert_error(400, self.edits.preview, [])
        self.assert_error(400, self.edits.preview, self.proposal(("src/a.sv",)) * (MAX_EDIT_FILES + 1))
        original = self.proposal(("src/a.sv",))[0]
        self.assert_error(413, self.edits.preview, [{**original, "content": "x" * (MAX_FILE_BYTES + 1)}])
        (self.root / "src/b.sv").write_bytes(b"\xff")
        self.assert_error(415, self.edits.preview, [{**original, "path": "src/b.sv"}])
        self.assert_error(409, self.edits.preview, [{**original, "revision": None}])
        entries = []
        for index in range(MAX_EDIT_BYTES // (MAX_FILE_BYTES * 2) + 1):
            path = f"src/big{index}.sv"
            (self.root / path).write_bytes(b"x" * MAX_FILE_BYTES)
            entries.append({"path": path, "content": "y" * MAX_FILE_BYTES,
                            "revision": revision(b"x" * MAX_FILE_BYTES)})
        self.assert_error(413, self.edits.preview, entries)

    def test_never_adopts_nonempty_unowned_or_foreign_owned_build_directory(self):
        self.project.build_path.mkdir()
        important = self.project.build_path / "important.txt"
        important.write_text("keep", encoding="utf-8")
        plan = self.plan()
        self.assert_error(409, self.edits.apply, plan["id"])
        self.assertEqual(important.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.project.build_path / ".vwb-root").exists())
        marker = self.project.build_path / ".vwb-root"
        marker.write_text(json.dumps({"schema": 1, "project_relative": "../foreign"}), encoding="utf-8")
        self.assert_error(409, self.edits.apply, self.plan()["id"])
        self.assertEqual(important.read_text(encoding="utf-8"), "keep")

    def test_existing_owned_build_is_preserved_and_empty_build_can_be_adopted(self):
        self.project.build_path.mkdir()
        result = self.edits.apply(self.plan()["id"])
        self.assertTrue((self.project.build_path / ".vwb-root").is_file())
        important = self.project.build_path / "result.vcd"
        important.write_text("keep", encoding="utf-8")
        self.edits.undo(result["undo_id"])
        self.assertEqual(important.read_text(encoding="utf-8"), "keep")

    def test_new_build_ancestors_are_synced_before_first_source_replacement(self):
        (self.root / "existing").mkdir()
        for build, parents in ((".vwb", [self.root]),
                               ("generated/cache/native", [self.root, self.root / "generated", self.root / "generated/cache"]),
                               ("existing/cache/native", [self.root / "existing", self.root / "existing/cache"])):
            with self.subTest(build=build):
                self.project = ProjectGrant(self.root, build_dir=build)
                self.edits = EditTransactions(self.project)
                plan = self.plan()
                events = []
                real_sync = edits_module._sync_directory
                real_replace = os.replace

                def sync(directory):
                    events.append(("sync", Path(directory)))
                    return real_sync(directory)

                def replace(source, destination):
                    events.append(("replace", Path(destination)))
                    return real_replace(source, destination)

                with patch("companion.edits._sync_directory", side_effect=sync), \
                        patch("companion.edits.os.replace", side_effect=replace):
                    applied = self.edits.apply(plan["id"])
                first_source_write = events.index(("replace", self.root / "src/a.sv"))
                indices = [events.index(("sync", path)) for path in parents]
                self.assertEqual(indices, sorted(indices))
                self.assertTrue(all(index < first_source_write for index in indices))
                for path in (self.project.build_path, self.edits._directory, self.edits._pending):
                    self.assertIn(("sync", path), events[:first_source_write])
                self.edits.undo(applied["undo_id"])

    def test_build_parent_sync_failure_prevents_every_source_write(self):
        self.project = ProjectGrant(self.root, build_dir="generated/cache/native")
        self.edits = EditTransactions(self.project)
        plan = self.plan()
        real_sync = edits_module._sync_directory

        def sync(directory):
            if Path(directory) == self.root / "generated":
                raise OSError("injected parent directory fsync failure")
            return real_sync(directory)

        with patch("companion.edits._sync_directory", side_effect=sync), \
                patch.object(self.edits, "_replace_file") as replace:
            self.assert_error(500, self.edits.apply, plan["id"])
        replace.assert_not_called()
        for item in plan["files"]:
            self.assertEqual((self.root / item["path"]).read_bytes(), item["before"].encode())
        self.assertFalse(self.edits._pending.exists())
        self.assertEqual(list((self.root / "src").glob(".vwb-save-*")), [])
        # Retrying after mkdir succeeded but fsync failed must persist the
        # existing cache entry too, rather than assuming it was made durable.
        events = []

        def retry_sync(directory):
            events.append(("sync", Path(directory)))
            return real_sync(directory)

        real_replace = self.edits._replace_file

        def retry_replace(target, staged):
            events.append(("replace", target))
            return real_replace(target, staged)

        with patch("companion.edits._sync_directory", side_effect=retry_sync), \
                patch.object(self.edits, "_replace_file", side_effect=retry_replace):
            self.edits.apply(self.plan()["id"])
        self.assertLess(events.index(("sync", self.root / "generated")),
                        events.index(("replace", self.root / "src/a.sv")))

    def test_unowned_or_nonprivate_recovery_directory_is_not_modified(self):
        self.project.build_path.mkdir()
        recovery = self.project.build_path / RECOVERY_DIRECTORY
        recovery.mkdir(mode=0o700)
        important = recovery / "important.txt"
        important.write_text("keep", encoding="utf-8")
        self.assertTrue(self.edits.status()["recovery_required"])
        self.assert_error(409, self.edits.preview, self.proposal())
        self.assert_error(409, self.edits.recover)
        self.assertEqual(important.read_text(encoding="utf-8"), "keep")

    def test_tampered_backup_or_symlink_never_restores_untrusted_bytes(self):
        self.interrupted()
        backup = self.edits._pending / "0.before"
        backup.write_bytes(b"tampered backup")
        self.assertTrue(EditTransactions(ProjectGrant(self.root)).status()["recovery_required"])
        self.assert_error(409, self.edits.recover)
        self.assertEqual((self.root / "src/a.sv").read_bytes(), b"module new;\r\nendmodule\r\n")
        backup.unlink()
        try:
            backup.symlink_to(self.root / "src/a.sv")
        except (OSError, NotImplementedError):
            self.skipTest("host does not permit symlink creation")
        self.assertTrue(self.edits.status()["recovery_required"])
        self.assert_error(403, self.edits.recover)

    def test_preparing_and_committed_journal_cleanup_survive_restart(self):
        self.edits._prepare_build()
        self.edits._recovery_directory(create=True)
        self.edits._pending.mkdir(mode=0o700)
        self.edits._write_private(self.edits._pending / "0.before", b"incomplete backup")
        restarted = EditTransactions(ProjectGrant(self.root))
        self.assertTrue(restarted.status()["recovery_required"])
        self.assertEqual(restarted.recover()["files"], [])
        plan = self.plan()
        with patch.object(self.edits, "_cleanup", side_effect=OSError("cleanup unavailable")):
            result = self.edits.apply(plan["id"])
        self.assertTrue(self.edits.status()["recovery_required"])
        (self.edits._pending / "0.before").unlink()
        restarted = EditTransactions(ProjectGrant(self.root))
        self.assertFalse(restarted.recover()["recovery_required"])
        self.assertEqual((self.root / "src/a.sv").read_bytes(), plan["files"][0]["after"].encode())
        self.assertIn("undo_id", result)

    def test_project_lock_serializes_batch_and_revision_checked_save(self):
        plan = self.plan()
        entered, release = threading.Event(), threading.Event()
        replace = self.edits._replace_file
        results = []

        def held_replace(target, staged):
            if not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(5))
            replace(target, staged)

        def save():
            try:
                self.project.save("src/b.sv", "concurrent save", plan["files"][1]["before_revision"])
                results.append("saved")
            except ProjectError as exc:
                results.append(exc.status)

        with patch.object(self.edits, "_replace_file", side_effect=held_replace):
            thread = threading.Thread(target=lambda: self.edits.apply(plan["id"]))
            thread.start()
            self.assertTrue(entered.wait(5))
            saver = threading.Thread(target=save)
            saver.start()
            release.set()
            thread.join(5)
            saver.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(saver.is_alive())
        self.assertEqual(results, [409])


if __name__ == "__main__":
    unittest.main()
