"""Tests for catalog_sources (VizieR → AAVSO fallback) and pipeline.comp_origin.

No network: requests.get is replaced by canned answers.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

import catalog_sources as cs
from pipeline import AAVSO_CHART, comp_origin, query_apass, query_vsx

# V0873 Per and part of its VSP chart (answer of 2026-11-04, trimmed).
RA, DEC = 41.78421, 41.37553

VSX_JSON = {"VSXObjects": {"VSXObject": [
    {"Name": "Gaia DR3 336795293651761408", "RA2000": "41.66664",
     "Declination2000": "41.33512", "VariabilityType": "ROT",
     "MaxMag": "14.41 G", "MinMag": "14.43 G"},
    {"Name": "V0873 Per", "AUID": "000-BLX-752", "RA2000": "41.78421",
     "Declination2000": "41.37553", "VariabilityType": "EW",
     "Period": "0.29490155", "Epoch": "2451370.8993",
     "MaxMag": "10.8 V", "MinMag": "11.5 V"},
    {"Name": "no magnitude", "RA2000": "41.9", "Declination2000": "41.5"},
    {"Name": "fainter than", "RA2000": "41.7", "Declination2000": "41.3",
     "MaxMag": "<15.2 r", "Period": "abc"},
    {"Name": "no position", "MaxMag": "12 V"},
]}}

VSP_JSON = {"chartid": "X43364NGX", "fov": 180.0, "maglimit": 14.4, "photometry": [
    {"auid": "000-BPD-777", "ra": "02:46:49.70", "dec": "41:10:11.6", "label": "106",
     "bands": [{"band": "V", "mag": 10.562, "error": 0.014},
               {"band": "B", "mag": 11.496, "error": 0.021}]},
    {"auid": "000-BPD-778", "ra": "02:47:30.00", "dec": "41:30:00.0", "label": 112,
     "bands": [{"band": "V", "mag": 11.208, "error": None}]},
    {"auid": "000-BPD-779", "ra": "02:46:10.00", "dec": "41:25:00.0", "label": "116",
     "bands": [{"band": "V", "mag": 11.622, "error": 0.03}]},
    {"auid": "NOISY", "ra": "02:46:20.00", "dec": "41:25:00.0", "label": "117",
     "bands": [{"band": "V", "mag": 11.7, "error": 0.09}]},
    {"auid": "B-ONLY", "ra": "02:46:30.00", "dec": "41:25:00.0",
     "bands": [{"band": "B", "mag": 12.0, "error": 0.01}]},
    {"auid": "TOO-FAINT", "ra": "02:46:40.00", "dec": "41:25:00.0",
     "bands": [{"band": "V", "mag": 16.0, "error": 0.01}]},
    {"auid": "CORNER", "ra": "02:56:00.00", "dec": "42:50:00.0",
     "bands": [{"band": "V", "mag": 11.0, "error": 0.01}]},
]}

APASS_TSV = """#
#   VizieR Astronomical Server
RAJ2000\tDEJ2000\tVmag\te_Vmag
deg\tdeg\tmag\tmag
---------\t---------\t------\t-----
41.70000\t+41.20000\t11.300\t0.020
41.78421\t+41.37553\t10.900\t0.010
41.90000\t+41.50000\t12.100\t0.030
"""


def _resp(*, json_data=None, text="", status=200):
    r = mock.Mock()
    r.status_code = status
    r.text = text
    if json_data is None:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = json_data
    if status >= 400:
        err = requests.HTTPError()
        err.response = r
        r.raise_for_status.side_effect = err
    return r


def _router(vizier=None, vsx=None, vsp=None):
    """requests.get stand-in: each source returns a response or raises."""
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(url)
        answer = {cs.VIZIER_URL: vizier, cs.AAVSO_VSX_URL: vsx,
                  cs.AAVSO_VSP_URL: vsp}[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return get, calls


class _Base(unittest.TestCase):
    def setUp(self):
        cs.reset_failures()
        self.log: list[str] = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)
        p = mock.patch.object(cs, "CACHE_DIR", self.cache)
        p.start()
        self.addCleanup(p.stop)

    def patch(self, get):
        p = mock.patch.object(cs.requests, "get", side_effect=get)
        p.start()
        self.addCleanup(p.stop)


class TestAavsoParsing(_Base):
    def test_vsx_rows_match_vizier_layout(self):
        get, _ = _router(vsx=_resp(json_data=VSX_JSON))
        self.patch(get)
        stars = cs.aavso_vsx(RA, DEC, 2.925)
        names = [s["name"] for s in stars]
        self.assertEqual(names[0], "V0873 Per")           # nearest first
        self.assertNotIn("no position", names)
        v = stars[0]
        self.assertEqual(set(v) - {"auid"},
                         {"name", "ra", "dec", "mag", "var_type", "period", "dist"})
        self.assertEqual((v["mag"], v["var_type"], v["period"]),
                         (10.8, "EW", "0.2949 d"))
        by_name = {s["name"]: s for s in stars}
        self.assertEqual(by_name["no magnitude"]["mag"], 99.0)
        self.assertEqual(by_name["no magnitude"]["var_type"], "?")
        self.assertEqual(by_name["fainter than"]["mag"], 15.2)
        self.assertEqual(by_name["fainter than"]["period"], "—")

    def test_vsx_single_object_and_empty(self):
        one = {"VSXObjects": {"VSXObject": VSX_JSON["VSXObjects"]["VSXObject"][1]}}
        get, _ = _router(vsx=_resp(json_data=one))
        self.patch(get)
        self.assertEqual([s["name"] for s in cs.aavso_vsx(RA, DEC, 1)], ["V0873 Per"])
        cs.requests.get.side_effect = _router(vsx=_resp(json_data={"VSXObjects": {}}))[0]
        self.assertEqual(cs.aavso_vsx(RA, DEC, 1), [])

    def test_vsx_html_answer_is_an_error(self):
        get, _ = _router(vsx=_resp(text="<html>Just a moment...</html>"))
        self.patch(get)
        with self.assertRaises(cs.CatalogError):
            cs.aavso_vsx(RA, DEC, 1)

    def test_vsp_query_stays_within_limits(self):
        get, _ = _router(vsp=_resp(json_data=VSP_JSON))
        self.patch(get)
        cs.aavso_vsp(RA, DEC, 10.8, radius_deg=2.0, dvmag=3.5)
        params = cs.requests.get.call_args.kwargs["params"]
        self.assertEqual(float(params["fov"]), cs.VSP_MAX_FOV)
        self.assertEqual(float(params["maglimit"]), 14.3)
        cs.aavso_vsp(RA, DEC, 10.8, radius_deg=0.5, dvmag=3.5)
        self.assertEqual(float(cs.requests.get.call_args.kwargs["params"]["fov"]), 60)

    def test_vsp_stars(self):
        get, _ = _router(vsp=_resp(json_data=VSP_JSON))
        self.patch(get)
        comps = {c["auid"]: c for c in cs.aavso_vsp(RA, DEC, 10.8, 1.5, 3.5)}
        self.assertNotIn("B-ONLY", comps)              # no V magnitude
        self.assertNotIn("CORNER", comps)              # outside the 1.5° cone
        c = comps["000-BPD-777"]
        self.assertAlmostEqual(c["ra"], 41.707083, places=5)
        self.assertAlmostEqual(c["dec"], 41.169889, places=5)
        self.assertEqual((c["vmag"], c["e_vmag"], c["label"]), (10.562, 0.014, "106"))
        self.assertEqual((c["source"], c["chart"]), (cs.VSP_LABEL, "X43364NGX"))
        self.assertEqual(comps["000-BPD-778"]["label"], "112")

    def test_sexagesimal_south(self):
        self.assertAlmostEqual(cs._sexa_to_deg("-00:30:00", hours=False), -0.5)
        self.assertAlmostEqual(cs._sexa_to_deg("12:00:00", hours=True), 180.0)


class TestFallback(_Base):
    def test_vizier_timeout_falls_back_to_aavso(self):
        get, calls = _router(vizier=requests.Timeout(), vsx=_resp(json_data=VSX_JSON))
        self.patch(get)
        stars, src = cs.fetch_vsx(RA, DEC, 2.925, on_log=self.log.append)
        self.assertEqual(src, "AAVSO VSX")
        self.assertIn("V0873 Per", [s["name"] for s in stars])
        self.assertEqual(calls, [cs.VIZIER_URL, cs.AAVSO_VSX_URL])
        self.assertTrue(any("VizieR VSX: failed" in m for m in self.log))
        self.assertTrue(any(m.startswith("AAVSO VSX: ") for m in self.log))

    def test_failed_source_is_skipped_for_a_while(self):
        get, calls = _router(vizier=requests.ConnectionError(),
                             vsx=_resp(json_data=VSX_JSON), vsp=_resp(json_data=VSP_JSON))
        self.patch(get)
        cs.fetch_vsx(RA, DEC, 1)
        cs.fetch_comps(RA, DEC, 10.8, radius_deg=1.5, dvmag=3.5, on_log=self.log.append)
        self.assertEqual(calls.count(cs.VIZIER_URL), 1)
        self.assertTrue(any("VizieR APASS: skipped" in m for m in self.log))

    def test_empty_vizier_answer_is_kept(self):
        empty = "RAJ2000\tDEJ2000\n---\t---\n"
        get, calls = _router(vizier=_resp(text=empty))
        self.patch(get)
        comps, src = cs.fetch_comps(RA, DEC, 10.8)
        self.assertEqual((comps, src), ([], "VizieR APASS"))
        self.assertEqual(calls, [cs.VIZIER_URL])

    def test_http_error_falls_back(self):
        get, _ = _router(vizier=_resp(status=503), vsp=_resp(json_data=VSP_JSON))
        self.patch(get)
        comps, src = cs.fetch_comps(RA, DEC, 10.8, radius_deg=1.5, n=50, dvmag=3.5,
                                    on_log=self.log.append)
        self.assertEqual(src, "AAVSO VSP")
        self.assertIn("VizieR APASS: failed (HTTP 503)", self.log)

    def test_order_can_be_reversed(self):
        get, calls = _router(vsx=_resp(json_data=VSX_JSON))
        self.patch(get)
        _, src = cs.fetch_vsx(RA, DEC, 1, order=("aavso", "vizier"))
        self.assertEqual((src, calls), ("AAVSO VSX", [cs.AAVSO_VSX_URL]))

    def test_all_sources_fail(self):
        get, _ = _router(vizier=requests.Timeout(), vsx=requests.Timeout())
        self.patch(get)
        self.assertEqual(cs.fetch_vsx(RA, DEC, 1, on_log=self.log.append), ([], None))
        self.assertTrue(self.log[-1].startswith("Catalogue: no source answered"))

    def test_comps_filtered_and_sorted_like_apass(self):
        get, _ = _router(vizier=requests.Timeout(), vsp=_resp(json_data=VSP_JSON))
        self.patch(get)
        comps, _ = cs.fetch_comps(RA, DEC, 11.0, radius_deg=1.5, n=50, dvmag=3.5)
        self.assertEqual([c["auid"] for c in comps],
                         ["000-BPD-778", "000-BPD-777", "000-BPD-779"])

    def test_vizier_apass_unchanged(self):
        get, _ = _router(vizier=_resp(text=APASS_TSV))
        self.patch(get)
        comps, src = cs.fetch_comps(RA, DEC, 11.0, radius_deg=1.5, n=6, dvmag=2.0)
        self.assertEqual(src, "VizieR APASS")
        # the star on the target is dropped, the rest sorted by |ΔV|
        self.assertEqual([c["vmag"] for c in comps], [11.3, 12.1])
        self.assertEqual({c["chart"] for c in comps}, {AAVSO_CHART})


def _days_ago(days):
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)


class TestCache(_Base):
    def fetch_vsx_once(self):
        get, calls = _router(vizier=requests.Timeout(), vsx=_resp(json_data=VSX_JSON))
        self.patch(get)
        cs.fetch_vsx(RA, DEC, 2.925)
        return calls

    def offline(self):
        cs.reset_failures()
        get, calls = _router(vizier=requests.Timeout(), vsx=requests.Timeout(),
                             vsp=requests.Timeout())
        cs.requests.get.side_effect = get
        return calls

    def test_answer_saved_with_its_query(self):
        self.fetch_vsx_once()
        files = list(self.cache.glob("vsx_aavso_*.json"))
        self.assertEqual(len(files), 1)
        e = json.loads(files[0].read_text())
        self.assertEqual((e["kind"], e["source"], e["name"]), ("vsx", "aavso", "AAVSO VSX"))
        self.assertEqual((e["ra"], e["dec"], e["radius_deg"], e["covered_deg"]),
                         (RA, DEC, 2.925, 2.925))
        self.assertEqual(len(e["rows"]), 4)
        self.assertIn("fetched_utc", e)

    def test_query_inside_the_cached_area_needs_no_network(self):
        self.fetch_vsx_once()
        calls = self.offline()
        stars, src = cs.fetch_vsx(RA + 0.1, DEC, 1.0, on_log=self.log.append)
        self.assertEqual(calls, [])
        self.assertEqual(src, "AAVSO VSX")
        self.assertEqual(stars[0]["name"], "V0873 Per")
        self.assertIn("from the cache", self.log[-1])

    def test_query_outside_the_cached_area_asks_the_sources(self):
        self.fetch_vsx_once()
        calls = self.offline()
        self.assertEqual(cs.fetch_vsx(RA + 2.0, DEC, 2.0), ([], None))
        self.assertEqual(calls, [cs.VIZIER_URL, cs.AAVSO_VSX_URL])

    def test_capped_answer_covers_only_out_to_its_farthest_star(self):
        with mock.patch.object(cs, "VSX_MAX_ROWS", 2):
            self.fetch_vsx_once()
            e = json.loads(next(self.cache.glob("vsx_*.json")).read_text())
            self.assertLess(e["covered_deg"], 0.2)          # 2 nearest stars only
            calls = self.offline()
            self.assertEqual(cs.fetch_vsx(RA, DEC, 1.0)[1], None)
            self.assertTrue(calls)
            self.assertEqual(cs.fetch_vsx(RA, DEC, 0.05)[1], "AAVSO VSX")

    def test_refresh_asks_again_and_falls_back_to_the_cache(self):
        calls = self.fetch_vsx_once()
        cs.reset_failures()
        calls.clear()
        cs.fetch_vsx(RA, DEC, 2.925, refresh=True)
        self.assertEqual(calls, [cs.VIZIER_URL, cs.AAVSO_VSX_URL])
        calls = self.offline()
        stars, src = cs.fetch_vsx(RA, DEC, 2.925, refresh=True, on_log=self.log.append)
        self.assertEqual(src, "AAVSO VSX")
        self.assertIn("as no source answered", self.log[-1])

    def test_old_cache_used_only_when_every_source_fails(self):
        with mock.patch.object(cs, "_now", return_value=_days_ago(40)):
            self.fetch_vsx_once()
        calls = self.offline()
        stars, src = cs.fetch_vsx(RA, DEC, 2.0, on_log=self.log.append)
        self.assertEqual(calls, [cs.VIZIER_URL, cs.AAVSO_VSX_URL])   # asked first
        self.assertEqual(src, "AAVSO VSX")
        self.assertRegex(self.log[-1], r"as no source answered .*40 days old")

    def test_apass_magnitude_range_must_be_covered(self):
        get, _ = _router(vizier=_resp(text=APASS_TSV))
        self.patch(get)
        cs.fetch_comps(RA, DEC, 11.0, radius_deg=1.5, dvmag=2.0)   # 9.00..13.00
        calls = self.offline()
        comps, src = cs.fetch_comps(RA, DEC, 11.5, radius_deg=1.0, dvmag=1.0)
        self.assertEqual((src, calls), ("VizieR APASS", []))
        self.assertEqual([c["vmag"] for c in comps], [11.3, 12.1])
        self.assertEqual(cs.fetch_comps(RA, DEC, 11.0, radius_deg=1.0, dvmag=3.5),
                         ([], None))                                   # 7.5..14.5

    def test_vsp_chart_reused_with_its_chart_id(self):
        get, _ = _router(vizier=requests.Timeout(), vsp=_resp(json_data=VSP_JSON))
        self.patch(get)
        first, _ = cs.fetch_comps(RA, DEC, 11.0, radius_deg=1.5, n=50, dvmag=3.5)
        calls = self.offline()
        again, src = cs.fetch_comps(RA, DEC, 11.0, radius_deg=0.5, n=50, dvmag=3.5)
        self.assertEqual((src, calls), ("AAVSO VSP", []))
        self.assertEqual([c["auid"] for c in again], [c["auid"] for c in first])
        self.assertEqual({c["chart"] for c in again}, {"X43364NGX"})
        # fainter than the chart's maglimit (14.5) cannot come from it
        self.assertEqual(cs.fetch_comps(RA, DEC, 12.0, radius_deg=0.5, dvmag=3.5),
                         ([], None))

    def test_unreadable_cache_files_are_ignored(self):
        self.cache.mkdir(exist_ok=True)
        (self.cache / "vsx_aavso_broken.json").write_text("{not json")
        self.fetch_vsx_once()
        self.offline()
        self.assertEqual(cs.fetch_vsx(RA, DEC, 1.0)[1], "AAVSO VSX")


class TestPipelineInterface(_Base):
    def test_query_functions_keep_their_return_type(self):
        get, _ = _router(vizier=requests.Timeout(), vsx=_resp(json_data=VSX_JSON),
                         vsp=_resp(json_data=VSP_JSON))
        self.patch(get)
        self.assertIsInstance(query_vsx(RA, DEC, 2.925), list)
        comps = query_apass(RA, DEC, 11.0, radius_deg=1.5, n=2, dvmag=3.5)
        self.assertEqual(len(comps), 2)
        self.assertTrue({"ra", "dec", "vmag"} <= set(comps[0]))

    def test_comp_origin(self):
        apass = {"ra": 0, "dec": 0, "vmag": 11, "source": "APASS DR9", "chart": "APASS DR9"}
        vsp_a = {"ra": 0, "dec": 0, "vmag": 11, "source": "AAVSO VSP", "chart": "X1"}
        vsp_b = dict(vsp_a, chart="X2")
        self.assertEqual(comp_origin([apass, apass]), ("APASS DR9", "APASS DR9"))
        self.assertEqual(comp_origin([vsp_a, vsp_a]), ("AAVSO VSP", "X1"))
        self.assertEqual(comp_origin([vsp_a, vsp_b]), ("AAVSO VSP", None))
        self.assertEqual(comp_origin([apass, vsp_a]), ("AAVSO VSP + APASS DR9", None))
        self.assertEqual(comp_origin([{"ra": 0, "dec": 0, "vmag": 11}]),
                         ("APASS DR9", "APASS DR9"))


if __name__ == "__main__":
    unittest.main()
