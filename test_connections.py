#!/usr/bin/env python3
"""
Connection & API diagnostics for seestar_varstar_siril.py
Run this first if you see catalog or network errors.

Usage:
    python3 test_connections.py
    # or from Siril: Script > Run Script > test_connections.py

The catalogue checks use catalog_sources.py, so they follow the settings file
(VizieR mirrors, timeouts: see config.example.json). The verdict says whether
the main script can run through VizieR, through the AAVSO fallback, or only
on fields already in the catalogue cache.
"""

import os
import shutil
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog_sources as cs  # noqa: E402

# M81 field coordinates used as test target
TEST_RA  = 149.28
TEST_DEC = 69.09

PASS = "✓  PASS"
FAIL = "✗  FAIL"
INFO = "·  INFO"

results = []


def check(name, ok, detail=""):
    """A check that counts in the result."""
    tag = PASS if ok else FAIL
    line = f"  {tag}  {name}"
    if detail:
        line += f"\n         {detail}"
    print(line)
    results.append(ok)
    return ok


def info(name, detail=""):
    """A note that does not count in the result."""
    line = f"  {INFO}  {name}"
    if detail:
        line += "\n         " + detail.replace("\n", "\n         ")
    print(line)


# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("  Seestar Variable Star — Connection Diagnostics")
print("=" * 60)

# ── 1. Python & requests ──────────────────────────────────────────────────────
print("\n[1] Python environment")
check("Python version", True, f"{sys.version.split()[0]}  ({sys.executable})")
check("requests module", cs.HAS_REQUESTS,
      "pip install requests" if not cs.HAS_REQUESTS else "")

# ── 2. Basic internet ─────────────────────────────────────────────────────────
print("\n[2] Internet connectivity")
try:
    with socket.create_connection(("8.8.8.8", 53), timeout=5):
        pass
    check("Internet (DNS reachable)", True)
except OSError as e:
    check("Internet (DNS reachable)", False, str(e))

# ── 3. Settings ───────────────────────────────────────────────────────────────
print("\n[3] Settings")
cfg = cs.config_path()
info("Settings file", f"{cfg}" + ("" if cfg.exists()
                                  else "  (none — defaults; see config.example.json)"))
for w in cs.CONFIG_WARNINGS:
    check("Settings file entry", False, w)
info("Catalogue sources",
     f"order: {' → '.join(cs.SOURCE_ORDER)}\n"
     f"VizieR mirrors: {', '.join(cs._host(m) for m in cs.VIZIER_MIRRORS)}\n"
     f"timeouts: connect {cs.TIMEOUT_CONNECT:g} s, answer {cs.TIMEOUT_READ:g} s")

# ── 4. VizieR (VSX and APASS) ─────────────────────────────────────────────────
print("\n[4] VizieR  (VSX variable stars, APASS comparison stars)")
vizier_ok = None   # first mirror that answered
for base in cs.VIZIER_MIRRORS:
    host = cs._host(base)
    try:
        r = cs._get(base + cs.VIZIER_PATH, {
            "-source": "B/vsx/vsx", "-c": f"{TEST_RA} {TEST_DEC}",
            "-c.r": "30", "-c.u": "arcmin",
            "-out": "Name,Type,RAJ2000,DEJ2000", "-out.max": "3"})
        rows = [ln for ln in r.text.splitlines()
                if ln.strip() and not ln.startswith(("#", "-"))]
        info(f"VizieR VSX at {host}", f"answered — {max(0, len(rows) - 2)} stars "
             "(M81 field, r=30 arcmin)")
        vizier_ok = vizier_ok or base
    except cs.CatalogError as e:
        info(f"VizieR VSX at {host}", f"failed: {e}")
check("VizieR VSX (any mirror)", vizier_ok is not None,
      f"using {cs._host(vizier_ok)}" if vizier_ok
      else "no mirror answered — the AAVSO fallback is used instead")
if vizier_ok:
    try:
        cs.VIZIER_MIRRORS = [vizier_ok]
        comps = cs.vizier_apass(TEST_RA, TEST_DEC, 10.0, 1 / 3, 3.0)
        check("VizieR APASS", True, f"{len(comps)} comparison stars at "
              f"{cs._host(vizier_ok)} (M81 field, r=20 arcmin)")
        apass_ok = True
    except cs.CatalogError as e:
        check("VizieR APASS", False, str(e))
        apass_ok = False
else:
    info("VizieR APASS", "not tried — no VizieR mirror answered")
    apass_ok = False

# ── 5. AAVSO (fallback) ───────────────────────────────────────────────────────
print("\n[5] AAVSO  (fallback: VSX api.list, VSP charts)")
try:
    stars = cs.aavso_vsx(TEST_RA, TEST_DEC, 0.5)
    vsx_ok = check("AAVSO VSX", True,
                   f"{len(stars)} variable stars at {cs._host(cs.AAVSO_VSX_URL)} "
                   "(M81 field, r=30 arcmin)")
except cs.CatalogError as e:
    vsx_ok = check("AAVSO VSX", False, f"{cs._host(cs.AAVSO_VSX_URL)}: {e}")
try:
    comps = cs.aavso_vsp(TEST_RA, TEST_DEC, 10.0, 0.25, 3.0)
    charts = {c["chart"] for c in comps}
    vsp_ok = check("AAVSO VSP", True,
                   f"{len(comps)} sequence stars"
                   + (f", chart {charts.pop()}" if len(charts) == 1 else "")
                   + " (M81 field, 30′ chart)")
except cs.CatalogError as e:
    vsp_ok = check("AAVSO VSP", False, f"{cs._host(cs.AAVSO_VSP_URL)}: {e}")

# ── 6. Catalogue cache ────────────────────────────────────────────────────────
print("\n[6] Catalogue cache")
cache = cs.cache_dir()
cached = {"vsx": 0, "comps": 0}
for f in (cache.glob("*.json") if cache.is_dir() else []):
    kind = f.name.split("_", 1)[0]
    if kind in cached:
        cached[kind] += 1
info("Cached answers", f"{cache}\n{cached['vsx']} variable-star and "
     f"{cached['comps']} comparison-star queries")

# ── 7. Siril ──────────────────────────────────────────────────────────────────
print("\n[7] Siril integration")
in_siril = False
try:
    sys.path.insert(0, "/Applications/Siril.app/Contents/Resources/share/siril/python_module")
    from sirilpy import SirilInterface
except ImportError:
    info("sirilpy module",
         "not available here: this is normal when run from a terminal.\n"
         "It is there when run from Siril (Script > Run Script).")
else:
    try:
        iface = SirilInterface()
        iface.connect()
        in_siril = True
        check("sirilpy connect", True, "Siril is running and connected")
        try:
            check("Siril working directory", True, str(iface.get_wd()))
        except Exception:
            check("Siril working directory", True, "(get_wd not available — use Browse)")
    except Exception as e:
        check("sirilpy connect", False,
              f"{e}\nOpen Siril first, then run this script from Script > Run Script")

# The Siril AppImage carries its own siril-cli, run through the AppImage
# (it is on PATH inside it, but cannot start on its own): no install needed.
appimage, appdir = os.environ.get("APPIMAGE"), os.environ.get("APPDIR")
cli = next((c for c in ["siril-cli", "/Applications/Siril.app/Contents/MacOS/siril-cli"]
            if shutil.which(c)), None)
if appimage and appdir and cli and Path(shutil.which(cli)).resolve().is_relative_to(
        Path(appdir).resolve()):
    check("siril-cli", True, f"in the Siril AppImage, run as: {appimage} siril-cli")
elif cli:
    check("siril-cli", True, shutil.which(cli))
elif not in_siril:
    info("siril-cli", "not on PATH: this is normal from a terminal with the Siril\n"
         "AppImage, whose siril-cli is inside it and is used when the main\n"
         "script runs from Siril. Otherwise install Siril 1.4+ (siril.org).")
else:
    check("siril-cli", False, "Install Siril 1.4+ from https://siril.org")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = sum(results)
total  = len(results)
print(f"  Result: {passed}/{total} checks passed")
if vizier_ok and apass_ok:
    print(f"  Catalogues: VizieR answers ({cs._host(vizier_ok)}) — normal route.")
elif vsx_ok and vsp_ok:
    print("  Catalogues: VizieR does not answer, the AAVSO fallback does —\n"
          "  the main script can run through the fallback route (AAVSO).")
else:
    print("  Catalogues: neither VizieR nor AAVSO answers — only fields already\n"
          f"  in the cache can be processed ({cached['vsx']} + {cached['comps']} "
          "cached queries).")
print("=" * 60)
