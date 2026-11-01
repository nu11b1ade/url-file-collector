"""Extra CLI checks; all temporary test data stays under _fixture/."""

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "collect_url_files.py"


def run(src, out, *options, expected=0, env=None):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "-s", str(src), "-o", str(out)] + list(options),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=env,
    )
    assert result.returncode == expected, (result.returncode, result.stdout, result.stderr)
    return result


def write_files(folder, files):
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def snapshot(folder):
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in folder.rglob("*") if path.is_file()
    }


def check_matching(base):
    src, out = base / "matching", base / "matched"
    hits = {
        "http.txt": b"http://intranet/status\n",
        "https.txt": b"HtTpS://intranet/status\n",
        "ftp.txt": b"FtP://server/download\n",
        "file.txt": b"FiLe:///C:/shared/settings\n",
        "domain.txt": b"example.com\n",
        "nested/domain.txt": b"sub.example.com.cn.\n",
        "ip.txt": b"192.168.1.25:8443\n",
        "ip-punctuation.txt": b"The address is 127.0.0.1.\n",
        "utf8-bom.txt": "网址：example.com".encode("utf-8-sig"),
        "gbk.txt": "网址：example.com".encode("gbk"),
        "gb18030.txt": "😀网址：example.com".encode("gb18030"),
        "latin1.txt": b"\xff\xff example.com\n",
        "late-nul.dat": b"x" * 8192 + b"\0 example.com\n",
    }
    misses = {
        "clean.txt": b"intranet localhost version 1.2.3 and 3.11\n",
        "invalid.txt": b"999.1.2.3 1.2.3.999 example.c -bad.com bad-.com\n",
        "example.com": b"The filename must not cause a hit.\n",
        "fake.PNG": b"https://intranet/status\n",
        "nul.dat": b"\0https://intranet/status\n",
        "boundary-nul.dat": b"x" * 8191 + b"\0 example.com\n",
    }
    write_files(src, dict(hits, **misses))
    result = run(src, out, "--dry-run")
    assert "scanned=19 skipped=3 hits=13 copied=0" in result.stdout, result.stdout
    assert not out.exists()
    result = run(src, out)
    assert "scanned=19 skipped=3 hits=13 copied=13" in result.stdout, result.stdout
    assert snapshot(out) == hits


def check_collisions(base):
    src, out = base / "collision-src", base / "collision-out"
    first, second = b"https://a/\n", b"https://b/\n"
    originals = {"report.txt": b"https://c/\n", "report__1.txt": b"https://d/\n"}
    write_files(src, {"report.txt": first, "report__2.txt": second})
    write_files(out, originals)
    result = run(src, out, "--dry-run")
    assert "report.txt -> report__2.txt [would copy]" in result.stdout
    assert "report__2.txt -> report__2__1.txt [would copy]" in result.stdout
    assert snapshot(out) == originals
    result = run(src, out)
    assert "scanned=2 skipped=0 hits=2 copied=2" in result.stdout
    expected = dict(originals, **{"report__2.txt": first, "report__2__1.txt": second})
    assert snapshot(out) == expected
    result = run(src, out)
    assert "scanned=2 skipped=2 hits=2 copied=0" in result.stdout
    assert snapshot(out) == expected


def check_limits_and_output(base):
    src, out = base / "limited", base / "limited-out"
    write_files(src, {"hit.txt": b"a.co\n", "large.txt": b"example.com\n"})
    result = run(src, out, "--max-bytes", "5")
    assert "scanned=2 skipped=1 hits=1 copied=1" in result.stdout
    assert snapshot(out) == {"hit.txt": b"a.co\n"}

    src = base / "default-limit"
    write_files(src, {"too-large.txt": b"https://host/\n" + b"x" * 5000000})
    result = run(src, base / "unused", "--dry-run")
    assert "scanned=1 skipped=1 hits=0 copied=0" in result.stdout

    src = base / "nested-output"
    out = src / "results"
    write_files(src, {"hit.txt": b"example.com\n", "clean.txt": b"Just words\n"})
    assert "scanned=2 skipped=0 hits=1 copied=1" in run(src, out).stdout
    result = run(src, out, "--verbose")
    assert "scanned=2 skipped=1 hits=1 copied=0" in result.stdout
    assert "same content; skipped" in result.stderr
    assert snapshot(out) == {"hit.txt": b"example.com\n"}


def check_redirected_report(base):
    src, out = base / "unicode-src", base / "unicode-out"
    files = {"客户.txt": b"example.com\n"}
    write_files(src, files)
    env = dict(os.environ, PYTHONIOENCODING="cp1252:strict")
    result = run(src, out, "--verbose", env=env)
    assert "scanned=1 skipped=0 hits=1 copied=1" in result.stdout
    assert "\\u5ba2\\u6237.txt" in result.stdout
    assert "[copied]" in result.stdout
    assert snapshot(out) == files


def check_errors(base):
    src = base / "errors"
    write_files(src, {"hit.txt": b"example.com\n"})
    run(base / "missing", base / "unused", expected=2)
    run(src / "hit.txt", base / "unused", expected=2)
    run(src, src / "hit.txt", expected=1)
    run(src, src, expected=1)
    run(src, base / "unused", "--max-bytes", "-1", expected=2)
    result = run(src, base / "unused", "--max-bytes", "0", "--dry-run")
    assert "scanned=1 skipped=1 hits=0 copied=0" in result.stdout


if __name__ == "__main__":
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 8))
    with tempfile.TemporaryDirectory(prefix="verify-", dir=str(ROOT / "_fixture")) as folder:
        base = Path(folder)
        check_matching(base)
        check_collisions(base)
        check_limits_and_output(base)
        check_redirected_report(base)
        check_errors(base)
    print("PASS: matching, encodings, binary checks, collisions, deduplication, "
          "dry-run, size limits, nested output, Unicode reporting, exit codes, "
          "and Python 3.8 syntax")
