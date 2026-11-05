"""Tests for the settings file of catalog_sources and the VizieR mirrors."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

import catalog_sources as cs

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"
CDS, CFA, NAO = ("https://vizier.cds.unistra.fr", "https://vizier.cfa.harvard.edu",
                 "https://vizier.nao.ac.jp")

VSX_TSV = """#
Name\tType\tPeriod\tmax\tRAJ2000\tDEJ2000
\t\td\tmag\tdeg\tdeg
----\t----\t----\t----\t----\t----
V0873 Per\tEW\t0.29490155\t10.800\t41.78421\t41.37553
"""


class TestLoadConfig(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "config.json"

    def load(self, data):
        self.path.write_text(data if isinstance(data, str) else json.dumps(data))
        return cs.load_config(self.path)

    def test_missing_file(self):
        self.assertEqual(cs.load_config(self.path), ({}, []))

    def test_example_file_is_valid(self):
        settings, warnings = cs.load_config(EXAMPLE)
        self.assertEqual(warnings, [])
        self.assertEqual(settings["VIZIER_MIRRORS"], [CDS, CFA, NAO])
        self.assertEqual(settings["SOURCE_ORDER"], ("vizier", "aavso"))
        self.assertEqual((settings["TIMEOUT_CONNECT"], settings["TIMEOUT_READ"]), (5, 20))
        self.assertEqual(settings["RETRY_AFTER"], 600)
        self.assertEqual(settings["CACHE_MAX_AGE_DAYS"], 30)
        self.assertEqual(settings["FINDCOMP_TIMEOUT"], 120)

    def test_partial_file(self):
        settings, warnings = self.load({"vizier_mirrors": [CFA + "/"],
                                        "source_order": ["aavso"]})
        self.assertEqual(settings, {"VIZIER_MIRRORS": [CFA], "SOURCE_ORDER": ("aavso",)})
        self.assertEqual(warnings, [])

    def test_bad_entries_ignored_with_warnings(self):
        settings, warnings = self.load({
            "vizier_mirrors": "vizier.cfa.harvard.edu",
            "source_order": ["vizier", "simbad"],
            "timeout_read": 0,
            "timeout_connect": True,
            "cache_max_age_days": -1,
            "retry_after_minutes": 5,
            "colour": "red",
        })
        self.assertEqual(settings, {"RETRY_AFTER": 300})
        self.assertEqual(len(warnings), 6)
        self.assertTrue(any("unknown setting 'colour'" in w for w in warnings))

    def test_unreadable_file(self):
        settings, warnings = self.load("{not json")
        self.assertEqual(settings, {})
        self.assertIn("defaults used", warnings[0])
        settings, warnings = self.load("[1, 2]")
        self.assertIn("not a JSON object", warnings[0])

    def test_apply_config_sets_module_settings(self):
        self.load({"vizier_mirrors": [CFA], "timeout_read": 7})
        saved = {k: getattr(cs, k) for k in ("VIZIER_MIRRORS", "VIZIER_URL", "TIMEOUT_READ")}
        self.addCleanup(lambda: [setattr(cs, k, v) for k, v in saved.items()])
        self.assertEqual(cs.apply_config(self.path), [])
        self.assertEqual(cs.VIZIER_MIRRORS, [CFA])
        self.assertEqual(cs.VIZIER_URL, CFA + cs.VIZIER_PATH)
        self.assertEqual(cs.TIMEOUT_READ, 7)


class TestMirrors(unittest.TestCase):
    def setUp(self):
        cs.reset_failures()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, value in (("CACHE_DIR", Path(tmp.name)),
                            ("VIZIER_MIRRORS", [CDS, CFA, NAO])):
            p = mock.patch.object(cs, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.calls: list[str] = []
        self.log: list[str] = []

    def route(self, answers):
        def get(url, params=None, timeout=None):
            self.calls.append(url)
            self.timeout = timeout
            a = answers[url.removesuffix(cs.VIZIER_PATH)]
            if isinstance(a, Exception):
                raise a
            r = mock.Mock(text=a, status_code=200)
            return r
        p = mock.patch.object(cs.requests, "get", side_effect=get)
        p.start()
        self.addCleanup(p.stop)

    def test_next_mirror_when_the_first_does_not_connect(self):
        self.route({CDS: requests.ConnectTimeout(), CFA: VSX_TSV, NAO: VSX_TSV})
        stars, src = cs.fetch_vsx(41.78421, 41.37553, 1.0, on_log=self.log.append)
        self.assertEqual(src, "VizieR VSX")
        self.assertEqual([s["name"] for s in stars], ["V0873 Per"])
        self.assertEqual(self.calls, [CDS + cs.VIZIER_PATH, CFA + cs.VIZIER_PATH])
        self.assertIn("VizieR VSX: 1 stars via vizier.cfa.harvard.edu", self.log)
        self.assertEqual(self.timeout, (cs.TIMEOUT_CONNECT, cs.TIMEOUT_READ))

    def test_failed_mirror_skipped_next_time(self):
        self.route({CDS: requests.ConnectTimeout(), CFA: VSX_TSV})
        cs.fetch_vsx(41.78421, 41.37553, 1.0, refresh=True)
        self.calls.clear()
        cs.fetch_vsx(41.78421, 41.37553, 1.0, refresh=True)
        self.assertEqual(self.calls, [CFA + cs.VIZIER_PATH])

    def test_all_mirrors_down_then_aavso(self):
        self.route({CDS: requests.ConnectTimeout(), CFA: requests.ReadTimeout(),
                    NAO: requests.ConnectionError()})
        with mock.patch.object(cs, "aavso_vsx", return_value=[{"name": "x"}]):
            stars, src = cs.fetch_vsx(41.78421, 41.37553, 1.0, on_log=self.log.append)
        self.assertEqual(src, "AAVSO VSX")
        failed = next(m for m in self.log if m.startswith("VizieR VSX: failed"))
        self.assertIn(f"vizier.cds.unistra.fr: no connection within {cs.TIMEOUT_CONNECT:g} s",
                      failed)
        self.assertIn(f"vizier.cfa.harvard.edu: no answer within {cs.TIMEOUT_READ:g} s",
                      failed)
        self.assertIn("vizier.nao.ac.jp: ConnectionError", failed)


if __name__ == "__main__":
    unittest.main()
