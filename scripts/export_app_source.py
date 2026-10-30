"""Build a source archive containing only the application source.

Allowlist, not denylist: exactly the two trees that make up the running app.
Nothing else, so no report, docs, scripts, deployment YAML, stale duplicate
tree, bytecode or node_modules can leak in.

Included:
  backend/**   API, worker, scaler, Dockerfile, requirements
  frontend/**  React app, Vite config, package files, bundled fonts

Excluded on purpose: infra/, scripts/, docs/, src/ (stale duplicate of backend),
REPORT.md, README.md, the report PDF/DOCX, submission-a3-infra.yml, .build/,
__pycache__, *.pyc, node_modules, dist.

Run: python scripts/export_app_source.py
"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "DocuMind-A3-n12136514-app-source"
OUT_DIR = os.path.join(ROOT, ".build", "submission")
ZIP = os.path.join(OUT_DIR, NAME + ".zip")

TREES = ["backend", "frontend"]
SKIP_DIRS = {"__pycache__", "node_modules", "dist", ".vite", ".cache"}
SKIP_EXT = {".pyc", ".pyo", ".log"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}


def collect():
    files = []
    for tree in TREES:
        base = os.path.join(ROOT, tree)
        if not os.path.isdir(base):
            raise SystemExit(f"missing required tree: {tree}")
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                if fn in SKIP_FILES or os.path.splitext(fn)[1] in SKIP_EXT:
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                files.append((full, f"{NAME}/{rel}"))
    return files


def main():
    files = collect()
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(ZIP):
        os.remove(ZIP)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for full, arc in files:
            z.write(full, arc)

    # verify what actually landed
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
        bad = [n for n in names
               if any(s in n for s in ("/scripts/", "/docs/", "/infra/", "__pycache__",
                                       "node_modules", ".pyc", "REPORT.md", ".pdf",
                                       ".docx", "submission-a3-infra"))]
        unpacked = sum(i.file_size for i in z.infolist())

    print(f"files written : {len(names)}")
    print(f"uncompressed  : {unpacked/1024/1024:.2f} MB")
    print(f"archive       : {ZIP}  ({os.path.getsize(ZIP)/1024/1024:.2f} MB)")
    print(f"junk present  : {bad if bad else 'none'}")
    print("\ncontents:")
    for n in names:
        print("  " + n.replace(NAME + "/", ""))


if __name__ == "__main__":
    main()
