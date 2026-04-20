# Nature PDF to Word

Native macOS app for converting Nature PDFs into cleaned Word documents with a managed [MinerU](https://github.com/opendatalab/MinerU) backend.

## Platform support

- One-click app support in this repo is for macOS only
- Current target: Apple Silicon Macs running macOS 14+
- Windows and Linux would need separate builds or a cross-platform rewrite

## What it does

- Select multiple Nature PDFs in one batch
- Choose any export folder
- Convert one `.docx` per PDF
- Remove Nature publisher metadata such as repeated `Nature | www.nature.com` banners, DOI/date blocks, `Open access`, and `Check for updates`
- Preserve title, authors, affiliations, Methods, References, Acknowledgements, figure/table captions, and Extended Data pages
- Fall back to page images for image-only pages that do not produce usable text

## Architecture

- `Sources/NaturePDFToWord`: SwiftUI macOS app shell, queue state, file pickers, runtime orchestration
- `Sources/NaturePDFToWord/Resources/Backend/nature_pdf_backend.py`: managed Python backend that bootstraps MinerU, downloads Pandoc, submits parse tasks to `mineru-api`, cleans Nature-specific metadata, and exports DOCX
- `BackendTests/fixtures`: fixtures derived from the provided sample Nature PDF
- `.github/workflows`: CI and unsigned release packaging

## Build

```bash
swift build
```

## One-click open on macOS

Build and package the app:

```bash
scripts/package_app.sh
```

Then open either:

- `dist/Nature PDF to Word.app`
- `dist/NaturePDFToWord-arm64.zip` after unzipping it

The packaged app already includes Python 3.12, so end users do not need to install Python separately.

## Run tests

```bash
swift test
python3 -m unittest discover -s BackendTests
```

## Package the unsigned macOS app

```bash
scripts/package_app.sh
```

This builds the Swift executable, copies the SwiftPM resource bundle, stages embedded Python 3.12.10 from the official python.org macOS installer, and creates `dist/NaturePDFToWord-arm64.zip`.

After that, users can open:

- `dist/Nature PDF to Word.app` directly on macOS
- or unzip `dist/NaturePDFToWord-arm64.zip` and double-click the app

Python download happens only at package-build time. It is not downloaded again on the end user's machine when they launch the packaged app.

## First run behavior

The first launch of the packaged app prepares a managed runtime in:

`~/Library/Application Support/NaturePDFToWord/runtime`

That runtime will:

- create a private virtual environment from the bundled Python
- install pinned `MinerU[pipeline]` from `v3.1.0`
- download MinerU models
- download Pandoc `3.9.0.2` for Apple Silicon macOS
- start `mineru-api` on `127.0.0.1:50417`

After the first successful setup, the existing runtime is reused on later launches. MinerU models and Pandoc are not downloaded again unless the runtime is deleted or the pinned runtime manifest changes.

## Sample PDF fixture helper

Use the helper below to inspect or regenerate fixture excerpts from any local sample PDF:

```bash
python3 scripts/extract_sample_fixtures.py "/path/to/your/sample.pdf"
```

## Gatekeeper note

GitHub releases are intentionally unsigned in v1. Users will need to right-click the app in Finder and choose `Open` the first time they launch it.
