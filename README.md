# Seestar S30 Pro — Variable Star Photometry

Automated pipeline for variable star differential photometry with the **ZWO Seestar S30 Pro** (30 mm, f/5, Sony IMX585). Built on top of [Siril](https://siril.org) (open-source astronomical image processing) and driven entirely from its Script menu — no command line required.

> **This is the `seestar-linux` branch of a fork** ([RygelThe16th/star_var_script](https://github.com/RygelThe16th/star_var_script)) of [jperret21/star_var_script](https://github.com/jperret21/star_var_script) v0.1.7, with the fixes below while they wait to be merged upstream. Once they are, use the original again.
>
> | Change | Upstream PR |
> |---|---|
> | Runs with the Siril **AppImage** on Linux (its bundled `siril-cli` is run through the AppImage) | [#3](https://github.com/jperret21/star_var_script/pull/3) |
> | When **VizieR** (CDS) does not answer, variable stars come from the AAVSO VSX API and comparison stars from an AAVSO VSP chart; Siril's `findcompstars` (SIMBAD) no longer waits forever | [#4](https://github.com/jperret21/star_var_script/pull/4) |
> | Catalogue answers are **cached** in `~/.cache/star_var_script/` and reused, also offline | not yet |
> | **Settings file** for VizieR mirrors and timeouts; `test_connections.py` checks AAVSO and tells whether the fallback route works | not yet |
> | Optional **V / V−C / FWHM plot** of a run (`light_curve_pub.png`) | not yet |
> | The target of an Argos `photometry_selection.json` is **selected automatically** in the star list | not yet |
>
> **Install**
> 1. On GitHub choose the `seestar-linux` branch, then *Code → Download ZIP* and unpack it (or `git clone -b seestar-linux https://github.com/RygelThe16th/star_var_script.git`).
> 2. In Siril: *Preferences → Scripts*, add the unpacked folder to the script paths (replace the folder of the original, if any), then run `seestar_varstar_siril.py` from the Script menu.
> 3. Only for the V / V−C / FWHM plot: install matplotlib and scipy into Siril's Python once — `~/.local/share/siril/venv/bin/python -m pip install matplotlib scipy` (Linux; the Siril AppImage ships without them).
> 4. Optional: copy `config.example.json` to `~/.config/star_var_script/config.json` and keep only what you change (VizieR mirrors, source order, timeouts, cache age).
> 5. If catalogues fail, run `python3 test_connections.py`: it ends with "normal route (VizieR)", "fallback route (AAVSO)" or "cached fields only".

---

## Features

- **Green-channel photometry** — the Bayer green, (G1+G2)/2, extracted with `seqextract_Green` before registration; reported to AAVSO as `TG`
- **Automatic target discovery** — queries VizieR/VSX to find all known variable stars in the field
- **Ensemble differential photometry** — APASS comparison stars fetched automatically from VizieR, or the AAVSO VSP comparison and check stars chosen in [Argos](https://github.com/jperret21/argos) for an Argos session
- **Approximate apparent V magnitude** — computed from APASS catalog magnitudes of comp stars
- **Per-frame FWHM** — extracted from the Siril registration sequence and exported to CSV
- **AAVSO-ready export** — AAVSO Extended Format CSV, ready for WebObs submission
- **Publication-quality plots** — light curve, diagnostics, Lomb-Scargle periodogram, phase fold
- **Interactive notebook** — Jupyter notebook for in-depth session analysis

## Pipeline overview

```
Seestar FITS frames
    ↓
  Siril: calibrate → green channel → register → plate-solve → aperture photometry
    ↓
  photometry.csv  ·  fwhm.csv  ·  aavso.csv
    ↓
  varstar_postprod/plot_lightcurve.py  ·  analysis.ipynb

```

## Repository

```
star_var_script/
├── seestar_varstar_siril.py   Main script (run from Siril Script menu)
├── pipeline.py                Pipeline logic
├── varstar_postprod/          Post-processing tools (independent of Siril)
│   ├── plot_lightcurve.py     CLI plot generator
│   └── analysis.ipynb         Interactive analysis notebook
└── docs/
    └── photometry_guide.md    Full technical reference
```

## Quick start

1. Open Siril and set your session folder (the one containing `lights/`)
2. **Script → Run Script → `seestar_varstar_siril.py`**
3. Load the session, pick a variable star, enter your AAVSO observer code, click **Run** (comparison stars are fetched automatically)
4. Results are written to `results/StarName/`

After the pipeline completes, generate plots:

```bash
cd varstar_postprod
python plot_lightcurve.py /path/to/results/StarName --bin 5 --sigma 3 --format both
```

## Credits

Photometry pipeline powered by [Siril](https://siril.org) (© Siril team, GPLv3).
Catalog queries via [VizieR](https://vizier.cds.unistra.fr) (CDS Strasbourg) — VSX and APASS DR9.

## Documentation

Full technical reference (algorithms, step-by-step, output formats, Siril commands, AAVSO guide):
→ [docs/photometry_guide.md](docs/photometry_guide.md)
