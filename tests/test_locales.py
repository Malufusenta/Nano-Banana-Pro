import unittest

from app.locale_validation import collect_locale_errors


class LocaleFilesTest(unittest.TestCase):
    def test_locales_match_reference(self) -> None:
        errors = collect_locale_errors()
        self.assertEqual(
            errors,
            [],
            "Локали не согласованы с en:\n" + "\n".join(errors),
        )


if __name__ == "__main__":
    unittest.main()
