import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atomdeeplit import metadata, triage, zotero
from atomdeeplit.__main__ import SCHEMA, apply


class WorkflowTests(unittest.TestCase):
    def test_markdown_links_and_plain_urls(self):
        url = "https://doi.org/10.1234/example"
        cell = "https://www.cell.com/chem/abstract/S0000-0000(26)00000-0"
        self.assertEqual(metadata.extract_urls(f"[{url}]({url}) [Cell]({cell})\n{url}"), [url, cell, url])

    def test_duplicate_arxiv_across_versions_and_doi(self):
        index = zotero.make_index([{
            "key": "EXAMPLE1", "itemType": "preprint", "DOI": "10.48550/arXiv.2601.00001",
            "title": "Version one", "url": "https://arxiv.org/abs/2601.00001v1",
        }])
        matches = zotero.matches(index, "https://arxiv.org/abs/2601.00001v2", {"title": "Version two"})
        self.assertEqual(matches, [{"key": "EXAMPLE1", "title": "Version one", "evidence": ["arxiv"]}])

    def test_model_cannot_select_unlisted_collection(self):
        result = {"suggestions": [{"row": 1, "collection_key": "OTHER", "priority": True, "reason": "Focus"}]}
        with self.assertRaisesRegex(ValueError, "outside the shortlist"):
            triage.validate_suggestions(result, {1: {"COLL0001"}})
        result["suggestions"][0]["collection_key"] = "COLL0001"
        self.assertTrue(triage.validate_suggestions(result, {1: {"COLL0001"}})[1]["priority"])

    def test_apply_checks_duplicates_and_uses_explicit_priority(self):
        record = {"doi": "10.1234/example", "title": "Test article", "type": "journal-article"}
        row = {"row": 1, "url": "https://doi.org/10.1234/example", "record": record,
               "news": False, "suggestion": {"collection_key": "COLL0001", "priority": True}}
        stamp = ("SAMPLE-SERVER", "5")
        collections = [{"key": "COLL0001", "name": "Electrolytes"}]
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            plan = folder / "plan.json"
            plan.write_text(json.dumps({"schema": SCHEMA, "zotero_server": stamp[0], "rows": [row]}), encoding="utf-8")
            args = argparse.Namespace(plan=plan, select="1", priority="", collection=[], use_suggestions=True,
                                      receipt=folder / "receipt.json", yes=True, backup_dir=folder)
            with patch.object(zotero, "library", return_value=(stamp, [], collections)), \
                    patch.object(zotero, "backup", return_value=folder / "backup.zip"), \
                    patch.object(zotero, "create_items", return_value={"successful": {"0": {"key": "EXAMPLE1"}}}) as create, \
                    patch.object(zotero, "list_items", return_value=[{
                        "key": "EXAMPLE1", "itemType": "journalArticle", "DOI": record["doi"],
                        "collections": ["COLL0001"], "tags": [],
                    }]), redirect_stdout(io.StringIO()):
                self.assertEqual(apply(args, {}), 0)
                sent = create.call_args.args[1][0]
                self.assertEqual(sent["collections"], ["COLL0001"])
                self.assertEqual(sent["tags"], [])
            self.assertTrue(json.loads(args.receipt.read_text())["results"][0]["verified"])
            args.receipt = folder / "new-receipt.json"
            existing = {"key": "EXAMPLE1", "itemType": "journalArticle", "DOI": record["doi"]}
            with patch.object(zotero, "library", return_value=(stamp, [existing], collections)), \
                    patch.object(zotero, "create_items") as create:
                with self.assertRaisesRegex(ValueError, "now matches"):
                    apply(args, {})
                create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
