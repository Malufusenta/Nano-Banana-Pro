import os
import unittest
from pathlib import Path

from app.cyrillic_text_scan import collect_cyrillic_hits

REPO = Path(__file__).resolve().parent.parent


class CyrillicScanTest(unittest.TestCase):
    def test_scanner_module_is_ascii_only(self) -> None:
        """Meta-check: this module stays free of Cyrillic so it does not match itself."""
        path = REPO / "app" / "cyrillic_text_scan.py"
        hits = collect_cyrillic_hits(REPO, include_admin_panel=False)
        self.assertTrue(
            all("cyrillic_text_scan.py" not in h for h in hits),
            "cyrillic_text_scan.py must stay ASCII-only:\n"
            + "\n".join(h for h in hits if "cyrillic_text_scan.py" in h),
        )

    @unittest.skipUnless(
        os.environ.get("STRICT_CYRILLIC") == "1",
        "set STRICT_CYRILLIC=1 to fail on any Cyrillic in app/ and main.py",
    )
    def test_no_cyrillic_in_bot_code_when_strict(self) -> None:
        hits = collect_cyrillic_hits(REPO, include_admin_panel=False)
        self.assertEqual(
            hits,
            [],
            "Cyrillic in code — move strings to app/locales:\n" + "\n".join(hits[:50]),
        )


if __name__ == "__main__":
    unittest.main()
