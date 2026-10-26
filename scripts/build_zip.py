from pathlib import Path
import hashlib
import tomllib
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION=tomllib.loads((ROOT/"pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
OUT=ROOT/"dist"/f"agenttxn-v{VERSION}-source.zip"
SKIP={".git","__pycache__",".pytest_cache",".ruff_cache","dist","build","validation"}
DIRECTORIES={"src","tests","examples","docs","scripts",".github","deploy"}
FILES={"README.md","CHANGELOG.md","LICENSE","pyproject.toml","CONTRIBUTING.md",
       "SECURITY.md","Makefile",".gitignore",".dockerignore",f"交接说明_{VERSION}.md"}

def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(OUT,"w",zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file():
                continue
            relative=path.relative_to(ROOT)
            if relative.parts[0] not in DIRECTORIES and str(relative) not in FILES and not (len(relative.parts)==1 and path.name.endswith("_RESULTS.txt")):
                continue
            if any(part in SKIP or part.startswith((".venv","venv")) or part.endswith(".egg-info") for part in relative.parts):
                continue
            if path.suffix in {".pyc",".db",".sqlite",".sqlite3",".pem",".key"} or path.name.endswith((".db-wal",".db-shm",".demo.json")) or path.name.startswith(".env"):
                continue
            archive.write(path,Path(f"agenttxn-v{VERSION}")/relative)
    digest=hashlib.sha256(OUT.read_bytes()).hexdigest()
    OUT.with_suffix(".zip.sha256").write_text(f"{digest}  {OUT.name}\n",encoding="utf-8")
    assert hashlib.sha256(OUT.read_bytes()).hexdigest()==digest
    print(f"Created {OUT}")
    print(f"SHA-256 verified: {digest}")

if __name__=="__main__":
    main()
