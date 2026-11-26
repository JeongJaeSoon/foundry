"""0.1.79: the design screenshots are replaced by text: docs/concepts.md and docs/snapshots.md (screens rendered from
example data by tests/doc_snapshots.py), linked from the README and the architecture notes. 0.1.111: the concept
diagrams are SVG files in docs/img (tests/test_lifecycle_01111.py)."""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANGUL = re.compile("[가-힣]")


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


class ConceptDocs(unittest.TestCase):
    def test_snapshots_are_generated_text_from_example_data(self):
        doc = read("docs", "snapshots.md")
        self.assertIn("tests/doc_snapshots.py", doc)
        self.assertIn("example-org", doc)
        for section in ("## Board pane", "## Unit detail", "## PR tab", "## Decision card", "## Handover"):
            body = doc.split(section, 1)[1].split("```text\n", 1)[1].split("```", 1)[0]
            self.assertTrue(body.strip(), section)
        self.assertIsNone(HANGUL.search(doc))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "tests", "doc_snapshots.py")))

    def test_linked_and_no_raster_images(self):
        for name in ("README.md", os.path.join("docs", "architecture.md")):
            text = read(name)
            self.assertIn("concepts.md", text)
            self.assertIn("snapshots.md", text)
        images = [f for d, _, fs in os.walk(os.path.join(ROOT, "docs")) for f in fs
                  if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))]
        self.assertEqual(images, [])


if __name__ == "__main__":
    unittest.main()
