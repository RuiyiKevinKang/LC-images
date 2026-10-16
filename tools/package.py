"""Package public source and copy it into the static site for download."""

from pathlib import Path
import argparse
import shutil
import zipfile


EXCLUDED_DIRECTORIES = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "node_modules", "test-results", "work", "vendor",
}
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db", "source.zip", "lc-mapping-lab-source.zip"}


def package(root: Path, destination: Path) -> None:
    root = root.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_files = sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.resolve() != destination
        and not any(part in EXCLUDED_DIRECTORIES for part in path.relative_to(root).parts)
        and path.name not in EXCLUDED_NAMES
        and path.suffix not in {".pyc", ".pyo", ".log"}
    )
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source_files:
            archive.write(path, arcname=Path(root.name) / path.relative_to(root))
    site_copy = root / "site" / "source.zip"
    site_copy.parent.mkdir(parents=True, exist_ok=True)
    if destination != site_copy.resolve():
        shutil.copyfile(destination, site_copy)
    print(f"Source archive: {destination}")
    print(f"Website download: {site_copy}")
    print(f"Files included: {len(source_files)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional destination ZIP path")
    arguments = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    package(project_root, arguments.output or project_root.parent / "lc-mapping-lab-source.zip")
