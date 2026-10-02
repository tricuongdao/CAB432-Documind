#!/usr/bin/env python3
"""Zip a directory's contents flat (for Lambda deployment packages)."""

import os
import sys
import zipfile


def zip_dir(in_dir: str, out_zip: str):
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(in_dir):
            for f in sorted(files):
                if f.endswith(".pyc"):
                    continue
                p = os.path.join(root, f)
                arc = os.path.relpath(p, in_dir).replace(os.sep, "/")
                z.write(p, arc)
    print(f"zipped {in_dir} -> {out_zip}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python zip_dir.py <in_dir> <out_zip>")
        sys.exit(2)
    zip_dir(sys.argv[1], sys.argv[2])
