#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


APP_NAME = "NaturePDFToWord"
MINERU_RELEASE_TAG = "mineru-3.1.0-released"
# Pin the exact commit behind MinerU 3.1.0 so installer behavior does not depend on tag naming.
MINERU_GIT_REF = "d9cd58add047c2364c1198eefcb1ee9cd63a971a"
# Newer pip versions reject the legacy #egg=... extra syntax for VCS installs.
MINERU_PIP_SPEC = f"mineru[pipeline] @ git+https://github.com/opendatalab/MinerU.git@{MINERU_GIT_REF}"
PANDOC_VERSION = "3.9.0.2"
PANDOC_ZIP_URL = (
    f"https://github.com/jgm/pandoc/releases/download/{PANDOC_VERSION}/"
    f"pandoc-{PANDOC_VERSION}-arm64-macOS.zip"
)
API_PORT = 50417
API_BASE_URL = f"http://127.0.0.1:{API_PORT}"
MINERU_TASK_SUBMIT_TIMEOUT = (10, 300)
MINERU_TASK_STATUS_TIMEOUT = (10, 60)
MINERU_TASK_RESULT_TIMEOUT = (10, 300)
MINERU_TASK_POLL_INTERVAL_SECONDS = 1.5
MINERU_TASK_MAX_WAIT_SECONDS = 60 * 60
RUNTIME_MANIFEST = "runtime-manifest.json"
MINERU_MODEL_SOURCE_ENV_VAR = "NATURE_PDF_TO_WORD_MINERU_MODEL_SOURCE"
MINERU_MODEL_SOURCES = ("huggingface", "modelscope")
METADATA_LINE_PATTERNS = [
    re.compile(r"^https://doi\.org/10\.1038/\S+$", re.IGNORECASE),
    re.compile(r"^doi:\s*10\.1038/\S+$", re.IGNORECASE),
    re.compile(r"^received:\s*", re.IGNORECASE),
    re.compile(r"^accepted:\s*", re.IGNORECASE),
    re.compile(r"^published online:\s*", re.IGNORECASE),
    re.compile(r"^open access$", re.IGNORECASE),
    re.compile(r"^check for updates$", re.IGNORECASE),
    re.compile(r"^a list of affiliations appears at the end of the paper\.$", re.IGNORECASE),
    re.compile(r"^about this article$", re.IGNORECASE),
    re.compile(r"^reprints and permissions$", re.IGNORECASE),
    re.compile(r"^peer review$", re.IGNORECASE),
    re.compile(r"^publisher[’']?s note$", re.IGNORECASE),
]
BANNER_PATTERNS = [
    re.compile(r"^nature\s*\|\s*www\.nature\.com\s*\|\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^\d+\s*\|\s*nature\s*\|\s*www\.nature\.com\s*$", re.IGNORECASE),
]
SECTION_LOCK_PATTERNS = [
    re.compile(r"^methods$", re.IGNORECASE),
    re.compile(r"^references$", re.IGNORECASE),
    re.compile(r"^acknowledg", re.IGNORECASE),
    re.compile(r"^extended data", re.IGNORECASE),
]


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def run(command: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    quoted = " ".join(shlex.quote(part) for part in command)
    log(f"$ {quoted}")
    subprocess.run(command, check=True, env=env, cwd=cwd)


def pip_runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PIP_DEFAULT_TIMEOUT", "120")
    env.setdefault("PIP_RETRIES", "12")
    env.setdefault("PIP_PROGRESS_BAR", "off")
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    # HTTP/1.1 is generally more stable than HTTP/2 on flaky consumer networks for git clones.
    env.setdefault("GIT_HTTP_VERSION", "HTTP/1.1")
    return env


def clear_partial_runtime(layout: RuntimeLayout) -> None:
    log("Removing the partial managed runtime so setup can restart cleanly...")
    for path in [
        layout.venv_dir,
        layout.pandoc_dir,
        layout.api_pid_file,
        layout.runtime_manifest,
        layout.root / ".mineru-models-ready",
    ]:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)


def load_requests_module():
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - only during broken runtime bootstrap
        raise SystemExit("The managed runtime is missing the requests package. Run ensure-runtime first.") from exc
    return requests


def python_module_command(layout: RuntimeLayout, module: str, *arguments: str) -> list[str]:
    # macOS strips DYLD_* variables when a shell wrapper is the initial executable.
    # Run MinerU entry points via `python -m ...` so the embedded framework settings survive.
    return [str(layout.venv_python), "-m", module, *arguments]


@dataclass
class RuntimeLayout:
    root: Path

    @property
    def venv_dir(self) -> Path:
        return self.root / "venv"

    @property
    def venv_python(self) -> Path:
        return self.venv_dir / "bin" / "python3"

    @property
    def venv_pip(self) -> Path:
        return self.venv_dir / "bin" / "pip"

    @property
    def venv_bin(self) -> Path:
        return self.venv_dir / "bin"

    @property
    def tools_dir(self) -> Path:
        return self.root / "tools"

    @property
    def pandoc_dir(self) -> Path:
        return self.tools_dir / "pandoc"

    @property
    def pandoc_binary(self) -> Path:
        return self.pandoc_dir / "bin" / "pandoc"

    @property
    def downloads_dir(self) -> Path:
        return self.root / "downloads"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def api_pid_file(self) -> Path:
        return self.root / "mineru-api.pid"

    @property
    def api_log_file(self) -> Path:
        return self.logs_dir / "mineru-api.log"

    @property
    def runtime_manifest(self) -> Path:
        return self.root / RUNTIME_MANIFEST

    @property
    def mineru_config_file(self) -> Path:
        return self.root / "mineru.json"


@dataclass
class CleanMarkdownResult:
    markdown: str
    warnings: list[str]


def ensure_runtime(args: argparse.Namespace) -> None:
    layout = RuntimeLayout(Path(args.runtime_root).expanduser().resolve())
    for directory in [layout.root, layout.tools_dir, layout.downloads_dir, layout.work_dir, layout.logs_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    desired_manifest = {
        "mineru_release_tag": MINERU_RELEASE_TAG,
        "mineru_git_ref": MINERU_GIT_REF,
        "pandoc_version": PANDOC_VERSION,
    }

    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            ensure_runtime_impl(layout, desired_manifest)
            break
        except Exception:
            if attempt == attempts:
                raise
            log("Runtime setup failed during bootstrap. Retrying once with a clean runtime directory...")
            clear_partial_runtime(layout)

    print(json.dumps({"status": "ok", "runtimeRoot": str(layout.root)}))


def ensure_runtime_impl(layout: RuntimeLayout, desired_manifest: dict[str, str]) -> None:
    venv_created = ensure_runtime_virtualenv(layout)

    manifest = {}
    if layout.runtime_manifest.exists():
        manifest = json.loads(layout.runtime_manifest.read_text(encoding="utf-8"))

    if venv_created or manifest != desired_manifest:
        log("Installing pinned backend dependencies...")
        pip_env = pip_runtime_env()
        pip_install_with_retry(
            [
                str(layout.venv_python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip",
                "setuptools",
                "wheel",
            ],
            env=pip_env,
        )
        pip_install_with_retry(
            [
                str(layout.venv_python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                MINERU_PIP_SPEC,
                "requests>=2.32,<3",
                "pypdf>=5.0,<6",
            ],
            env=pip_env,
        )
        layout.runtime_manifest.write_text(json.dumps(desired_manifest, indent=2), encoding="utf-8")

    ensure_pandoc(layout)
    ensure_mineru_models(layout)


def ensure_runtime_virtualenv(layout: RuntimeLayout) -> bool:
    bootstrap_required = (
        not layout.venv_python.exists()
        or not layout.venv_pip.exists()
        or not (layout.venv_bin / "mineru-models-download").exists()
    )

    if not bootstrap_required:
        return False

    if layout.venv_dir.exists():
        log("Recreating the managed Python 3.12 virtual environment...")
        shutil.rmtree(layout.venv_dir, ignore_errors=True)
    else:
        log("Creating the managed Python 3.12 virtual environment...")

    venv.EnvBuilder(with_pip=True, clear=False, symlinks=True).create(layout.venv_dir)
    return True


def ensure_pandoc(layout: RuntimeLayout) -> None:
    if layout.pandoc_binary.exists():
        ensure_executable(layout.pandoc_binary)
        return

    archive_path = layout.downloads_dir / Path(PANDOC_ZIP_URL).name
    if not archive_path.exists():
        log(f"Downloading Pandoc {PANDOC_VERSION}...")
        urllib.request.urlretrieve(PANDOC_ZIP_URL, archive_path)

    extract_dir = layout.tools_dir / f"pandoc-{PANDOC_VERSION}"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)

    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(layout.tools_dir)

    extracted_root = find_pandoc_extract_root(layout)
    if extracted_root is None:
        raise RuntimeError("Pandoc download did not contain the expected binary.")

    if layout.pandoc_dir.exists():
        shutil.rmtree(layout.pandoc_dir)
    shutil.copytree(extracted_root, layout.pandoc_dir)
    ensure_executable(layout.pandoc_binary)
    log("Pandoc staged into the managed runtime.")


def find_pandoc_extract_root(layout: RuntimeLayout) -> Path | None:
    patterns = [
        f"pandoc-{PANDOC_VERSION}/bin/pandoc",
        f"pandoc-{PANDOC_VERSION}-*/bin/pandoc",
        "pandoc*/bin/pandoc",
    ]

    for pattern in patterns:
        for candidate in sorted(layout.tools_dir.glob(pattern)):
            if candidate.is_file():
                return candidate.parent.parent

    for candidate in sorted(layout.tools_dir.rglob("pandoc")):
        if candidate.is_file() and candidate.parent.name == "bin":
            return candidate.parent.parent

    return None


def ensure_executable(path: Path) -> None:
    mode = path.stat().st_mode
    executable_bits = 0

    if mode & stat.S_IRUSR:
        executable_bits |= stat.S_IXUSR
    if mode & stat.S_IRGRP:
        executable_bits |= stat.S_IXGRP
    if mode & stat.S_IROTH:
        executable_bits |= stat.S_IXOTH

    if executable_bits and mode & executable_bits != executable_bits:
        path.chmod(mode | executable_bits)


def ensure_mineru_models(layout: RuntimeLayout) -> None:
    sentinel = layout.root / ".mineru-models-ready"
    if sentinel.exists():
        return

    if not layout.venv_python.exists():
        raise RuntimeError("MinerU model downloader is missing from the managed runtime.")

    runtime_env = mineru_runtime_env(layout)
    last_error: subprocess.CalledProcessError | None = None

    for index, source in enumerate(mineru_model_source_candidates()):
        log(f"Downloading MinerU pipeline models from {source}...")
        try:
            run(
                python_module_command(layout, "mineru.cli.models_download", "-s", source, "-m", "pipeline"),
                env=runtime_env,
            )
            sentinel.write_text("ready\n", encoding="utf-8")
            return
        except subprocess.CalledProcessError as error:
            last_error = error
            if index == len(MINERU_MODEL_SOURCES) - 1:
                break
            log(f"MinerU model download from {source} failed. Retrying with the alternate source...")

    assert last_error is not None
    raise last_error


def mineru_runtime_env(layout: RuntimeLayout) -> dict[str, str]:
    env = os.environ.copy()
    env["MINERU_MODEL_SOURCE"] = "local"
    env["MINERU_TOOLS_CONFIG_JSON"] = str(layout.mineru_config_file)
    return env


def mineru_model_source_candidates(environment: dict[str, str] | None = None) -> list[str]:
    env = environment or os.environ
    preferred = env.get(MINERU_MODEL_SOURCE_ENV_VAR, "").strip().lower()

    candidates: list[str] = []
    if preferred in MINERU_MODEL_SOURCES:
        candidates.append(preferred)

    for source in MINERU_MODEL_SOURCES:
        if source not in candidates:
            candidates.append(source)

    return candidates


def pip_install_with_retry(command: list[str], env: dict[str, str], attempts: int = 3) -> None:
    last_error: subprocess.CalledProcessError | None = None
    for attempt in range(1, attempts + 1):
        try:
            run(command, env=env)
            return
        except subprocess.CalledProcessError as error:
            last_error = error
            if attempt == attempts:
                break
            wait_seconds = attempt * 5
            log(f"pip/bootstrap command failed on attempt {attempt}/{attempts}. Retrying in {wait_seconds} seconds...")
            time.sleep(wait_seconds)
    assert last_error is not None
    raise last_error


def healthcheck() -> bool:
    requests = load_requests_module()
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=2)
    except Exception:
        return False
    return response.ok


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def ensure_api_server(layout: RuntimeLayout) -> None:
    if healthcheck():
        return

    if layout.api_pid_file.exists():
        stale_pid = int(layout.api_pid_file.read_text(encoding="utf-8").strip())
        if not pid_is_running(stale_pid):
            layout.api_pid_file.unlink(missing_ok=True)

    if layout.api_pid_file.exists() and healthcheck():
        return

    log("Starting local mineru-api...")
    api_output_root = layout.work_dir / "mineru-output"
    api_output_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MINERU_API_OUTPUT_ROOT"] = str(api_output_root)
    env["MINERU_API_DISABLE_ACCESS_LOG"] = "1"
    env["MINERU_API_SHUTDOWN_ON_STDIN_EOF"] = "0"
    env.update(mineru_runtime_env(layout))

    with layout.api_log_file.open("ab") as handle:
        process = subprocess.Popen(
            python_module_command(layout, "mineru.cli.fast_api", "--host", "127.0.0.1", "--port", str(API_PORT)),
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

    layout.api_pid_file.write_text(f"{process.pid}\n", encoding="utf-8")

    for _ in range(120):
        if healthcheck():
            return
        time.sleep(1)

    log_tail = ""
    if layout.api_log_file.exists():
        log_tail = tail_text(layout.api_log_file)

    if process.poll() is not None:
        layout.api_pid_file.unlink(missing_ok=True)

    if log_tail:
        raise RuntimeError(f"mineru-api did not become healthy within 120 seconds.\n{log_tail}")
    raise RuntimeError("mineru-api did not become healthy within 120 seconds.")


def tail_text(path: Path, lines: int = 40) -> str:
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not content:
        return ""
    return "\n".join(content[-lines:])


def convert_file(args: argparse.Namespace) -> None:
    layout = RuntimeLayout(Path(args.runtime_root).expanduser().resolve())
    ensure_api_server(layout)

    requests = load_requests_module()

    input_pdf = Path(args.input).expanduser().resolve()
    output_docx = Path(args.output_path).expanduser().resolve()
    output_docx.parent.mkdir(parents=True, exist_ok=True)

    working_dir = layout.work_dir / input_pdf.stem / time.strftime("%Y%m%d-%H%M%S")
    raw_dir = working_dir / "raw"
    normalized_dir = working_dir / "normalized"
    raw_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)

    task_id = submit_mineru_task(requests, input_pdf)
    archive_path = download_task_archive(requests, task_id, raw_dir / f"{task_id}.zip", layout)
    extract_dir = raw_dir / "extracted"
    extract_zip(archive_path, extract_dir)

    markdown_path, content_list_path, middle_json_path = locate_parse_outputs(extract_dir)
    markdown = markdown_path.read_text(encoding="utf-8") if markdown_path else ""
    content_list = load_json_if_exists(content_list_path)
    middle_json = load_json_if_exists(middle_json_path)

    clean_result = clean_nature_markdown(markdown, content_list, middle_json)

    fallback_pages = detect_image_only_pages(input_pdf)
    fallback_images = {}
    if fallback_pages:
        fallback_dir = normalized_dir / "fallback-pages"
        fallback_images = render_page_fallbacks(input_pdf, fallback_pages, fallback_dir)
        if fallback_images:
            clean_result.markdown = append_image_fallback_sections(clean_result.markdown, fallback_images, normalized_dir)
            for warning in ["usedImageFallback", "emptyTextPageRecovered"]:
                if warning not in clean_result.warnings:
                    clean_result.warnings.append(warning)

    normalized_markdown_path = normalized_dir / "document.md"
    normalized_markdown_path.write_text(clean_result.markdown, encoding="utf-8")

    resource_roots = build_resource_roots(normalized_dir, extract_dir, markdown_path)
    convert_markdown_to_docx(layout, normalized_markdown_path, output_docx, resource_roots)

    result = {
        "sourcePdfPath": str(input_pdf),
        "status": "succeeded",
        "outputDocxPath": str(output_docx),
        "warnings": clean_result.warnings,
        "message": f"Converted with {len(clean_result.warnings)} warning(s)." if clean_result.warnings else "Converted successfully.",
    }
    print(json.dumps(result))


def submit_mineru_task(requests, input_pdf: Path) -> str:
    with input_pdf.open("rb") as handle:
        response = requests.post(
            f"{API_BASE_URL}/tasks",
            files={"files": (input_pdf.name, handle, "application/pdf")},
            data={
                "backend": "pipeline",
                "parse_method": "auto",
                "return_md": "true",
                "return_middle_json": "true",
                "return_content_list": "true",
                "return_images": "true",
                "response_format_zip": "true",
            },
            timeout=MINERU_TASK_SUBMIT_TIMEOUT,
        )
    response.raise_for_status()
    payload = response.json()
    task_id = payload.get("task_id")
    if not task_id:
        raise RuntimeError(f"mineru-api did not return a task_id: {payload}")
    log(f"Submitted MinerU task {task_id} for {input_pdf.name}.")
    return task_id


def download_task_archive(
    requests,
    task_id: str,
    target: Path,
    layout: RuntimeLayout | None = None,
) -> Path:
    status_url = f"{API_BASE_URL}/tasks/{task_id}"
    result_url = f"{API_BASE_URL}/tasks/{task_id}/result"
    deadline = time.monotonic() + MINERU_TASK_MAX_WAIT_SECONDS
    read_timeout_type = requests.exceptions.ReadTimeout

    while True:
        try:
            response = requests.get(status_url, timeout=MINERU_TASK_STATUS_TIMEOUT)
        except read_timeout_type:
            remaining_seconds = max(0, int(deadline - time.monotonic()))
            log(
                f"Task {task_id} status request timed out after "
                f"{MINERU_TASK_STATUS_TIMEOUT[1]} seconds; assuming processing continues. "
                f"Time remaining before abort: {remaining_seconds}s."
            )
            if time.monotonic() >= deadline:
                raise RuntimeError(task_timeout_message(task_id, layout))
            time.sleep(MINERU_TASK_POLL_INTERVAL_SECONDS)
            continue

        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        log(f"Task {task_id} status: {status}")
        if status == "completed":
            archive = requests.get(result_url, timeout=MINERU_TASK_RESULT_TIMEOUT)
            archive.raise_for_status()
            target.write_bytes(archive.content)
            return target
        if status == "failed":
            raise RuntimeError(f"mineru-api task {task_id} failed: {payload}")
        if time.monotonic() >= deadline:
            raise RuntimeError(task_timeout_message(task_id, layout))
        time.sleep(MINERU_TASK_POLL_INTERVAL_SECONDS)


def task_timeout_message(task_id: str, layout: RuntimeLayout | None = None) -> str:
    message = f"mineru-api task {task_id} did not finish within {MINERU_TASK_MAX_WAIT_SECONDS} seconds."
    if layout and layout.api_log_file.exists():
        log_tail = tail_text(layout.api_log_file)
        if log_tail:
            message = f"{message}\n{log_tail}"
    return message


def extract_zip(archive_path: Path, extract_dir: Path) -> None:
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extract_dir)


def locate_parse_outputs(extract_dir: Path) -> tuple[Path | None, Path | None, Path | None]:
    markdown = next(iter(sorted(extract_dir.rglob("*.md"))), None)
    content_list = next(iter(sorted(extract_dir.rglob("*_content_list.json"))), None)
    middle_json = next(iter(sorted(extract_dir.rglob("*_middle.json"))), None)
    return markdown, content_list, middle_json


def build_resource_roots(normalized_dir: Path, extract_dir: Path, markdown_path: Path | None) -> list[str]:
    roots = [normalized_dir]
    if markdown_path is not None:
        roots.append(markdown_path.parent)
    roots.append(extract_dir)

    unique_roots: list[str] = []
    seen: set[str] = set()
    for root in roots:
        resolved = str(root.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_roots.append(resolved)
    return unique_roots


def load_json_if_exists(path: Path | None) -> Any:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def clean_nature_markdown(markdown: str, content_list: Any, middle_json: Any) -> CleanMarkdownResult:
    repeated_fragments = collect_repeated_fragments(content_list) | collect_repeated_fragments(middle_json)
    warnings: list[str] = []
    locked_lines = lock_critical_lines(markdown)
    lines = markdown.splitlines()
    cleaned_lines: list[str] = []
    skipping_metadata_block = False
    publisher_metadata_removed = False

    for raw_line in lines:
        line = raw_line.rstrip()
        normalized = normalize_fragment(line)

        if skipping_metadata_block:
            if not normalized:
                skipping_metadata_block = False
                continue
            if is_metadata_line(line):
                publisher_metadata_removed = True
                continue
            skipping_metadata_block = False

        if normalized in locked_lines:
            cleaned_lines.append(line)
            continue

        if should_drop_banner(line, normalized, repeated_fragments):
            publisher_metadata_removed = True
            continue

        if is_doi_block_start(line):
            publisher_metadata_removed = True
            skipping_metadata_block = True
            continue

        if is_metadata_line(line):
            publisher_metadata_removed = True
            continue

        cleaned_lines.append(line)

    cleaned_text = normalize_markdown("\n".join(cleaned_lines))
    if publisher_metadata_removed:
        warnings.append("publisherMetadataRemoved")

    return CleanMarkdownResult(markdown=cleaned_text, warnings=warnings)


def normalize_fragment(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip().lower()
    collapsed = re.sub(r"\b\d+\b", "#", collapsed)
    return collapsed


def lock_critical_lines(markdown: str) -> set[str]:
    locked: set[str] = set()
    for line in markdown.splitlines():
        normalized = normalize_fragment(line)
        if not normalized:
            if locked:
                break
            continue
        if any(pattern.search(line) for pattern in BANNER_PATTERNS):
            continue
        if normalized == "article":
            continue
        if is_doi_block_start(line) or is_metadata_line(line):
            break
        locked.add(normalized)
        if len(locked) >= 6:
            break
    return locked


def should_drop_banner(line: str, normalized: str, repeated_fragments: set[str]) -> bool:
    if any(pattern.search(line) for pattern in BANNER_PATTERNS):
        return True
    if normalized == "article":
        return True
    if normalized in repeated_fragments and ("nature" in normalized or "www.nature.com" in normalized or normalized == "article"):
        return True
    return False


def is_doi_block_start(line: str) -> bool:
    return bool(re.search(r"(https://doi\.org/10\.1038/|doi:\s*10\.1038/)", line, flags=re.IGNORECASE))


def is_metadata_line(line: str) -> bool:
    for pattern in METADATA_LINE_PATTERNS:
        if pattern.search(line.strip()):
            return True
    return False


def normalize_markdown(markdown: str) -> str:
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    return markdown.strip() + "\n"


def collect_repeated_fragments(document: Any) -> set[str]:
    if document is None:
        return set()

    counts: dict[str, set[int]] = {}
    for block in iter_text_blocks(document):
        text = normalize_fragment(block["text"])
        if not text or len(text) < 6:
            continue
        counts.setdefault(text, set()).add(block["page"])

    repeated = {text for text, pages in counts.items() if len(pages) >= 2}
    return repeated


def iter_text_blocks(node: Any, page_hint: int = 0) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        page = extract_page_number(node) or page_hint
        text_value = None
        for key in ("text", "content", "raw_text", "value", "md"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                text_value = value
                break
        if text_value:
            yield {"page": page, "text": text_value}

        for value in node.values():
            yield from iter_text_blocks(value, page)
    elif isinstance(node, list):
        for item in node:
            yield from iter_text_blocks(item, page_hint)


def extract_page_number(node: dict[str, Any]) -> int | None:
    for key in ("page_idx", "page_index", "page_no", "page_num", "page_number", "page"):
        value = node.get(key)
        if isinstance(value, int):
            return value + 1 if key == "page_idx" else value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def detect_image_only_pages(pdf_path: Path) -> list[int]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages: list[int] = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            pages.append(index)
    return pages


def render_page_fallbacks(pdf_path: Path, page_numbers: list[int], destination: Path) -> dict[int, Path]:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return {}

    destination.mkdir(parents=True, exist_ok=True)
    rendered: dict[int, Path] = {}
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for page_number in page_numbers:
            page = document.get_page(page_number - 1)
            try:
                bitmap = page.render(scale=2.0)
                image = bitmap.to_pil()
                image_path = destination / f"page-{page_number:02d}.png"
                image.save(image_path)
                rendered[page_number] = image_path
            finally:
                page.close()
    finally:
        document.close()
    return rendered


def append_image_fallback_sections(markdown: str, image_paths: dict[int, Path], root_dir: Path) -> str:
    sections = [markdown.rstrip(), "", "# Image-only pages", ""]
    for page_number in sorted(image_paths):
        relative_path = image_paths[page_number].relative_to(root_dir)
        sections.append(f"## Page {page_number}")
        sections.append("")
        sections.append(f"![Page {page_number}]({relative_path.as_posix()})")
        sections.append("")
    return "\n".join(sections).strip() + "\n"


def convert_markdown_to_docx(layout: RuntimeLayout, markdown_path: Path, output_docx: Path, resource_roots: list[str]) -> None:
    command = [
        str(layout.pandoc_binary),
        "--from",
        "gfm+tex_math_dollars+pipe_tables",
        "--to",
        "docx",
        "--standalone",
        "--wrap=none",
        "--resource-path",
        os.pathsep.join(resource_roots),
        "--output",
        str(output_docx),
        str(markdown_path),
    ]
    run(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Managed MinerU backend for Nature PDF to Word.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure = subparsers.add_parser("ensure-runtime", help="Create the managed runtime and download dependencies.")
    ensure.add_argument("--runtime-root", required=True)
    ensure.set_defaults(func=ensure_runtime)

    convert = subparsers.add_parser("convert", help="Convert one PDF into a cleaned DOCX.")
    convert.add_argument("--runtime-root", required=True)
    convert.add_argument("--input", required=True)
    convert.add_argument("--output-path", required=True)
    convert.add_argument("--backend", default="pipeline")
    convert.add_argument("--cleanup-profile", default="nature_metadata_only")
    convert.set_defaults(func=convert_file)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as error:
        message = f"Command failed with exit code {error.returncode}: {' '.join(error.cmd)}"
        log(message)
        print(
            json.dumps(
                {
                    "sourcePdfPath": "",
                    "status": "failed",
                    "outputDocxPath": None,
                    "warnings": ["parseFailed"],
                    "message": message,
                }
            )
        )
        raise SystemExit(error.returncode) from error
    except KeyboardInterrupt:
        raise SystemExit(signal.SIGTERM)
    except Exception as error:
        log(str(error))
        print(
            json.dumps(
                {
                    "sourcePdfPath": "",
                    "status": "failed",
                    "outputDocxPath": None,
                    "warnings": ["parseFailed"],
                    "message": str(error),
                }
            )
        )
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
