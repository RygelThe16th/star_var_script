"""Tests for generate_pub_plot: the optional V / V-C / FWHM plot of a result."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import PUB_PLOT_NAME, generate_pub_plot

try:
    import matplotlib  # noqa: F401
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def _write_result(d: Path, n: int = 60, fwhm: bool = True) -> None:
    rows = ["# Star: V0873 Per",
            "# Ensemble V (flux mean of the APASS comp stars): 11.565",
            "# V_app = V_C + ensemble_V  (approximate apparent V magnitude)",
            "JD,V_C,V_app,err"]
    for i in range(n):
        jd = 2461345.01 + i * 0.002
        vc = -0.7 + 0.5 * math.exp(-((jd - 2461345.068) / 0.02) ** 2)
        rows.append(f"{jd:.6f},{vc:.4f},{vc + 11.565:.4f},0.05")
    (d / "photometry.csv").write_text("\n".join(rows) + "\n")
    if fwhm:
        f = ["# FWHM per frame", "JD,FWHM_arcsec,wFWHM_arcsec"]
        f += [f"{2461345.01 + i * 0.002:.6f},10.2,12.7" for i in range(n)]
        (d / "fwhm.csv").write_text("\n".join(f) + "\n")


class TestPubPlot(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.log: list[str] = []

    @unittest.skipUnless(HAS_MPL, "needs matplotlib")
    def test_three_panels_written(self):
        _write_result(self.dir)
        out = generate_pub_plot(self.dir, self.log.append)
        self.assertEqual(out, self.dir / PUB_PLOT_NAME)
        self.assertGreater(out.stat().st_size, 10_000)
        self.assertEqual(out.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertIn("Publication plot: V, V-C, FWHM → light_curve_pub.png", self.log)

    @unittest.skipUnless(HAS_MPL, "needs matplotlib")
    def test_without_fwhm(self):
        _write_result(self.dir, fwhm=False)
        self.assertIsNotNone(generate_pub_plot(self.dir, self.log.append))
        self.assertIn("Publication plot: V, V-C → light_curve_pub.png", self.log)

    def test_no_photometry(self):
        self.assertIsNone(generate_pub_plot(self.dir, self.log.append))
        self.assertIn("no photometry.csv", self.log[-1])

    def test_matplotlib_missing_is_reported_not_raised(self):
        _write_result(self.dir)
        with mock.patch.dict(sys.modules, {"plot_lightcurve": None}):
            self.assertIsNone(generate_pub_plot(self.dir, self.log.append))
        self.assertIn("pip install matplotlib", self.log[-1])
        self.assertFalse((self.dir / PUB_PLOT_NAME).exists())


if __name__ == "__main__":
    unittest.main()
