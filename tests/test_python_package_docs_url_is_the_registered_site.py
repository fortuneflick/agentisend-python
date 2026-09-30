import unittest
from pathlib import Path


class PythonPackageDocsUrlTest(unittest.TestCase):
    def test_python_package_docs_url_is_the_registered_site(self) -> None:
        text = Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text()
        self.assertIn('Documentation = "https://agentisend.com/docs"', text)
        self.assertNotIn("docs.agentisend.dev", text)
