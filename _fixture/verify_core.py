"""Exercise the shared collector with real, temporary filesystem fixtures."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collect_url_files_core import collect


class CollectionChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="core-", dir=str(ROOT / "_fixture")
        )
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.src = self.base / "source"
        self.src.mkdir()
        self.out = self.base / "output"

    def write(self, relative, data):
        path = self.src / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_preview_reports_relative_hits_without_writing(self):
        self.write("blocked.PNG", b"https://host/\n")
        self.write("clean.txt", b"Just words\n")
        self.write("nested/客户.txt", "网址：example.com\n".encode("gbk"))
        events = []
        console = io.StringIO()

        with redirect_stdout(console):
            result = collect(self.src, self.out, dry_run=True, on_event=events.append)

        self.assertEqual(
            (result.scanned, result.hits, result.copied, result.skipped), (3, 1, 0, 1)
        )
        self.assertFalse(self.out.exists())
        self.assertEqual(console.getvalue(), "")
        self.assertEqual([event.progress.scanned for event in events], [1, 2, 3])
        self.assertEqual(events[0].progress.hits, 0)
        self.assertEqual(events[-1].progress, result)
        self.assertEqual(
            [(event.relative_path, event.destination, event.action)
             for event in events if event.destination is not None],
            [(Path("nested/客户.txt"), Path("nested/客户.txt"), "would copy")],
        )

    def test_collision_copies_and_sha256_duplicates_report_correct_counters(self):
        self.write("report.txt", b"https://a/\n")
        self.write("report__2.txt", b"https://b/\n")
        self.out.mkdir()
        (self.out / "report.txt").write_bytes(b"https://c/\n")
        (self.out / "report__1.txt").write_bytes(b"https://d/\n")
        events = []

        result = collect(self.src, self.out, on_event=events.append)

        self.assertEqual(
            (result.scanned, result.hits, result.copied, result.skipped), (2, 2, 2, 0)
        )
        self.assertEqual(
            [event.destination for event in events],
            [Path("report__2.txt"), Path("report__2__1.txt")],
        )
        expected = {
            "report.txt": b"https://c/\n",
            "report__1.txt": b"https://d/\n",
            "report__2.txt": b"https://a/\n",
            "report__2__1.txt": b"https://b/\n",
        }
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.out.iterdir()}, expected
        )

        events.clear()
        result = collect(self.src, self.out, on_event=events.append)
        self.assertEqual(
            (result.scanned, result.hits, result.copied, result.skipped), (2, 2, 0, 2)
        )
        self.assertEqual([event.action for event in events], ["same content; skipped"] * 2)
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.out.iterdir()}, expected
        )

    def test_symlinks_are_not_scanned_or_used_as_copy_targets(self):
        self.write("hit.txt", b"example.com\n")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "external.txt").write_bytes(b"example.com\n")
        self.out.mkdir()
        try:
            (self.src / "linked.txt").symlink_to(outside / "external.txt")
            (self.src / "linked-dir").symlink_to(outside, target_is_directory=True)
            (self.out / "hit.txt").symlink_to(outside / "external.txt")
        except (OSError, NotImplementedError) as error:
            self.skipTest("Symlinks are unavailable: {}".format(error))

        result = collect(self.src, self.out)

        self.assertEqual(
            (result.scanned, result.hits, result.copied, result.skipped), (2, 1, 1, 1)
        )
        self.assertTrue((self.out / "hit.txt").is_symlink())
        self.assertEqual((self.out / "hit__1.txt").read_bytes(), b"example.com\n")
        self.assertEqual(list(outside.iterdir()), [outside / "external.txt"])

    def test_output_subdirectory_link_cannot_redirect_copies(self):
        self.write("nested/hit.txt", b"example.com\n")
        outside = self.base / "outside"
        outside.mkdir()
        self.out.mkdir()
        try:
            (self.out / "nested").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Symlinks are unavailable: {}".format(error))

        with self.assertRaisesRegex(ValueError, "symbolic link|reparse point"):
            collect(self.src, self.out)

        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((self.src / "nested/hit.txt").read_bytes(), b"example.com\n")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Requires Linux file size limits")
    def test_failed_write_removes_partial_file_and_allows_clean_retry(self):
        import resource
        import signal

        data = b"https://example.com/status\n"
        self.write("hit.txt", data)
        previous_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
        previous_handler = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        try:
            # Trigger a real write/flush failure without mocking the collector.
            resource.setrlimit(resource.RLIMIT_FSIZE, (8, previous_limit[1]))
            with self.assertRaises(OSError):
                collect(self.src, self.out)
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, previous_limit)
            signal.signal(signal.SIGXFSZ, previous_handler)

        self.assertFalse((self.out / "hit.txt").exists())
        result = collect(self.src, self.out)
        self.assertEqual(result.copied, 1)
        self.assertEqual(list(self.out.iterdir()), [self.out / "hit.txt"])
        self.assertEqual((self.out / "hit.txt").read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
