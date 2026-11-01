#!/usr/bin/env python3
r"""Collect text files containing URLs, domain names, or IPv4 addresses.
递归扫描文本内容，将含 URL、域名或 IPv4 地址的文件复制到输出目录。

Python 3.8+, standard library only / 仅使用标准库，无需安装依赖。

Windows: py collect_url_files.py -s "C:\input" -o "C:\collected"
Linux:   python3 collect_url_files.py -s ./input -o ./collected
Preview / 预览: add --dry-run; no files or directories are created.
Details / 详细日志: add --verbose. Default size limit / 默认大小限制: 5000000 bytes.

Relative paths are kept; different-content collisions get __1, __2, etc.
保留相对路径；内容不同的重名文件在扩展名前添加 __1、__2 等后缀。
Same-size, same-SHA256 files are skipped but still counted as hits.
大小及 SHA256 相同的文件不重复复制，但仍计入命中数。
"""

import argparse
from pathlib import Path
import sys

from collect_url_files_core import DEFAULT_MAX_BYTES, collect


def nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    if number < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return number


def main(argv=None) -> int:
    # Redirected Windows consoles may use a code page that cannot encode paths.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--src", "-s", required=True, help="source directory / 源目录")
    parser.add_argument("--out", "-o", required=True, help="output directory / 输出目录")
    parser.add_argument("--dry-run", action="store_true", help="preview without writing")
    parser.add_argument(
        "--max-bytes", type=nonnegative_int, default=DEFAULT_MAX_BYTES,
        help="skip larger files (default: 5000000 bytes)",
    )
    parser.add_argument("--verbose", action="store_true", help="log per-file decisions")
    args = parser.parse_args(argv)
    try:
        src = Path(args.src).expanduser().resolve()
        if not src.is_dir():
            print("error: Source directory missing or not a directory: {}".format(src),
                  file=sys.stderr)
            return 2
        out = Path(args.out).expanduser().resolve()
        hit_paths = []

        def on_event(event):
            if event.destination is not None:
                hit_paths.append((event.relative_path, event.destination, event.action))
            if args.verbose:
                if event.action == "skipped":
                    message = "skip {}: {}".format(event.relative_path, event.reason)
                else:
                    message = "{}: {}".format(event.action, event.relative_path)
                print(message, file=sys.stderr)

        result = collect(src, out, args.max_bytes, args.dry_run, on_event=on_event)
        print("scanned={} skipped={} hits={} copied={}".format(
            result.scanned, result.skipped, result.hits, result.copied
        ))
        if hit_paths:
            print("Hit paths (source -> output, relative to their roots):")
            for source, destination, action in hit_paths:
                print("  {} -> {} [{}]".format(source, destination, action))
        else:
            print("Hit paths: (none)")
        return 0
    except Exception as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
