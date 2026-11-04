"""
catalog_sources.py — variable-star and comparison-star catalogue queries.

Each catalogue has a primary source (VizieR) and a fallback (the AAVSO APIs):

  variable stars     VizieR B/vsx/vsx       →  AAVSO VSX  (vsx.aavso.org api.list)
  comparison stars   VizieR II/336/apass9   →  AAVSO VSP  (app.aavso.org/vsp/api/chart/)

A source is tried only when the previous one times out or fails; an empty but
valid answer is kept. Answers are cached (see CACHE_DIR) and reused for any
query they cover, so a field queried once also works offline. No Siril
dependency: it can be tried from a terminal (python3 catalog_sources.py RA DEC).

AAVSO API references:
  VSX  https://archive.aavso.org/apis-aavso-resources  (api.list: ra, dec,
       radius in degrees, tomag; format=json)
  VSP  https://archive.aavso.org/api-vsp  (ra, dec, fov in arcmin, maglimit)
"""

from __future__ import annotations

import datetime
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

VIZIER_URL    = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
# www.aavso.org/vsx is behind a Cloudflare browser check; vsx.aavso.org serves
# the same API without it.
AAVSO_VSX_URL = "https://vsx.aavso.org/index.php"
AAVSO_VSP_URL = "https://app.aavso.org/vsp/api/chart/"

# Order in which the sources are tried ("vizier", "aavso").
SOURCE_ORDER: tuple[str, ...] = ("vizier", "aavso")
TIMEOUT = 20          # s, per request
# A source that timed out or failed is skipped for this long, so that one
# pipeline run does not wait for the same dead server at every query.
RETRY_AFTER = 600     # s

VSX_MAX_ROWS = 500    # as VizieR -out.max: the nearest stars are kept
APASS_MAX_ROWS = 200
# Above a 180′ field VSP accepts no maglimit fainter than 12 (HTTP 400).
VSP_MAX_FOV = 180.0   # arcmin

APASS_LABEL = "APASS DR9"
VSP_LABEL   = "AAVSO VSP"

SOURCE_NAMES = {
    ("vsx", "vizier"):   "VizieR VSX",
    ("vsx", "aavso"):    "AAVSO VSX",
    ("comps", "vizier"): "VizieR APASS",
    ("comps", "aavso"):  "AAVSO VSP",
}

Log = Optional[Callable[[str], None]]


# Answers are kept in CACHE_DIR (None: $XDG_CACHE_HOME/star_var_script/catalog,
# shared by all sessions) and reused, without a query, for any query inside
# their area and magnitude range while younger than CACHE_MAX_AGE_DAYS. Older
# ones are used only when every source fails.
CACHE_DIR: Optional[Path] = None
CACHE_MAX_AGE_DAYS = 30.0
_CACHE_VERSION = 1

class CatalogError(Exception):
    """A source did not answer, or answered something unreadable."""


_failed_at: dict[str, float] = {}


def source_down(source: str) -> bool:
    """True while *source* is skipped after a recent failure."""
    t = _failed_at.get(source)
    return t is not None and time.monotonic() - t < RETRY_AFTER


def reset_failures() -> None:
    _failed_at.clear()


def _get(url: str, params: dict) -> "requests.Response":
    if not HAS_REQUESTS:
        raise CatalogError("the requests module is not available")
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.Timeout:
        raise CatalogError(f"no answer within {TIMEOUT} s") from None
    except requests.HTTPError as e:
        raise CatalogError(f"HTTP {e.response.status_code}") from None
    except requests.RequestException as e:
        raise CatalogError(type(e).__name__) from None
    return r


def _json(r: "requests.Response"):
    try:
        return r.json()
    except ValueError:
        raise CatalogError("the answer is not JSON") from None


def _sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Angular separation in degrees."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = (math.sin(d1) * math.sin(d2)
         + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _sexa_to_deg(text: str, hours: bool) -> float:
    """'02:46:49.70' (hours) or '+41:10:11.6' (degrees) → degrees."""
    s = text.strip()
    sign = -1.0 if s.startswith("-") else 1.0
    parts = [float(p) for p in s.lstrip("+-").split(":")]
    value = parts[0] + (parts[1] if len(parts) > 1 else 0) / 60 \
        + (parts[2] if len(parts) > 2 else 0) / 3600
    return sign * value * (15.0 if hours else 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# VizieR
# ─────────────────────────────────────────────────────────────────────────────

def vizier_tsv(catalog: str, ra: float, dec: float,
               radius_arcmin: float, columns: list[str],
               filters: dict | None = None, max_rows: int = 500) -> list[list[str]]:
    """Data rows of a VizieR cone search. Raises CatalogError."""
    params: dict = {
        "-source": catalog,
        "-c": f"{ra:.6f} {dec:+.6f}",
        "-c.r": str(radius_arcmin),
        "-c.u": "arcmin",
        "-out": ",".join(columns),
        "-out.max": str(max_rows),
        # Sort by distance from field centre: without this, VizieR returns rows
        # in catalog order and -out.max can truncate away the central stars
        # (e.g. RR Lyr lost among >500 KIC variables in the Kepler field).
        "-sort": "_r",
    }
    if filters:
        params.update(filters)
    r = _get(VIZIER_URL, params)
    # TSV layout: #comments, column names, units, dashed separator, data.
    rows = []
    in_data = False
    for line in r.text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if line.startswith("-"):
            in_data = True
            continue
        if in_data:
            rows.append(line.split("\t"))
    if not in_data and "<html" in r.text[:500].lower():
        raise CatalogError("the answer is not a VizieR table")
    return rows


def vizier_vsx(ra: float, dec: float, radius_deg: float) -> list[dict]:
    """Variable stars from VizieR B/vsx/vsx."""
    rows = vizier_tsv(
        "B/vsx/vsx", ra, dec, radius_deg * 60,
        ["Name", "Type", "Period", "max", "RAJ2000", "DEJ2000"],
    )
    stars = []
    for parts in rows:
        if len(parts) < 6:
            continue
        try:
            name     = parts[0].strip()
            var_type = parts[1].strip() or "?"
            period   = parts[2].strip()
            mag_str  = parts[3].strip()
            ra_s     = parts[4].strip()
            dec_s    = parts[5].strip()
            if not ra_s or not dec_s:
                continue
            stars.append({
                "name":     name,
                "ra":       float(ra_s),
                "dec":      float(dec_s),
                "mag":      float(mag_str) if mag_str else 99.0,
                "var_type": var_type,
                "period":   f"{float(period):.4f} d" if period else "—",
                "dist":     0.0,
            })
        except (ValueError, IndexError):
            continue
    return stars


def vizier_apass(ra: float, dec: float, target_mag: float,
                 radius_deg: float, dvmag: float) -> list[dict]:
    """APASS DR9 stars from VizieR II/336/apass9, unsorted.

    The magnitude range is filtered by VizieR: rows come sorted by distance
    and capped at max_rows, so filtering here instead would only see the few
    hundred nearest (mostly faint) stars — for a bright target, none in range.
    """
    rows = vizier_tsv(
        "II/336/apass9", ra, dec, radius_deg * 60,
        ["RAJ2000", "DEJ2000", "Vmag", "e_Vmag"],
        filters={"e_Vmag": "<0.05",
                 "Vmag": f"{target_mag - dvmag:.2f}..{target_mag + dvmag:.2f}"},
        max_rows=APASS_MAX_ROWS,
    )
    comps = []
    for parts in rows:
        if len(parts) < 3:
            continue
        try:
            comps.append({"ra": float(parts[0].strip()),
                          "dec": float(parts[1].strip()),
                          "vmag": float(parts[2].strip()),
                          "source": APASS_LABEL, "chart": APASS_LABEL})
        except (ValueError, IndexError):
            continue
    return comps


# ─────────────────────────────────────────────────────────────────────────────
# AAVSO
# ─────────────────────────────────────────────────────────────────────────────

_LEADING_NUMBER = re.compile(r"^\s*[<>]?\s*(-?\d+(?:\.\d+)?)")


def _vsx_mag(text: Optional[str]) -> float:
    """'10.8 V', '<15.2 G', '13.1: r' → leading number, 99.0 if none."""
    m = _LEADING_NUMBER.match(text or "")
    return float(m.group(1)) if m else 99.0


def aavso_vsx(ra: float, dec: float, radius_deg: float) -> list[dict]:
    """Variable stars from the AAVSO VSX api.list, the VSX_MAX_ROWS nearest."""
    data = _json(_get(AAVSO_VSX_URL, {
        "view": "api.list", "ra": f"{ra:.6f}", "dec": f"{dec:+.6f}",
        "radius": f"{radius_deg:.6f}", "format": "json",
    }))
    try:
        objs = (data.get("VSXObjects") or {}).get("VSXObject") or []
    except AttributeError:
        raise CatalogError("unexpected VSX answer") from None
    if isinstance(objs, dict):       # a single star is not wrapped in a list
        objs = [objs]
    stars = []
    for o in objs:
        try:
            s_ra, s_dec = float(o["RA2000"]), float(o["Declination2000"])
        except (KeyError, TypeError, ValueError):
            continue
        period = o.get("Period")
        try:
            period_s = f"{float(period):.4f} d" if period else "—"
        except ValueError:
            period_s = "—"
        stars.append({
            "name":     (o.get("Name") or "?").strip(),
            "ra":       s_ra,
            "dec":      s_dec,
            "mag":      _vsx_mag(o.get("MaxMag")),
            "var_type": (o.get("VariabilityType") or "").strip() or "?",
            "period":   period_s,
            "dist":     0.0,
            "auid":     o.get("AUID") or "",
        })
    stars.sort(key=lambda s: _sep_deg(ra, dec, s["ra"], s["dec"]))
    return stars[:VSX_MAX_ROWS]


def aavso_vsp(ra: float, dec: float, target_mag: float,
              radius_deg: float, dvmag: float) -> list[dict]:
    """Sequence stars of an AAVSO VSP chart centred on (ra, dec), unsorted.

    The chart is a square of side 2·radius, at most VSP_MAX_FOV; only the stars
    within radius_deg are kept, as for a cone search. Stars without a V
    magnitude are left out; e_vmag is None where VSP gives no error.
    """
    fov = min(2 * radius_deg * 60, VSP_MAX_FOV)
    maglimit = math.ceil(round((target_mag + dvmag) * 10, 6)) / 10
    data = _json(_get(AAVSO_VSP_URL, {
        "format": "json", "ra": f"{ra:.6f}", "dec": f"{dec:+.6f}",
        "fov": f"{fov:g}", "maglimit": f"{maglimit:g}",
    }))
    if not isinstance(data, dict) or "photometry" not in data:
        raise CatalogError("unexpected VSP answer")
    chart = data.get("chartid") or None
    comps = []
    for p in data.get("photometry") or []:
        try:
            c_ra  = _sexa_to_deg(str(p["ra"]), hours=True)
            c_dec = _sexa_to_deg(str(p["dec"]), hours=False)
        except (KeyError, ValueError):
            continue
        v = next((b for b in p.get("bands") or [] if b.get("band") == "V"), None)
        if v is None or v.get("mag") is None:
            continue
        if _sep_deg(ra, dec, c_ra, c_dec) > radius_deg:
            continue
        comps.append({"ra": c_ra, "dec": c_dec, "vmag": float(v["mag"]),
                      "e_vmag": v.get("error"),
                      "auid": p.get("auid") or "", "label": str(p.get("label") or ""),
                      "source": VSP_LABEL, "chart": chart})
    return comps


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────
#
# One JSON file per query: the rows a source returned, before any filtering,
# with the area and magnitude range they are complete for. Sources cut their
# answer to the nearest stars (VSX 500, APASS 200): a capped answer is complete
# only out to its farthest star, which is then the radius it covers.

def cache_dir() -> Path:
    if CACHE_DIR is not None:
        return Path(CACHE_DIR)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "star_var_script" / "catalog"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _covered_radius(rows: list[dict], ra: float, dec: float,
                    radius_deg: float, cap: Optional[int]) -> float:
    if cap is not None and len(rows) >= cap:
        return max((_sep_deg(ra, dec, r["ra"], r["dec"]) for r in rows), default=0.0)
    return radius_deg


def _cache_save(kind: str, source: str, name: str, ra: float, dec: float,
                radius_deg: float, covered_deg: float,
                mag_lo: Optional[float], mag_hi: Optional[float],
                rows: list[dict], log: Callable[[str], None]) -> None:
    entry = {
        "version": _CACHE_VERSION, "kind": kind, "source": source, "name": name,
        "ra": ra, "dec": dec, "radius_deg": radius_deg, "covered_deg": covered_deg,
        "mag_lo": mag_lo, "mag_hi": mag_hi,
        "fetched_utc": _now().isoformat(timespec="seconds"), "rows": rows,
    }
    lo = "none" if mag_lo is None else f"{mag_lo:.2f}"
    hi = "none" if mag_hi is None else f"{mag_hi:.2f}"
    # The same query again overwrites its file.
    fname = f"{kind}_{source}_{ra:09.5f}_{dec:+09.5f}_r{radius_deg:.4f}_m{lo}_{hi}.json"
    d = cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (fname + ".tmp")
        tmp.write_text(json.dumps(entry), encoding="utf-8")
        os.replace(tmp, d / fname)
    except OSError as e:
        log(f"Catalogue cache: not saved ({e})")


def _age_days(entry: dict) -> float:
    try:
        t = datetime.datetime.fromisoformat(entry["fetched_utc"])
    except (KeyError, TypeError, ValueError):
        return math.inf
    return (_now() - t).total_seconds() / 86400


def _cache_lookup(kind: str, order: Sequence[str], ra: float, dec: float,
                  radius_deg: float, mag_lo: Optional[float],
                  mag_hi: Optional[float]) -> list[dict]:
    """Cached answers that cover the query: by source order, then newest first."""
    found = []
    try:
        files = list(cache_dir().glob(f"{kind}_*.json"))
    except OSError:
        return []
    for f in files:
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
            if (e.get("version") != _CACHE_VERSION or e.get("kind") != kind
                    or e.get("source") not in order):
                continue
            if (_sep_deg(ra, dec, float(e["ra"]), float(e["dec"])) + radius_deg
                    > float(e["covered_deg"]) + 1e-6):
                continue
            if mag_lo is not None and e.get("mag_lo") is not None \
                    and float(e["mag_lo"]) > mag_lo + 1e-6:
                continue
            if mag_hi is not None and e.get("mag_hi") is not None \
                    and float(e["mag_hi"]) < mag_hi - 1e-6:
                continue
            if mag_lo is None and e.get("mag_lo") is not None:
                continue
            if mag_hi is None and e.get("mag_hi") is not None:
                continue
            if not isinstance(e.get("rows"), list):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        found.append(e)
    found.sort(key=lambda e: (list(order).index(e["source"]), _age_days(e)))
    return found


def _cached_rows(e: dict, ra: float, dec: float, radius_deg: float,
                 mag_lo: Optional[float], mag_hi: Optional[float],
                 cap: Optional[int]) -> list[dict]:
    """The rows of a cached answer that this query would have returned."""
    rows = []
    for r in e["rows"]:
        if _sep_deg(ra, dec, r["ra"], r["dec"]) > radius_deg:
            continue
        v = r.get("vmag")
        if v is not None and ((mag_lo is not None and v < mag_lo)
                              or (mag_hi is not None and v > mag_hi)):
            continue
        rows.append(r)
    rows.sort(key=lambda r: _sep_deg(ra, dec, r["ra"], r["dec"]))
    return rows[:cap] if cap is not None else rows


# ─────────────────────────────────────────────────────────────────────────────
# Fallback
# ─────────────────────────────────────────────────────────────────────────────

class _Call:
    """One source for one query: how to ask it and what its answer covers."""

    def __init__(self, fetch: Callable[[], list[dict]],
                 mag_lo: Optional[float] = None, mag_hi: Optional[float] = None,
                 cap: Optional[int] = None, max_radius: Optional[float] = None):
        self.fetch, self.mag_lo, self.mag_hi = fetch, mag_lo, mag_hi
        self.cap, self.max_radius = cap, max_radius


def _first_answer(kind: str, calls: dict[str, _Call],
                  order: Optional[Sequence[str]], on_log: Log, *,
                  ra: float, dec: float, radius_deg: float,
                  mag_lo: Optional[float] = None, mag_hi: Optional[float] = None,
                  cap: Optional[int] = None, refresh: bool = False
                  ) -> tuple[list[dict], Optional[str]]:
    """Rows from the cache or from the first source in *order* that answers.

    mag_lo/mag_hi/cap describe the query (what the caller needs); the _Call of
    each source what its own answer covers. refresh skips the fresh cache.
    """
    log = on_log or (lambda _msg: None)
    for s in order or ():
        if s not in calls:
            log(f"Catalogue: unknown source '{s}' ignored")
    order = [s for s in (order or SOURCE_ORDER) if s in calls] or list(SOURCE_ORDER)
    cached = _cache_lookup(kind, order, ra, dec, radius_deg, mag_lo, mag_hi)

    def from_cache(e: dict, why: str) -> tuple[list[dict], Optional[str]]:
        rows = _cached_rows(e, ra, dec, radius_deg, mag_lo, mag_hi, cap)
        log(f"{e['name']}: {len(rows)} stars from {why} "
            f"(fetched {e['fetched_utc'][:16].replace('T', ' ')} UTC, "
            f"{_age_days(e):.0f} days old)")
        return rows, e["name"]

    fresh = [e for e in cached if _age_days(e) <= CACHE_MAX_AGE_DAYS]
    if fresh and not refresh:
        return from_cache(fresh[0], "the cache")

    for source in order:
        name = SOURCE_NAMES[(kind, source)]
        if source_down(source):
            log(f"{name}: skipped (failed less than {RETRY_AFTER // 60} min ago)")
            continue
        call = calls[source]
        try:
            rows = call.fetch()
        except CatalogError as e:
            _failed_at[source] = time.monotonic()
            log(f"{name}: failed ({e})")
            continue
        _failed_at.pop(source, None)
        log(f"{name}: {len(rows)} stars")
        covered = _covered_radius(rows, ra, dec, radius_deg, call.cap)
        if call.max_radius is not None:
            covered = min(covered, call.max_radius)
        _cache_save(kind, source, name, ra, dec, radius_deg, covered,
                    call.mag_lo, call.mag_hi, rows, log)
        return rows, name

    if cached:
        return from_cache(cached[0], "the cache, as no source answered")
    log("Catalogue: no source answered, and nothing cached for this field")
    return [], None


def fetch_vsx(ra: float, dec: float, radius_deg: float, *,
              order: Optional[Sequence[str]] = None, on_log: Log = None,
              refresh: bool = False) -> tuple[list[dict], Optional[str]]:
    """Variable stars within radius_deg, and the name of the source used.

    refresh: query the sources even when the cache covers the field.
    """
    return _first_answer("vsx", {
        "vizier": _Call(lambda: vizier_vsx(ra, dec, radius_deg), cap=VSX_MAX_ROWS),
        "aavso":  _Call(lambda: aavso_vsx(ra, dec, radius_deg), cap=VSX_MAX_ROWS),
    }, order, on_log, ra=ra, dec=dec, radius_deg=radius_deg,
        cap=VSX_MAX_ROWS, refresh=refresh)


def fetch_comps(ra: float, dec: float, target_mag: float, *,
                radius_deg: float = 1.0, n: int = 6, dvmag: float = 2.0,
                order: Optional[Sequence[str]] = None, on_log: Log = None,
                refresh: bool = False) -> tuple[list[dict], Optional[str]]:
    """Comparison stars closest in V to target_mag, and the source used.

    Kept: |V − target_mag| ≤ dvmag, V error < 0.05 where known, not within 5″
    of (ra, dec). Each star has ra, dec, vmag, plus source (APASS_LABEL or
    VSP_LABEL), chart (the VSP chart ID, or APASS_LABEL) and, from VSP, auid.
    """
    # The magnitude range as VizieR is asked for it (2 decimals).
    lo = float(f"{target_mag - dvmag:.2f}")
    hi = float(f"{target_mag + dvmag:.2f}")
    maglimit = math.ceil(round((target_mag + dvmag) * 10, 6)) / 10
    rows, name = _first_answer("comps", {
        "vizier": _Call(lambda: vizier_apass(ra, dec, target_mag, radius_deg, dvmag),
                        mag_lo=lo, mag_hi=hi, cap=APASS_MAX_ROWS),
        "aavso":  _Call(lambda: aavso_vsp(ra, dec, target_mag, radius_deg, dvmag),
                        mag_hi=maglimit, max_radius=VSP_MAX_FOV / 120),
    }, order, on_log, ra=ra, dec=dec, radius_deg=radius_deg,
        mag_lo=lo, mag_hi=hi, refresh=refresh)
    comps = []
    for c in rows:
        if abs(c["vmag"] - target_mag) > dvmag:
            continue
        err = c.get("e_vmag")
        if err is not None and err >= 0.05:
            continue
        sep = ((c["ra"] - ra) * 3600) ** 2 + ((c["dec"] - dec) * 3600) ** 2
        if sep < 25:  # < 5 arcsec from target
            continue
        comps.append(c)
    comps.sort(key=lambda c: abs(c["vmag"] - target_mag))
    return comps[:n], name


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Try the catalogue sources from a terminal.")
    ap.add_argument("ra", type=float, help="degrees")
    ap.add_argument("dec", type=float, help="degrees")
    ap.add_argument("--radius", type=float, default=1.5, help="degrees (default 1.5)")
    ap.add_argument("--mag", type=float, default=11.0, help="target V (default 11)")
    ap.add_argument("--dvmag", type=float, default=3.5)
    ap.add_argument("--refresh", action="store_true", help="ignore the fresh cache")
    ap.add_argument("--order", default=",".join(SOURCE_ORDER),
                    help="comma-separated sources (default vizier,aavso)")
    args = ap.parse_args()
    order = [s.strip() for s in args.order.split(",") if s.strip()]
    stars, src = fetch_vsx(args.ra, args.dec, args.radius, order=order, on_log=print,
                            refresh=args.refresh)
    for s in stars[:10]:
        print(f"  {s['name']:<30} {s['ra']:10.5f} {s['dec']:+10.5f}  "
              f"max {s['mag']:5.2f}  {s['var_type']:<8} {s['period']}")
    comps, src = fetch_comps(args.ra, args.dec, args.mag, radius_deg=args.radius,
                             n=10, dvmag=args.dvmag, order=order, on_log=print,
                             refresh=args.refresh)
    for c in comps:
        print(f"  {c.get('auid', ''):<12} {c['ra']:10.5f} {c['dec']:+10.5f}  "
              f"V {c['vmag']:6.3f}  {c['source']} {c['chart'] or ''}")
