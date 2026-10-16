# Third-party runtime notices

LC Mapping Lab's own application code is distributed under the MIT license in
`LICENSE`. Third-party runtime files retain their upstream licenses; the MIT
license does not replace those licenses.

The browser loads these fixed versions from the same website origin:

| Component | Version | Upstream license / source |
| --- | --- | --- |
| Pyodide | 0.27.7 | MPL-2.0; [source at the release tag](https://github.com/pyodide/pyodide/tree/0.27.7) |
| CPython | 3.12.7 | Python Software Foundation License and included historical notices; [source and license](https://github.com/python/cpython/tree/v3.12.7) |
| NumPy | 2.0.2 | BSD-3-Clause and included third-party notices; [source](https://github.com/numpy/numpy/tree/v2.0.2) |
| micropip | 0.9.0 | MPL-2.0; [upstream project and source distribution](https://pypi.org/project/micropip/0.9.0/) |
| packaging | 24.2 | Apache-2.0 OR BSD-2-Clause; [source](https://github.com/pypa/packaging/tree/24.2) |
| openpyxl | 3.1.5 | MIT; [upstream project and source distribution](https://pypi.org/project/openpyxl/3.1.5/) |
| et_xmlfile | 2.0.0 | MIT; [upstream project and source distribution](https://pypi.org/project/et-xmlfile/2.0.0/) |

`tools/vendor_runtime.py` downloads the fixed versions from the official Pyodide
distribution and official PyPI metadata/file hosts. It preserves the original
wheels and extracts available license, notice, and package metadata files. The
browser runtime is served without modifications to these upstream files.

After that tool completes, the deployed runtime directory
`site/vendor/pyodide/` contains:

- `VENDOR-MANIFEST.json`: exact download sources, versions, SHA256 values,
  dependency closure, and paths to extracted upstream license materials.
- `LICENSE-PYODIDE.txt`: the upstream Pyodide license.
- `licenses/`: upstream license/notice texts and wheel metadata extracted from
  the downloaded archives.
- The original Python/NumPy/package archives, which retain their included
  notices and metadata.

Pyodide package wheel hashes are checked against its official
`pyodide-lock.json`; Excel package wheel hashes are checked against the
version-specific official PyPI JSON metadata. Runtime core hashes are recorded
for inspection; the upstream lockfile does not supply comparison hashes for the
core files.

The generated runtime directory is excluded from Git and from the downloadable
application source ZIP. Run `python tools/vendor_runtime.py` to reproduce it.
This repository includes the downloader and deployment workflow; the upstream
source links above identify the corresponding source code for the unmodified
third-party runtime. Consult the complete preserved license texts for their
terms and included third-party notices.
