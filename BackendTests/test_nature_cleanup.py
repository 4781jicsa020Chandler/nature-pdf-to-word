from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "Sources" / "NaturePDFToWord" / "Resources" / "Backend" / "nature_pdf_backend.py"
FIXTURES = ROOT / "BackendTests" / "fixtures"


def load_backend_module():
    spec = importlib.util.spec_from_file_location("nature_pdf_backend", BACKEND)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backend = load_backend_module()


class NatureCleanupTests(unittest.TestCase):
    def test_first_page_cleanup_removes_publisher_block(self):
        markdown = (FIXTURES / "first_page_excerpt.md").read_text(encoding="utf-8")
        content_list = json.loads((FIXTURES / "repeated_fragments_content_list.json").read_text(encoding="utf-8"))

        result = backend.clean_nature_markdown(markdown, content_list, None)

        self.assertIn("Linear RAG scanning mediates editing of Igκ", result.markdown)
        self.assertIn("Adam Yongxin Ye", result.markdown)
        self.assertNotIn("Nature | www.nature.com | 1", result.markdown)
        self.assertNotIn("https://doi.org/10.1038/s41586-026-10362-5", result.markdown)
        self.assertNotIn("Open access", result.markdown)
        self.assertIn("publisherMetadataRemoved", result.warnings)

    def test_extended_data_headings_are_preserved(self):
        markdown = (FIXTURES / "extended_data_excerpt.md").read_text(encoding="utf-8")
        result = backend.clean_nature_markdown(markdown, None, None)

        self.assertIn("Extended Data Fig. 7", result.markdown)
        self.assertIn("linear RAG scanning", result.markdown)
        self.assertNotIn("\nArticle\n", result.markdown)

    def test_repeated_fragment_detection_finds_nature_banner(self):
        content_list = json.loads((FIXTURES / "repeated_fragments_content_list.json").read_text(encoding="utf-8"))
        repeated = backend.collect_repeated_fragments(content_list)

        self.assertIn("nature | www.nature.com | #", repeated)

    def test_build_resource_roots_includes_original_markdown_directory(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            normalized_dir = root / "normalized"
            extract_dir = root / "raw" / "extracted"
            markdown_dir = extract_dir / "Linear RAG" / "auto"
            normalized_dir.mkdir(parents=True, exist_ok=True)
            markdown_dir.mkdir(parents=True, exist_ok=True)
            markdown_path = markdown_dir / "Linear RAG.md"
            markdown_path.write_text("# sample\n", encoding="utf-8")

            resource_roots = backend.build_resource_roots(normalized_dir, extract_dir, markdown_path)

            self.assertEqual(
                resource_roots,
                [
                    str(normalized_dir.resolve()),
                    str(markdown_dir.resolve()),
                    str(extract_dir.resolve()),
                ],
            )


class RuntimeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.layout = backend.RuntimeLayout(Path(self.tempdir.name))
        for directory in [
            self.layout.root,
            self.layout.tools_dir,
            self.layout.downloads_dir,
            self.layout.work_dir,
            self.layout.logs_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)
        self.desired_manifest = {
            "mineru_release_tag": backend.MINERU_RELEASE_TAG,
            "mineru_git_ref": backend.MINERU_GIT_REF,
            "pandoc_version": backend.PANDOC_VERSION,
        }

    def test_ensure_runtime_impl_reuses_existing_runtime_assets(self):
        self._create_executable(self.layout.venv_python)
        self._create_executable(self.layout.venv_pip)
        self._create_executable(self.layout.venv_bin / "mineru-models-download")
        self._create_executable(self.layout.pandoc_binary)
        self.layout.runtime_manifest.write_text(json.dumps(self.desired_manifest), encoding="utf-8")
        (self.layout.root / ".mineru-models-ready").write_text("ready\n", encoding="utf-8")

        with (
            patch.object(backend.venv, "EnvBuilder") as env_builder,
            patch.object(backend, "pip_install_with_retry") as pip_install,
            patch.object(backend.urllib.request, "urlretrieve", side_effect=AssertionError("Pandoc should not redownload")),
            patch.object(backend, "run", side_effect=AssertionError("MinerU models should not redownload")),
        ):
            backend.ensure_runtime_impl(self.layout, self.desired_manifest)

        env_builder.assert_not_called()
        pip_install.assert_not_called()

    def test_ensure_runtime_impl_reinstalls_when_manifest_changes(self):
        self._create_executable(self.layout.venv_python)
        self._create_executable(self.layout.venv_pip)
        self._create_executable(self.layout.venv_bin / "mineru-models-download")
        self._create_executable(self.layout.pandoc_binary)
        (self.layout.root / ".mineru-models-ready").write_text("ready\n", encoding="utf-8")

        commands: list[list[str]] = []

        def record_install(command, env, attempts=3):
            commands.append(command)

        with patch.object(backend, "pip_install_with_retry", side_effect=record_install):
            backend.ensure_runtime_impl(self.layout, self.desired_manifest)

        self.assertEqual(len(commands), 2)
        manifest = json.loads(self.layout.runtime_manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest, self.desired_manifest)

    def test_ensure_runtime_impl_reinstalls_when_virtual_environment_is_recreated(self):
        self.layout.venv_dir.mkdir(parents=True, exist_ok=True)
        self._create_executable(self.layout.pandoc_binary)
        self.layout.runtime_manifest.write_text(json.dumps(self.desired_manifest), encoding="utf-8")
        (self.layout.root / ".mineru-models-ready").write_text("ready\n", encoding="utf-8")

        commands: list[list[str]] = []

        def record_install(command, env, attempts=3):
            commands.append(command)

        with (
            patch.object(backend.venv, "EnvBuilder") as env_builder,
            patch.object(backend, "pip_install_with_retry", side_effect=record_install),
        ):
            backend.ensure_runtime_impl(self.layout, self.desired_manifest)

        env_builder.assert_called_once()
        self.assertEqual(len(commands), 2)

    def test_ensure_pandoc_accepts_arm64_archive_layout(self):
        archive_path = self.layout.downloads_dir / Path(backend.PANDOC_ZIP_URL).name
        root = f"pandoc-{backend.PANDOC_VERSION}-arm64"

        with zipfile.ZipFile(archive_path, "w") as archive:
            self._write_zip_file(archive, f"{root}/bin/pandoc", "#!/bin/sh\n")
            self._write_zip_file(archive, f"{root}/share/man/man1/pandoc.1", "pandoc manpage\n")

        backend.ensure_pandoc(self.layout)

        self.assertTrue(self.layout.pandoc_binary.exists())
        self.assertTrue((self.layout.pandoc_dir / "share/man/man1/pandoc.1").exists())

    def test_ensure_pandoc_repairs_execute_permission(self):
        self._create_executable(self.layout.pandoc_binary)
        self.layout.pandoc_binary.chmod(0o644)

        backend.ensure_pandoc(self.layout)

        self.assertTrue(os.access(self.layout.pandoc_binary, os.X_OK))

    def test_ensure_mineru_models_uses_noninteractive_pipeline_download(self):
        self._create_executable(self.layout.venv_python)
        calls = []

        def fake_run(command, *, env=None, cwd=None):
            calls.append((command, env))

        with patch.object(backend, "run", side_effect=fake_run):
            backend.ensure_mineru_models(self.layout)

        self.assertEqual(len(calls), 1)
        command, env = calls[0]
        self.assertEqual(
            command,
            [
                str(self.layout.venv_python),
                "-m",
                "mineru.cli.models_download",
                "-s",
                "huggingface",
                "-m",
                "pipeline",
            ],
        )
        self.assertEqual(env["MINERU_MODEL_SOURCE"], "local")
        self.assertEqual(env["MINERU_TOOLS_CONFIG_JSON"], str(self.layout.mineru_config_file))
        self.assertTrue((self.layout.root / ".mineru-models-ready").exists())

    def test_ensure_mineru_models_retries_with_alternate_source(self):
        self._create_executable(self.layout.venv_python)
        calls = []

        def fake_run(command, *, env=None, cwd=None):
            calls.append(command)
            if len(calls) == 1:
                raise backend.subprocess.CalledProcessError(returncode=1, cmd=command)

        with patch.object(backend, "run", side_effect=fake_run):
            backend.ensure_mineru_models(self.layout)

        self.assertEqual(calls[0][-3:], ["huggingface", "-m", "pipeline"])
        self.assertEqual(calls[1][-3:], ["modelscope", "-m", "pipeline"])
        self.assertTrue((self.layout.root / ".mineru-models-ready").exists())

    def test_ensure_api_server_launches_via_python_module(self):
        self._create_executable(self.layout.venv_python)
        self._create_executable(self.layout.api_log_file)

        popen_calls = []

        class DummyProcess:
            pid = 123

            def poll(self):
                return None

        def fake_popen(command, stdout=None, stderr=None, env=None, start_new_session=None):
            popen_calls.append((command, env))
            return DummyProcess()

        with (
            patch.object(backend, "healthcheck", side_effect=[False, True]),
            patch.object(backend.subprocess, "Popen", side_effect=fake_popen),
        ):
            backend.ensure_api_server(self.layout)

        command, env = popen_calls[0]
        self.assertEqual(
            command,
            [
                str(self.layout.venv_python),
                "-m",
                "mineru.cli.fast_api",
                "--host",
                "127.0.0.1",
                "--port",
                str(backend.API_PORT),
            ],
        )
        self.assertEqual(env["MINERU_MODEL_SOURCE"], "local")
        self.assertEqual(env["MINERU_TOOLS_CONFIG_JSON"], str(self.layout.mineru_config_file))

    def _create_executable(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)

    def _write_zip_file(self, archive: zipfile.ZipFile, name: str, content: str):
        info = zipfile.ZipInfo(name)
        info.external_attr = 0o755 << 16
        archive.writestr(info, content)


class TaskDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.layout = backend.RuntimeLayout(Path(self.tempdir.name))
        self.layout.logs_dir.mkdir(parents=True, exist_ok=True)
        self.layout.api_log_file.write_text("mineru-api log tail\n", encoding="utf-8")

    def test_download_task_archive_retries_after_read_timeout(self):
        archive_bytes = self._zip_bytes({"result/document.md": "# ok\n"})
        status_calls = 0

        class FakeReadTimeout(Exception):
            pass

        class FakeResponse:
            def __init__(self, *, json_payload=None, content=b""):
                self._json_payload = json_payload
                self.content = content

            def raise_for_status(self):
                return None

            def json(self):
                return self._json_payload

        class FakeRequests:
            class exceptions:
                ReadTimeout = FakeReadTimeout

            def get(self, url, timeout=None):
                nonlocal status_calls
                if url.endswith("/result"):
                    return FakeResponse(content=archive_bytes)
                status_calls += 1
                if status_calls == 1:
                    raise FakeReadTimeout("status timed out")
                if status_calls == 2:
                    return FakeResponse(json_payload={"status": "processing"})
                return FakeResponse(json_payload={"status": "completed"})

        target = self.layout.root / "task.zip"

        with patch.object(backend.time, "sleep", return_value=None):
            result = backend.download_task_archive(FakeRequests(), "task-123", target, self.layout)

        self.assertEqual(result, target)
        self.assertEqual(target.read_bytes(), archive_bytes)

    def test_download_task_archive_times_out_with_log_tail(self):
        class FakeReadTimeout(Exception):
            pass

        class FakeRequests:
            class exceptions:
                ReadTimeout = FakeReadTimeout

            def get(self, url, timeout=None):
                raise FakeReadTimeout("status timed out")

        target = self.layout.root / "task.zip"
        monotonic_values = iter(
            [
                0.0,
                backend.MINERU_TASK_MAX_WAIT_SECONDS + 1.0,
                backend.MINERU_TASK_MAX_WAIT_SECONDS + 1.0,
            ]
        )

        with (
            patch.object(backend.time, "sleep", return_value=None),
            patch.object(backend.time, "monotonic", side_effect=lambda: next(monotonic_values)),
        ):
            with self.assertRaises(RuntimeError) as context:
                backend.download_task_archive(FakeRequests(), "task-123", target, self.layout)

        self.assertIn("task-123", str(context.exception))
        self.assertIn("mineru-api log tail", str(context.exception))

    def _zip_bytes(self, files: dict[str, str]) -> bytes:
        path = Path(self.tempdir.name) / "archive.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return path.read_bytes()


@unittest.skipUnless(os.getenv("NATURE_SAMPLE_PDF"), "Set NATURE_SAMPLE_PDF to run the sample cleanup smoke test.")
class SamplePDFSmokeTests(unittest.TestCase):
    def test_sample_pdf_first_page_smoke(self):
        from pypdf import PdfReader

        sample_pdf = Path(os.environ["NATURE_SAMPLE_PDF"]).expanduser().resolve()
        text = PdfReader(str(sample_pdf)).pages[0].extract_text() or ""
        result = backend.clean_nature_markdown(text, None, None)

        self.assertIn("Linear RAG scanning mediates editing", result.markdown)
        self.assertNotIn("https://doi.org/10.1038", result.markdown)


if __name__ == "__main__":
    unittest.main()
