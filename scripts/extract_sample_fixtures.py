#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract lightweight fixture text from a Nature sample PDF.")
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()

    reader = PdfReader(str(args.pdf))
    for page_number in [1, 22]:
        page = reader.pages[page_number - 1]
        print(f"--- PAGE {page_number} ---")
        print((page.extract_text() or "").strip())
        print()


if __name__ == "__main__":
    main()
