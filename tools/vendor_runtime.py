"""Vendor the pinned minimal Pyodide runtime, NumPy and Excel support.

Run with Python 3.10+ and normal HTTPS network access. The downloader only fetches
runtime files from the official Pyodide v0.27.7 jsDelivr distribution, and pinned
pure Python Excel wheels from the official PyPI JSON API and its file host. It obtains
the upstream Pyodide license from its official source repository if the runtime
distribution does not expose a LICENSE file. It never downloads the full bundle.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import email
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time
import urllib.error
import urllib.request
import zipfile


VERSION = "0.27.7"
BASE = f"https://cdn.jsdelivr.net/pyodide/v{VERSION}/full/"
CORE_FILES = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip")
ROOT_PACKAGES = ("numpy", "micropip")
PYPI_PACKAGES = {"openpyxl": "3.1.5", "et_xmlfile": "2.0.0"}
LICENSE_SOURCES = (
    BASE + "LICENSE",
    "https://raw.githubusercontent.com/pyodide/pyodide/0.27.7/LICENSE",
)


def safe_filename(name: str) -> str:
    if not name or PurePosixPath(name).name != name or "\\" in name or name in (".", ".."):
        raise ValueError(f"Unsafe distribution filename: {name!r}")
    return name


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_content(path: Path) -> None:
    with path.open("rb") as stream:
        first = stream.read(512)
    if path.name.endswith(".wasm") and first[:4] != b"\0asm":
        raise ValueError(f"Not a WebAssembly module: {path.name}")
    if path.name.endswith((".whl", ".zip")):
        if first[:4] not in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
            raise ValueError(f"Not a ZIP/wheel archive: {path.name}")
        with zipfile.ZipFile(path) as archive:
            if not archive.namelist():
                raise ValueError(f"Empty archive: {path.name}")
            if archive.testzip() is not None:
                raise ValueError(f"Corrupt archive: {path.name}")
    if path.name.endswith(".js"):
        if len(first) < 100 or first.lstrip().startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
            raise ValueError(f"Not JavaScript: {path.name}")
    if path.name.endswith(".json"):
        json.loads(path.read_text(encoding="utf-8"))


def fetch(url: str, destination: Path, expected_hash: str | None = None, hash_source: str | None = None) -> dict:
    if destination.is_file():
        try:
            digest = sha256(destination)
            if expected_hash and digest.lower() != expected_hash.lower():
                raise ValueError("cached SHA256 mismatch")
            verify_content(destination)
            print(f"CACHE {destination.name}: {destination.stat().st_size:,} bytes", flush=True)
            return {
                "file": destination.name, "url": url, "bytes": destination.stat().st_size,
                "sha256": digest,
                "verified_against_lockfile": bool(expected_hash) and hash_source == "pyodide-lock.json",
                "hash_source": hash_source if expected_hash else None,
                "cache_used": True,
            }
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            print(f"CACHE INVALID {destination.name}: {error}", flush=True)
    temporary = destination.with_name(destination.name + ".part")
    last_error = None
    for attempt in range(3):
        try:
            print(f"GET {destination.name}, attempt {attempt + 1}/3", flush=True)
            request = urllib.request.Request(url, headers={"User-Agent": "LC-Mapping-Lab vendor downloader/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as stream:
                content_type = response.headers.get("Content-Type", "")
                if "text/html" in content_type and destination.suffix not in (".html",):
                    raise ValueError(f"Unexpected HTML response: {url}")
                for chunk in iter(lambda: response.read(64 * 1024), b""):
                    stream.write(chunk)
            digest = sha256(temporary)
            if expected_hash and digest.lower() != expected_hash.lower():
                raise ValueError(f"SHA256 mismatch for {destination.name}: {digest} != {expected_hash}")
            temporary.replace(destination)
            verify_content(destination)
            result = {
                "file": destination.name,
                "url": url,
                "bytes": destination.stat().st_size,
                "sha256": digest,
                "verified_against_lockfile": bool(expected_hash) and hash_source == "pyodide-lock.json",
                "hash_source": hash_source if expected_hash else None,
            }
            print(f"OK {destination.name}: {result['bytes']:,} bytes" + (f" [SHA256 verified: {hash_source}]" if expected_hash else ""), flush=True)
            return result
        except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile) as error:
            last_error = error
            print(f"RETRY {destination.name}, attempt {attempt + 1}/3 failed: {type(error).__name__}: {error}", flush=True)
            if temporary.exists():
                temporary.unlink()
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Failed to download {url}: {last_error}") from last_error


def package_closure(lock: dict) -> dict[str, dict]:
    packages = lock["packages"]
    normalized = {re.sub(r"[-_.]+", "-", key).lower(): key for key in packages}
    selected = {}

    def visit(requested: str) -> None:
        key = normalized.get(re.sub(r"[-_.]+", "-", requested).lower())
        if key is None:
            raise ValueError(f"Dependency {requested!r} is absent from the official lockfile")
        if key in selected:
            return
        package = packages[key]
        selected[key] = package
        for dependency in package.get("depends", []):
            visit(dependency)

    for root in ROOT_PACKAGES:
        visit(root)
    return selected


def extract_license_materials(package_name: str, archive_path: Path, license_root: Path) -> list[str]:
    if not zipfile.is_zipfile(archive_path):
        return []
    extracted = []
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if entry.is_dir() or path.is_absolute() or ".." in path.parts:
                continue
            basename = path.name.upper()
            license_file = basename.startswith(("LICENSE", "COPYING", "NOTICE"))
            metadata_file = basename == "METADATA" and ".dist-info/" in entry.filename
            if not (license_file or metadata_file):
                continue
            if entry.file_size > 2_000_000:
                raise ValueError(f"Unexpectedly large license entry: {entry.filename}")
            output = license_root / package_name / Path(*path.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(archive.read(entry))
            extracted.append(str(output.relative_to(license_root.parent)).replace("\\", "/"))
    return extracted


def describe_wheel(path: Path) -> dict:
    if not zipfile.is_zipfile(path):
        return {}
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if not names:
            return {}
        message = email.message_from_bytes(archive.read(names[0]))
        return {key: message.get(key) for key in ("Name", "Version", "License", "License-Expression", "Home-page") if message.get(key)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", type=Path, default=Path(__file__).resolve().parent.parent / "site")
    args = parser.parse_args()
    vendor = args.site_root.resolve() / "vendor/pyodide"
    vendor.mkdir(parents=True, exist_ok=True)
    lock_result = fetch(BASE + "pyodide-lock.json", vendor / "pyodide-lock.json")
    lock = json.loads((vendor / "pyodide-lock.json").read_text(encoding="utf-8"))
    reported_version = lock.get("info", {}).get("version")
    if reported_version is not None and str(reported_version) != VERSION:
        raise ValueError(f"Unexpected lockfile version: {reported_version}")
    selected = package_closure(lock)
    print("Lockfile dependency closure: " + ", ".join(sorted(selected)), flush=True)
    tasks = [(name, None) for name in CORE_FILES]
    for package_name, package in sorted(selected.items()):
        filename = safe_filename(package["file_name"])
        digest = package.get("sha256")
        if not digest or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError(f"Missing/invalid official SHA256 for {package_name}")
        tasks.append((filename, digest))
    results = [lock_result]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch, BASE + name, vendor / name, digest, "pyodide-lock.json" if digest else None): name for name, digest in tasks}
        for future in as_completed(futures):
            results.append(future.result())

    pypi_packages = {}
    for package_name, version in PYPI_PACKAGES.items():
        metadata_name = f"pypi-{package_name}-{version}.json"
        metadata_url = f"https://pypi.org/pypi/{package_name}/{version}/json"
        metadata_result = fetch(metadata_url, vendor / metadata_name)
        results.append(metadata_result)
        metadata = json.loads((vendor / metadata_name).read_text(encoding="utf-8"))
        if metadata["info"]["version"] != version:
            raise ValueError(f"Unexpected PyPI version for {package_name}")
        candidates = [item for item in metadata["urls"] if item["packagetype"] == "bdist_wheel" and item["filename"].endswith("-none-any.whl") and "py3" in item["filename"].split("-")[-3]]
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one pure Python 3 wheel for {package_name}, found {len(candidates)}")
        wheel = candidates[0]
        filename = safe_filename(wheel["filename"])
        if not wheel["url"].startswith("https://files.pythonhosted.org/"):
            raise ValueError(f"Unexpected official PyPI file host: {wheel['url']}")
        digest = wheel["digests"]["sha256"]
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError(f"Invalid PyPI SHA256 for {package_name}")
        results.append(fetch(wheel["url"], vendor / filename, digest, metadata_url))
        pypi_packages[package_name] = {
            "version": version, "filename": filename, "sha256": digest,
            "source_metadata": metadata_url, "requires_dist": metadata["info"].get("requires_dist"),
            "wheel_metadata": describe_wheel(vendor / filename),
        }

    license_result = None
    for url in LICENSE_SOURCES:
        try:
            license_result = fetch(url, vendor / "LICENSE-PYODIDE.txt")
            text = (vendor / "LICENSE-PYODIDE.txt").read_text(encoding="utf-8")
            if "Mozilla Public License" not in text:
                raise ValueError("Unexpected official Pyodide license content")
            break
        except RuntimeError:
            continue
    if license_result is None:
        raise RuntimeError("Could not obtain the official Pyodide license; vendoring incomplete")
    results.append(license_result)
    python_version = str(lock["info"]["python"])
    if not re.fullmatch(r"\d+\.\d+\.\d+", python_version):
        raise ValueError(f"Unexpected CPython version: {python_version!r}")
    python_license = fetch(
        f"https://raw.githubusercontent.com/python/cpython/v{python_version}/LICENSE",
        vendor / "LICENSE-PYTHON.txt",
    )
    if "PYTHON SOFTWARE FOUNDATION LICENSE" not in (vendor / "LICENSE-PYTHON.txt").read_text(encoding="utf-8"):
        raise ValueError("Unexpected official CPython license content")
    results.append(python_license)

    license_root = vendor / "licenses"
    materials = {"python_stdlib": extract_license_materials("python_stdlib", vendor / "python_stdlib.zip", license_root)}
    package_details = {}
    for name, package in sorted(selected.items()):
        path = vendor / package["file_name"]
        materials[name] = extract_license_materials(name, path, license_root)
        package_details[name] = {
            "version": package.get("version"), "filename": package["file_name"],
            "depends": package.get("depends", []),
            "upstream_lock_metadata": {key: value for key, value in package.items() if "license" in key.lower()},
            "wheel_metadata": describe_wheel(path),
            "license_materials": materials[name],
        }
    for name, package in sorted(pypi_packages.items()):
        materials[name] = extract_license_materials(name, vendor / package["filename"], license_root)
        package["license_materials"] = materials[name]
    manifest = {
        "pyodide_version": VERSION,
        "official_distribution": BASE,
        "requested_packages": list(ROOT_PACKAGES),
        "package_dependency_closure": package_details,
        "pypi_excel_packages": pypi_packages,
        "files": sorted(results, key=lambda item: item["file"]),
        "upstream_license_materials": materials,
        "total_download_bytes": sum(result["bytes"] for result in results),
        "local_index_url": "new URL('./vendor/pyodide/', self.location.href).href",
    }
    (vendor / "VENDOR-MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    notes = [
        "# Vendored Pyodide runtime", "",
        f"Pinned version: {VERSION}. Official distribution: {BASE}", "",
        "Only core runtime files, the transitive dependency closure of NumPy/micropip, and two pinned pure Python Excel wheels are included.",
        "NumPy/micropip packages were checked against SHA256 in the unchanged upstream pyodide-lock.json.",
        "openpyxl 3.1.5 and et_xmlfile 2.0.0 were checked against SHA256 from the official version-pinned PyPI JSON API; its metadata is preserved.",
        "Core file hashes record the downloaded bytes; the upstream lockfile does not provide hashes for core runtime files.", "",
        "Pyodide is distributed under the Mozilla Public License 2.0; see LICENSE-PYODIDE.txt.",
        f"CPython {python_version} license and notices are preserved in LICENSE-PYTHON.txt from the matching official CPython source tag.",
        "Individual packages retain their own upstream licenses. License texts and wheel metadata are preserved in licenses/ and in the original wheels.",
        "See VENDOR-MANIFEST.json for exact sources, hashes, package versions, dependency closure and license metadata.", "",
        "In worker.js load vendor/pyodide/pyodide.js from this site, then pass the absolute directory URL with a trailing slash to loadPyodide({indexURL}).",
        "Use new URL('./vendor/pyodide/', self.location.href).href so deployments under repository subpaths work.",
        "Serve .wasm as application/wasm. Call loadPackage(['numpy', 'micropip']), then install the same-origin openpyxl and et_xmlfile wheels with micropip.install([...], deps=False).", "",
    ]
    for name, info in package_details.items():
        metadata = info["wheel_metadata"]
        license_name = metadata.get("License-Expression") or metadata.get("License") or "See preserved upstream archive and metadata"
        notes.append(f"- {name} {info['version']}: {license_name}")
    for name, info in sorted(pypi_packages.items()):
        metadata = info["wheel_metadata"]
        license_name = metadata.get("License-Expression") or metadata.get("License") or "See preserved upstream archive and metadata"
        notes.append(f"- {name} {info['version']}: {license_name}")
    (vendor / "README.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    on_disk = sum(path.stat().st_size for path in vendor.rglob("*") if path.is_file())
    print(json.dumps({"vendor_directory": str(vendor), "packages": sorted(selected), "pypi_excel_wheels": {name: info['filename'] for name, info in pypi_packages.items()}, "download_bytes": manifest["total_download_bytes"], "total_bytes_on_disk": on_disk, "MiB_on_disk": round(on_disk / 1024 / 1024, 2)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
