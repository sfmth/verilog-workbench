"""Pages-prefix asset integrity and repeatable development-preview builds."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urljoin, urlsplit

from scripts.build_web import BuildError, build_web, validate_web


class WebBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "web"
        self.output = self.directory / "dist/web"
        self.write("index.html", '<a href="workspace/#files">Workspace</a><a href="m0/">Proof</a>')
        self.write("workspace/index.html", '<a href="../">Home</a><script type="module" src="app.mjs"></script>')
        self.write("workspace/app.mjs", "import {client} from '../m0/client.mjs';\nnew Worker(new URL('./worker.mjs', import.meta.url));")
        self.write("workspace/worker.mjs", "const wasm = new URL('engine.wasm', import.meta.url);")
        self.write("workspace/engine.wasm", "test fixture")
        self.write("m0/index.html", '<link href="style.css" rel="stylesheet"><a href="../">Home</a>')
        self.write("m0/client.mjs", "export const client = {};")
        self.write("m0/style.css", "body { background: url('../assets/pattern.svg'); }")
        self.write("assets/pattern.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')

    def write(self, relative, content):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_build_copies_static_entry_points_and_records_content(self):
        manifest = build_web(self.source, self.output)
        self.assertEqual(manifest["status"], "development-preview")
        self.assertEqual(manifest["entry_points"], ["/verilog-workbench/", "/verilog-workbench/workspace/", "/verilog-workbench/m0/"])
        self.assertTrue((self.output / ".nojekyll").is_file())
        self.assertEqual(json.loads((self.output / "vwb-web-build.json").read_text()), manifest)
        for relative, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.output / relative).read_bytes()).hexdigest(), digest)

    def test_document_links_and_fragment_reload_keep_the_project_prefix(self):
        build_web(self.source, self.output)
        base = "https://owner.github.io/verilog-workbench/"
        for owner, reference in [("", "workspace/#files"), ("workspace/", "../"), ("m0/", "../"), ("workspace/", "../m0/client.mjs")]:
            url = urljoin(urljoin(base, owner), reference)
            pathname = urlsplit(url).path.removeprefix("/verilog-workbench/")
            target = self.output / pathname
            if target.is_dir():
                target /= "index.html"
            self.assertTrue(target.is_file(), url)
            self.assertTrue(url.startswith(base), url)

    def test_missing_html_module_worker_wasm_and_css_targets_fail(self):
        for relative in ("workspace/index.html", "workspace/app.mjs", "workspace/worker.mjs", "workspace/engine.wasm", "assets/pattern.svg"):
            path = self.source / relative
            content = path.read_bytes()
            with self.subTest(relative=relative):
                path.unlink()
                with self.assertRaises(BuildError):
                    validate_web(self.source)
                path.write_bytes(content)

    def test_root_relative_and_escaping_urls_fail_before_output_changes(self):
        for reference in ("/workspace/", "../../outside.txt", "%2fworkspace/", "..\\outside.txt"):
            with self.subTest(reference=reference):
                self.write("m0/index.html", f'<a href="{reference}">Broken</a>')
                with self.assertRaises(BuildError):
                    build_web(self.source, self.output)
                self.assertFalse(self.output.exists())

    def test_dynamic_import_and_multiline_reexport_are_validated(self):
        for source in ("const module = import('./missing.mjs');", "export {\n  value\n} from './missing.mjs';"):
            self.write("workspace/app.mjs", source)
            with self.subTest(source=source), self.assertRaisesRegex(BuildError, "missing local URL"):
                validate_web(self.source)

    def test_comments_external_links_fragments_and_query_strings(self):
        self.write("index.html", '<a href="https://example.com/">Docs</a><a href="#guide">Guide</a><a href="workspace/?mode=edit#files">Workspace</a>')
        self.write("workspace/app.mjs", "// import './missing.mjs';\n/* new URL('./missing.wasm', import.meta.url) */\nimport '../m0/client.mjs';")
        validate_web(self.source)

    def test_unbundled_bare_import_fails(self):
        self.write("workspace/app.mjs", "import 'client.mjs';")
        self.write("workspace/client.mjs", "export {};")
        with self.assertRaisesRegex(BuildError, "bare module specifier"):
            validate_web(self.source)

    def test_symlink_escape_is_rejected(self):
        external = self.directory / "outside.mjs"
        external.write_text("export {};", encoding="utf-8")
        try:
            (self.source / "workspace/linked.mjs").symlink_to(external)
        except OSError:
            self.skipTest("host does not permit symlinks")
        with self.assertRaisesRegex(BuildError, "Symlinked assets"):
            validate_web(self.source)

    def test_rebuild_removes_old_output_and_keeps_source(self):
        build_web(self.source, self.output)
        (self.output / "stale.txt").write_text("previous build", encoding="utf-8")
        build_web(self.source, self.output)
        self.assertFalse((self.output / "stale.txt").exists())
        self.assertTrue((self.source / "workspace/index.html").is_file())

    def test_source_overlap_and_unowned_output_are_preserved(self):
        for output in (self.source, self.source / "dist", self.directory):
            with self.subTest(output=output), self.assertRaisesRegex(BuildError, "must not overlap"):
                build_web(self.source, output)
        self.output.mkdir(parents=True)
        keep = self.output / "keep.txt"
        keep.write_text("owned by user", encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "Refusing to replace"):
            build_web(self.source, self.output)
        self.assertEqual(keep.read_text(), "owned by user")

    def test_custom_prefix_and_invalid_prefix(self):
        self.assertEqual(build_web(self.source, self.output, "/preview/")["base_path"], "/preview/")
        for base in ("preview/", "/../", "//", "/preview"):
            with self.subTest(base=base), self.assertRaises(BuildError):
                build_web(self.source, self.output, base)


if __name__ == "__main__":
    unittest.main()
