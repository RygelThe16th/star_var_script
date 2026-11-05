"""Tests for argos_target_in: the star of the list an Argos selection is for."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import unittest

from pipeline import argos_target_in

SEL = {"targets": [{"name": "V0873 Per", "auid": "000-BLX-752",
                    "ra_deg_j2000": 41.78421, "dec_deg_j2000": 41.37553}]}

# starsv.csv / VSX rows: no AUID, coordinates rounded differently
STARS = [
    {"name": "Gaia DR3 336795293651761408", "ra": 41.66664, "dec": 41.33512},
    {"name": "V0873 Per", "ra": 41.784208, "dec": 41.375528},
    {"name": "ZTF J024910.89+412928.0", "ra": 42.29537, "dec": 41.49111},
]


class TestArgosTarget(unittest.TestCase):
    def test_target_found_by_coordinates(self):
        name, star, sep = argos_target_in(SEL, STARS)
        self.assertEqual(name, "V0873 Per")
        self.assertIs(star, STARS[1])
        self.assertLess(sep, 0.1)

    def test_nearest_of_two_close_stars(self):
        near = {"name": "near", "ra": 41.78421 + 2 / 3600, "dec": 41.37553}
        far = {"name": "far", "ra": 41.78421, "dec": 41.37553 + 20 / 3600}
        self.assertIs(argos_target_in(SEL, [far, near])[1], near)

    def test_beyond_30_arcsec_is_not_the_target(self):
        off = [{"name": "x", "ra": 41.78421, "dec": 41.37553 + 31 / 3600}]
        name, star, sep = argos_target_in(SEL, off)
        self.assertEqual(name, "V0873 Per")
        self.assertIsNone(star)
        self.assertAlmostEqual(sep, 31, places=3)

    def test_empty_list(self):
        self.assertEqual(argos_target_in(SEL, []), ("V0873 Per", None, math.inf))

    def test_unusable_selection(self):
        for sel in (None, {}, {"targets": []},
                    {"targets": [{"name": "X", "ra_deg_j2000": None}]}):
            self.assertEqual(argos_target_in(sel, STARS), (None, None, math.inf))

    def test_name_falls_back_to_auid_then_coordinates(self):
        t = dict(SEL["targets"][0], name="")
        self.assertEqual(argos_target_in({"targets": [t]}, STARS)[0], "000-BLX-752")
        t = dict(t, auid=None)
        self.assertEqual(argos_target_in({"targets": [t]}, STARS)[0],
                         "41.78421 +41.37553")

    def test_star_without_coordinates_skipped(self):
        stars = [{"name": "broken", "ra": "?", "dec": None}] + STARS
        self.assertIs(argos_target_in(SEL, stars)[1], STARS[1])


if __name__ == "__main__":
    unittest.main()
