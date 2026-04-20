# Nature OA Downloader

Standalone utility for downloading the most recent open-access research papers from the journal *Nature* into a separate folder.

It uses Crossref metadata to find current `Nature` papers, keeps only recent research-style DOIs from the journal (`10.1038/s41586-...`), filters for Creative Commons licenses, and then downloads the linked PDFs into `output/pdfs/`.

## Run

```bash
python3 nature_oa_downloader/download_recent_nature_oa.py --email your_email@example.com
```

That downloads the most recent 100 OA *Nature* papers by default.

## Useful options

```bash
python3 nature_oa_downloader/download_recent_nature_oa.py --limit 25 --metadata-only
python3 nature_oa_downloader/download_recent_nature_oa.py --limit 100 --output-dir /path/to/output
python3 nature_oa_downloader/download_recent_nature_oa.py --delay 1.5 --email your_email@example.com
```

## Output

- `output/papers.json`
- `output/papers.csv`
- `output/download_urls.txt`
- `output/pdfs/*.pdf`

If a PDF request is blocked or interrupted, rerun the command. Existing files are skipped, so it resumes cleanly.
