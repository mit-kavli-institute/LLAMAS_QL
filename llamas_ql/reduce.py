"""Trimmed QL reduction-orchestration layer.
Subset of the llamas-pyjamas reduce.py: trace generation, extraction,
reference-arc wavelength application, and flat-field generation/application.
Sky subtraction, traditional/CRR cube construction, and main() are intentionally
omitted; build cubes via Cube.SimpleCubeConstructor.
"""

import os
import argparse
import pickle
import traceback
import multiprocessing
import logging
import gc
from datetime import datetime

from llamas_ql.Trace.traceLlamasMaster import run_ray_tracing
from llamas_ql.config import BASE_DIR, OUTPUT_DIR, DATA_DIR, CALIB_DIR, BIAS_DIR, LUT_DIR, SLOW_BIAS_FILE, FAST_BIAS_FILE
from llamas_ql.Extract.extractLlamas import ExtractLlamas, save_extractions
import llamas_ql.GUI.guiExtract as ge
from llamas_ql.File.llamasIO import process_fits_by_color
from llamas_ql.File.llamasRSS import update_ra_dec_in_fits
import llamas_ql.Arc.arcLlamasMulti as arc
from llamas_ql.File.llamasRSS import RSSgeneration
from llamas_ql.Utils.utils import count_trace_fibres, check_header, configure_pipeline_logging, setup_logger
from llamas_ql.Bias.llamasBias import BiasLlamas
from astropy.io import fits
import numpy as np

import shutil

_cached_reference_arc = None

logger = logging.getLogger(__name__)

from llamas_ql.DataModel.validate import validate_and_fix_extensions, get_placeholder_extension_indices, validate_for_gui
from llamas_ql.Flat.flatLlamas import process_flat_field_complete, process_pixel_flat_simple
from llamas_ql.Flat.fibreFlat import (compute_fibre_flat_lamp_only,
                                           compute_fibre_flat_twilight,
                                           reduce_twilight_flat,
                                           apply_fibre_flat_to_rss)

_linefile = os.path.join(LUT_DIR, '')


def validate_input_files(file_list):

    for file in file_list:
        assert os.path.exists(file), f"Input file does not exist: {file}"
        validate_and_fix_extensions(file, 
                                    output_file=None, backup=True)

    return True

def get_input_files_from_config(config, file_keys=None):
      """
      Extract all input file paths from a configuration dictionary.
      
      Parameters
      ----------
      config : dict
          Configuration dictionary with file paths
      file_keys : list, optional
          List of config keys that contain file paths. If None, uses default keys.
          
      Returns
      -------
      list
          List of all file paths found in the config
      """
      if file_keys is None:
          file_keys = ['science_files', 'slow_bias_file', 'fast_bias_file',
                       'red_flat_file', 'green_flat_file', 'blue_flat_file',
                       'twilight_flat', 'red_twilight_flat',
                       'green_twilight_flat', 'blue_twilight_flat']

      input_files = []

      for key in file_keys:
          if key in config:
              value = config[key]
              # If it's a list, extend the input_files list
              if isinstance(value, list):
                  input_files.extend(value)
              # If it's a single string, append it
              elif isinstance(value, str):
                  input_files.append(value)

      return input_files


def validate_pipeline_config(config: dict, config_path: str) -> bool:
    """
    Pre-flight validation of the pipeline configuration.

    Checks package directories, required master bias files, required config keys,
    and file/directory path existence before any processing begins.  On failure,
    prints a clear itemised summary and calls sys.exit(1).

    Parameters
    ----------
    config : dict
        Parsed configuration dictionary from the config .txt file.
    config_path : str
        Path to the config file (used in error messages for context).

    Returns
    -------
    bool
        True if all checks pass (warnings are allowed).
    """
    import sys

    errors = []
    warnings = []

    print("\n" + "=" * 60)
    print("PIPELINE PRE-FLIGHT CHECK")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Package directory checks — fail immediately if missing
    # ------------------------------------------------------------------
    if not os.path.isdir(CALIB_DIR):
        errors.append(
            f"Package mastercalib directory not found: '{CALIB_DIR}'. "
            f"This directory is required for trace fallbacks."
        )
    if not os.path.isdir(LUT_DIR):
        errors.append(
            f"Package LUT directory not found: '{LUT_DIR}'. "
            f"This directory is required for the reference arc and trace LUT."
        )

    if errors:
        for e in errors:
            print(f"  \u2717  ERROR: {e}")
        print(f"\nPipeline pre-flight check FAILED — {len(errors)} error(s).")
        print("Fix the above issues and re-run.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Master bias file checks
    # ------------------------------------------------------------------
    # Check packaged default locations
    for label, path in [('slow_master_bias', SLOW_BIAS_FILE),
                         ('fast_master_bias', FAST_BIAS_FILE)]:
        if not os.path.isfile(path):
            errors.append(
                f"Required master bias not found: '{path}'. "
                f"Create it from raw bias frames using BiasLlamas and place it in the Bias/ directory, "
                f"or set 'slow_bias_file'/'fast_bias_file' in your config to a custom path."
            )

    # Check user-supplied overrides if present
    for key in ('slow_bias_file', 'fast_bias_file'):
        if key in config:
            p = config[key]
            if not os.path.isfile(p):
                errors.append(f"'{key}' path does not exist: '{p}'")

    # ------------------------------------------------------------------
    # 3. Required config keys
    # ------------------------------------------------------------------
    required_keys = ['science_files', 'red_flat_file', 'green_flat_file', 'blue_flat_file']
    for key in required_keys:
        if key not in config:
            errors.append(f"Required key '{key}' is missing from config.")

    if config.get('generate_new_wavelength_soln') is True and 'arc_file' not in config:
        errors.append(
            "'generate_new_wavelength_soln' is true but 'arc_file' is not set in config."
        )

    # ------------------------------------------------------------------
    # 4. File path existence checks
    # ------------------------------------------------------------------
    file_keys = ['science_files', 'red_flat_file', 'green_flat_file', 'blue_flat_file',
                 'twilight_flat', 'red_twilight_flat', 'green_twilight_flat',
                 'blue_twilight_flat']
    if config.get('generate_new_wavelength_soln') is True:
        file_keys.append('arc_file')

    for key in file_keys:
        if key not in config:
            continue  # already caught by required check or truly optional
        val = config[key]
        paths = val if isinstance(val, list) else [val]
        for p in paths:
            if not os.path.isfile(p):
                errors.append(f"'{key}' path does not exist: '{p}'")

    # Directory keys — warn only (pipeline creates them if missing)
    dir_keys = ['output_dir', 'trace_output_dir', 'extraction_output_dir',
                'flat_field_output_dir', 'log_output_dir', 'cube_output_dir', 'flat_file_dir']
    for key in dir_keys:
        if key in config and config[key]:
            p = config[key]
            if not os.path.isdir(p):
                warnings.append(
                    f"'{key}' directory does not exist yet: '{p}' — it will be created."
                )

    # ------------------------------------------------------------------
    # 5. LUT reference arc check
    # ------------------------------------------------------------------
    if not config.get('generate_new_wavelength_soln'):
        ref_arc = os.path.join(LUT_DIR, 'LLAMAS_reference_arc.pkl')
        if not os.path.isfile(ref_arc):
            errors.append(
                f"Reference arc not found: '{ref_arc}'. "
                f"Set 'generate_new_wavelength_soln = true' and provide 'arc_file' to generate one, "
                f"or place the reference arc pickle at the expected location."
            )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    for w in warnings:
        print(f"  \u26a0  WARNING: {w}")
    for e in errors:
        print(f"  \u2717  ERROR: {e}")

    if errors:
        print(
            f"\nPipeline pre-flight check FAILED — "
            f"{len(errors)} error(s), {len(warnings)} warning(s)."
        )
        print("Fix the above issues and re-run.")
        sys.exit(1)

    if warnings:
        print(f"\nPipeline pre-flight check passed with {len(warnings)} warning(s).")
    else:
        print("\nPipeline pre-flight check passed.")

    return True


def copy_mastercalib_traces_for_placeholders(flat_file, trace_dir, channel, placeholder_indices=None):
    """
    Copy mastercalib traces for placeholder extensions identified in a flat file.

    Args:
        flat_file: Path to flat field FITS file
        trace_dir: Directory where traces should be copied to
        channel: Channel name ('red', 'green', 'blue')
        placeholder_indices: Optional list of placeholder indices (will detect if None)

    Returns:
        int: Number of traces copied
    """
    if placeholder_indices is None:
        placeholder_indices = get_placeholder_extension_indices(flat_file)

    if len(placeholder_indices) == 0:
        return 0  # No placeholders to handle

    print(f"Copying {len(placeholder_indices)} mastercalib traces for missing {channel} cameras")

    traces_copied = 0
    with fits.open(flat_file) as hdul:
        for idx in placeholder_indices:
            hdu = hdul[idx]

            # Extract camera metadata
            bench = hdu.header['BENCH']
            side = hdu.header['SIDE']
            cam_channel = hdu.header['COLOR'].lower()

            # Only copy if the channel matches
            if cam_channel != channel.lower():
                continue

            # Determine mastercalib trace filename
            master_trace_file = f'LLAMAS_master_{channel.lower()}_{bench}_{side}_traces.pkl'
            master_trace_path = os.path.join(CALIB_DIR, master_trace_file)

            # Target filename (user trace naming convention)
            user_trace_file = f'LLAMAS_{channel.lower()}_{bench}_{side}_traces.pkl'
            user_trace_path = os.path.join(trace_dir, user_trace_file)

            # Copy mastercalib trace to user trace directory
            if os.path.exists(master_trace_path):
                shutil.copy2(master_trace_path, user_trace_path)
                print(f"  ✓ Copied mastercalib trace for {channel}{bench}{side}")
                traces_copied += 1
            else:
                print(f"  ✗ WARNING: Mastercalib trace not found: {master_trace_file}")

    return traces_copied



def generate_traces(red_flat, green_flat, blue_flat, output_dir,
                    slow_bias=None, fast_bias=None, missing_cams=False):
    """Generate fiber traces from flat field observations for all three channels.

    This function intelligently handles missing camera extensions by:
    1. Detecting placeholder extensions in each flat field file
    2. Running trace generation only for real camera data
    3. Copying mastercalib traces for placeholder extensions

    Args:
        red_flat: Path to red flat field FITS file.
        green_flat: Path to green flat field FITS file.
        blue_flat: Path to blue flat field FITS file.
        output_dir: Directory to save trace files.
        slow_bias: Path to SLOW-mode master bias FITS file.
        fast_bias: Path to FAST-mode master bias FITS file.
        missing_cams: Deprecated parameter, kept for backwards compatibility.

    Raises:
        AssertionError: If any of the flat field files do not exist.
    """

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    assert os.path.exists(red_flat), "Red flat file does not exist."
    assert os.path.exists(green_flat), "Green flat file does not exist."
    assert os.path.exists(blue_flat), "Blue flat file does not exist."

    print("\n" + "="*60)
    print("TRACE GENERATION WITH PLACEHOLDER DETECTION")
    print("="*60)

    # Process each channel with placeholder detection
    channels = [
        ('red', red_flat),
        ('green', green_flat),
        ('blue', blue_flat)
    ]

    for channel, flat_file in channels:
        print(f"\n--- Processing {channel.upper()} channel ---")

        # Detect placeholder extensions
        placeholder_indices = get_placeholder_extension_indices(flat_file)

        if placeholder_indices:
            print(f"Found {len(placeholder_indices)} placeholder extensions in {os.path.basename(flat_file)}")
            print(f"Will skip trace generation for placeholder indices: {placeholder_indices}")
        else:
            print(f"No placeholder extensions found in {os.path.basename(flat_file)}")

        # Run trace generation, skipping placeholder extensions
        # slow_bias/fast_bias are forwarded; run_ray_tracing selects per READ-MDE via GUI_extract
        run_ray_tracing(
            flat_file,
            outpath=output_dir,
            channel=channel,
            slow_bias=slow_bias,
            fast_bias=fast_bias,
            is_master_calib=False,
            skip_extension_indices=placeholder_indices
        )

        # Copy mastercalib traces for placeholder extensions
        if placeholder_indices:
            traces_copied = copy_mastercalib_traces_for_placeholders(
                flat_file,
                output_dir,
                channel,
                placeholder_indices
            )
            if traces_copied > 0:
                print(f"Successfully copied {traces_copied} mastercalib traces for {channel} placeholders")

    print("\n" + "="*60)
    print(f"All traces generated and saved to {output_dir}")
    print("="*60)

    return


###need to edit GUI extract to give custom output_dir
#currently designed to use skyflats
#only used for generating new wl solutions
def extract_flat_field(flat_file_dir, output_dir, slow_bias=None, fast_bias=None):

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    ge.GUI_extract(flat_file_dir, output_dir=output_dir, slow_bias=slow_bias, fast_bias=fast_bias,
                   remove_cosmic_rays=False)

    return


def run_extraction(science_file, output_dir, slow_bias=None, fast_bias=None,
                   trace_dir=None, mastercalib_trace_dir=None,
                   remove_cosmic_rays=True, mask_output_dir=None):
    """
    Run spectrum extraction with hybrid trace support.

    Args:
        science_file: Path to science FITS file or list of paths
        output_dir: Output directory for extractions
        slow_bias: Path to SLOW-mode master bias FITS file
        fast_bias: Path to FAST-mode master bias FITS file
        trace_dir: User trace directory (for real extensions)
        mastercalib_trace_dir: Mastercalib trace directory (for placeholder extensions)
        remove_cosmic_rays: Enable L.A.Cosmic cosmic ray removal before extraction
        mask_output_dir: Output directory for cosmic ray mask FITS files

    Returns:
        str: Path to extraction file
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Default mastercalib location if not specified
    if mastercalib_trace_dir is None:
        mastercalib_trace_dir = CALIB_DIR

    assert os.path.exists(science_file), "Science file does not exist."
    if type(science_file) is list:
        for file in science_file:
            assert os.path.exists(file), f"Science file {file} does not exist."
            extraction_file_path = ge.GUI_extract(
                file,
                output_dir=output_dir,
                slow_bias=slow_bias,
                fast_bias=fast_bias,
                trace_dir=trace_dir,
                mastercalib_trace_dir=mastercalib_trace_dir,
                remove_cosmic_rays=remove_cosmic_rays,
                mask_output_dir=mask_output_dir
            )
    else:
        assert os.path.exists(science_file), "Science file does not exist."
        extraction_file_path, _ = ge.GUI_extract(
            science_file,
            output_dir=output_dir,
            slow_bias=slow_bias,
            fast_bias=fast_bias,
            trace_dir=trace_dir,
            mastercalib_trace_dir=mastercalib_trace_dir,
            remove_cosmic_rays=remove_cosmic_rays,
            mask_output_dir=mask_output_dir
        )

    # GUI_extract returns just the basename; make it a full path
    if extraction_file_path and not os.path.isabs(extraction_file_path):
        extraction_file_path = os.path.join(output_dir, extraction_file_path)

    return extraction_file_path


def calc_wavelength_soln(arc_file, output_dir, slow_bias=None, fast_bias=None):

    ge.GUI_extract(arc_file, output_dir=output_dir, slow_bias=slow_bias, fast_bias=fast_bias,
                   remove_cosmic_rays=False)

    arc_picklename = os.path.join(output_dir, os.path.basename(arc_file).replace('_mef.fits', '_extract.pkl'))

    with open(arc_picklename, 'rb') as fp:
        batch_data = pickle.load(fp)
    
    arcdict = ExtractLlamas.loadExtraction(arc_picklename)
    arcspec, metadata = arcdict['extractions'], arcdict['metadata']

    arc.shiftArcX(arc_picklename)

    return arcdict



def relative_throughput(shift_picklename, flat_picklename):

    arc.fiberRelativeThroughput(flat_picklename, shift_picklename)
    ### need to add code in to return the name of the throughput file
    return


def correct_wavelengths(science_extraction_file, soln=None):
    # TODO: when arc processing pipeline is wired up, use soln to generate/load a custom arc solution
    global _cached_reference_arc
    if _cached_reference_arc is None:
        logger.info("Loading reference arc (first call, will be cached)")
        _cached_reference_arc = ExtractLlamas.loadExtraction(
            os.path.join(LUT_DIR, 'LLAMAS_reference_arc.pkl'))
    arcdict = _cached_reference_arc

    _science = ExtractLlamas.loadExtraction(science_extraction_file)
    extractions = _science['extractions']
    metadata    = _science['metadata']
    primary_hdr = _science.get('primary_header')
    print(f'extractions: {extractions}')
    print(f'metadata: {metadata}')
    std_wvcal = arc.arcTransfer(_science, arcdict,)

    print(f'std_wvcal: {std_wvcal}')
    print(f'std_wvcal metadata: {std_wvcal.get("metadata", {})}')

    return std_wvcal, primary_hdr


def _pointing_from_header(header):
    """Extract (ra_deg, dec_deg, pa_deg) from an RSS/science primary header (F6).

    Prefers the decimal ``RA``/``DEC`` keywords, falling back to the sexagesimal
    HIERARCH ``TEL RA``/``TEL DEC`` pair, and reads the field rotation from
    ``TEL PA`` (then ``TEL ROT``). Returns ``(0.0, 0.0, 0.0)`` if nothing usable
    is present so cube construction degrades to the old placeholder behaviour.
    """
    if header is None:
        return 0.0, 0.0, 0.0
    ra = header.get('RA')
    dec = header.get('DEC')
    try:
        ra, dec = float(ra), float(dec)
        if not (np.isfinite(ra) and np.isfinite(dec)):
            raise ValueError
    except (TypeError, ValueError):
        ra = dec = None
    if ra is None or dec is None:
        tra, tdec = header.get('TEL RA'), header.get('TEL DEC')
        if tra and tdec:
            try:
                from astropy.coordinates import SkyCoord
                import astropy.units as u
                c = SkyCoord(str(tra), str(tdec), unit=(u.hourangle, u.deg))
                ra, dec = float(c.ra.deg), float(c.dec.deg)
            except Exception:
                return 0.0, 0.0, 0.0
        else:
            return 0.0, 0.0, 0.0
    pa = header.get('TEL PA', header.get('TEL ROT', 0.0))
    try:
        pa = float(pa)
        if not np.isfinite(pa):
            pa = 0.0
    except (TypeError, ValueError):
        pa = 0.0
    return float(ra), float(dec), pa


def resolve_twilight_files(config):
    """Resolve the twilight flat to use for each colour (F1).

    Supports both the per-colour keys ``{red,green,blue}_twilight_flat`` and the
    singular ``twilight_flat`` key, with the per-colour key taking precedence and
    the singular key as a shared fallback. Returns ``{color: path_or_None}`` for
    ``red``/``green``/``blue``.  A single LLAMAS twilight MEF contains all
    cameras, so multiple colours commonly resolve to the same file.
    """
    shared = config.get('twilight_flat')
    return {color: (config.get(f'{color}_twilight_flat') or shared)
            for color in ('red', 'green', 'blue')}


def _process_flat_for_rss(flat_files, flat_pixel_maps, output_dir,
                          trace_dir, arc_dict_config,
                          timestamp, label='flat',
                          slow_bias=None, fast_bias=None):
    """Apply pixel flat → extract → wavelength-calibrate → generate RSS for a flat frame.

    Used for both dome flats (fibre-flat RSS) and twilight flats.
    The pixel-to-pixel flat map *must* be applied to the raw flat frame before
    extraction so that extracted spectra represent pure fibre throughput
    (detector per-pixel sensitivity removed).

    Parameters
    ----------
    flat_files : list of str
        Raw flat FITS files (dome or twilight).
    flat_pixel_maps : list of str
        Pixel flat maps generated from the dome flat.
    output_dir : str
        Directory for all intermediate and output files.
    slow_bias : str or None
        Path to SLOW-mode master bias FITS (passed to run_extraction).
    fast_bias : str or None
        Path to FAST-mode master bias FITS (passed to run_extraction).
    trace_dir : str or None
        Directory containing fibre trace files.
    arc_dict_config : object or None
        Arc solution config forwarded to correct_wavelengths.
    timestamp : str
        Timestamp string used in log-file names.
    label : str
        Short label used in log filenames (e.g. 'dome', 'twilight').

    Returns
    -------
    list of str
        RSS output file paths (may be empty on failure).
    """
    rss_outputs = []
    for flat_file in flat_files:
        if flat_file is None:
            continue

        # Step 1: pixel-to-pixel flat correction (2D)
        corr_file, _ = apply_flat_field_correction(
            flat_file, flat_pixel_maps, output_dir,
            validate_matching=True, require_all_matches=False
        )
        if not corr_file:
            print(f"  WARNING: Pixel flat correction failed for {os.path.basename(flat_file)} — skipping")
            continue

        # Step 2: extract (bias subtraction is handled inside run_extraction)
        # run_extraction returns only a basename (from GUI_extract); join with output_dir
        pkl_basename = run_extraction(corr_file, output_dir,
                                      slow_bias=slow_bias, fast_bias=fast_bias,
                                      trace_dir=trace_dir, mastercalib_trace_dir=CALIB_DIR,
                                      remove_cosmic_rays=False)
        if not pkl_basename:
            print(f"  WARNING: Extraction failed for {os.path.basename(corr_file)} — skipping")
            continue
        pkl = os.path.join(output_dir, pkl_basename)

        # Step 3: wavelength-calibrate and save corrected pickle
        corr_extr, primary_hdr = correct_wavelengths(pkl, soln=arc_dict_config)
        base = os.path.splitext(os.path.basename(pkl))[0]
        corr_pkl = os.path.join(output_dir, f'{base}_corrected_extractions.pkl')
        save_extractions(
            corr_extr['extractions'], primary_header=primary_hdr,
            savefile=corr_pkl, save_dir=output_dir,
            prefix=f'LLAMASExtract_{label}_corrected',
        )

        # Step 4: generate RSS
        rss_base = os.path.join(
            output_dir,
            os.path.basename(corr_pkl).replace('_corrected_extractions.pkl', '_RSS.fits')
        )
        rss_logger = setup_logger(__name__, f'{label}_RSS_{timestamp}.log')
        rss_gen = RSSgeneration(logger=rss_logger)
        out = rss_gen.generate_rss(corr_pkl, rss_base)
        if out:
            rss_outputs.extend(out)

    return rss_outputs


def process_flat_field_calibration(red_flat, green_flat, blue_flat, trace_dir, output_dir,
                                  arc_calib_file=None, verbose=False, method='simple',
                                  filter_size=12, signal_thresholds=None,
                                  clip_range=(0.90, 1.10), use_bias=None,
                                  saturation_threshold=None):
    """Generate flat field pixel maps for science frame correction.

    Args:
        red_flat (str): Path to red flat field FITS file
        green_flat (str): Path to green flat field FITS file
        blue_flat (str): Path to blue flat field FITS file
        trace_dir (str): Directory containing trace files
        output_dir (str): Output directory for flat field products
        arc_calib_file (str, optional): Path to arc calibration file
        verbose (bool): Enable verbose output
        method (str): 'simple' (default, median+Gaussian in wavelength space)
            or 'bspline' (legacy arc+B-spline fitting).
        filter_size (int): Median filter size (simple method only).
        signal_thresholds (dict, optional): Per-channel thresholds (simple method only).
        clip_range (tuple): Sensitivity clip range (simple method only).

    Returns:
        list: List of flat field pixel map FITS file paths
    """
    flat_output_dir = output_dir
    os.makedirs(flat_output_dir, exist_ok=True)

    print(f"Processing flat field calibration (method={method})")
    print(f"  Red flat: {os.path.basename(red_flat)}")
    print(f"  Green flat: {os.path.basename(green_flat)}")
    print(f"  Blue flat: {os.path.basename(blue_flat)}")
    print(f"  Output directory: {flat_output_dir}")

    try:
        if method == 'simple':
            results = process_pixel_flat_simple(
                red_flat, green_flat, blue_flat,
                arc_calib_file=arc_calib_file,
                use_bias=use_bias,
                output_dir=flat_output_dir,
                trace_dir=trace_dir,
                verbose=verbose,
                filter_size=filter_size,
                signal_thresholds=signal_thresholds,
                clip_range=clip_range,
                saturation_threshold=saturation_threshold,
            )
        elif method == 'bspline':
            results = process_flat_field_complete(
                red_flat, green_flat, blue_flat,
                arc_calib_file=arc_calib_file,
                use_bias=use_bias,
                output_dir=flat_output_dir,
                trace_dir=trace_dir,
                verbose=verbose,
            )
        else:
            raise ValueError(
                f"Unknown flat field method: {method!r}. "
                f"Use 'simple' or 'bspline'.")

        pixel_map_files = [results['pixel_map_file']]
        print(f"Flat field calibration complete: {os.path.basename(pixel_map_files[0])}")
        print(f"  MEF file with 24 extensions generated")
        return pixel_map_files

    except Exception as e:
        print(f"Error in flat field calibration: {str(e)}")
        logger.error(f"Flat field calibration failed: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
        return []


def _extract_channel_from_filename(path):
    """Extract the colour channel name ('red', 'green', or 'blue') from a filename.

    Parses the last occurrence of _red, _green, or _blue before the extension.

    Args:
        path (str): File path.

    Returns:
        str or None: Channel name lower-cased, or None if not found.
    """
    basename = os.path.basename(path).lower()
    for ch in ('red', 'green', 'blue'):
        if f'_{ch}' in basename or basename.endswith(f'{ch}.fits'):
            return ch
    return None


def _find_matching_flat_rss(flat_rss_list, channel):
    """Return the flat RSS path that matches a given colour channel.

    Args:
        flat_rss_list (list): List of flat RSS file paths from RSSgeneration.
        channel (str or None): Channel to match ('red', 'green', 'blue').

    Returns:
        str or None: Path of the matching flat RSS file, or None.
    """
    if not flat_rss_list or channel is None:
        return None
    for path in flat_rss_list:
        if f'_{channel}' in os.path.basename(path).lower():
            return path
    return None


def build_flat_field_map(flat_pixel_maps, science_file):
    """
    Build mapping between science extensions and corresponding flat field pixel maps.

    Handles multi-extension FITS (MEF) flat field files by matching
    extension metadata exactly (channel, bench, side).

    Args:
        flat_pixel_maps (list): List with single MEF file path
        science_file (str): Path to science FITS file

    Returns:
        dict: {sci_ext_idx: {'flat_ext_idx': N, 'match_found': bool, ...}}
    """
    flat_map = {}

    if len(flat_pixel_maps) != 1:
        print(f"ERROR: Expected single MEF flat file, got {len(flat_pixel_maps)} files")
        return {}

    flat_mef_file = flat_pixel_maps[0]
    print(f"Building flat field map from MEF: {os.path.basename(flat_mef_file)}")

    try:
        with fits.open(flat_mef_file) as flat_hdul, fits.open(science_file) as sci_hdul:
            # Iterate over science extensions (skip primary at index 0)
            for sci_ext_idx in range(1, len(sci_hdul)):
                if sci_hdul[sci_ext_idx].data is None:
                    continue

                # Get science metadata - try multiple header keys
                sci_header = sci_hdul[sci_ext_idx].header
                sci_channel = sci_header.get('CHANNEL', sci_header.get('COLOR', '')).lower()
                sci_bench = str(sci_header.get('BENCH', ''))
                sci_side = sci_header.get('SIDE', '').upper()

                # Alternative naming if metadata not directly present
                if not sci_channel or not sci_bench:
                    cam_name = sci_header.get('CAM_NAME', '')
                    if cam_name:
                        parts = cam_name.split('_')
                        if len(parts) >= 2:
                            sci_channel = parts[1].lower() if not sci_channel else sci_channel
                            if len(parts[0]) >= 2 and not sci_bench:
                                sci_bench = parts[0][0]
                                sci_side = parts[0][1]

                sci_key = f"{sci_channel}{sci_bench}{sci_side}"

                # Search flat extensions for EXACT match
                matching_flat_ext = None

                for flat_ext_idx in range(1, len(flat_hdul)):
                    if flat_hdul[flat_ext_idx].data is None:
                        continue

                    flat_header = flat_hdul[flat_ext_idx].header
                    flat_channel = flat_header.get('CHANNEL', '').lower()
                    flat_bench = str(flat_header.get('BENCH', ''))
                    flat_side = flat_header.get('SIDE', '').upper()

                    # Exact match required (no scoring)
                    if (flat_channel == sci_channel and
                        flat_bench == sci_bench and
                        flat_side == sci_side):
                        matching_flat_ext = flat_ext_idx
                        print(f"  ✓ Sci ext {sci_ext_idx} ({sci_key}) → Flat ext {flat_ext_idx}")
                        break

                # Store mapping
                flat_map[sci_ext_idx] = {
                    'flat_file': flat_mef_file,
                    'flat_ext_idx': matching_flat_ext,
                    'science_key': sci_key,
                    'science_channel': sci_channel,
                    'science_bench': sci_bench,
                    'science_side': sci_side,
                    'match_found': matching_flat_ext is not None,
                    'is_mef': True
                }

                if matching_flat_ext is None:
                    print(f"  ✗ Sci ext {sci_ext_idx} ({sci_key}) → No flat match found")

        # Summary
        matched = sum(1 for m in flat_map.values() if m['match_found'])
        print(f"✓ Matched {matched}/{len(flat_map)} science extensions to flat field")

    except Exception as e:
        print(f"ERROR building flat field map: {e}")
        logger.error(f"Failed to build flat field map: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
        return {}

    return flat_map


def validate_flat_field_matching(flat_map, science_file):
    """
    Validate flat field matching and provide detailed reporting.
    
    Args:
        flat_map (dict): Flat field mapping from build_flat_field_map()
        science_file (str): Path to science FITS file
        
    Returns:
        dict: {'valid': bool, 'report': str, 'warnings': list, 'stats': dict}
    """
    total_extensions = len(flat_map)
    matched_extensions = sum(1 for v in flat_map.values() if v['match_found'])
    perfect_matches = sum(1 for v in flat_map.values() if v.get('match_score', 0) >= 6)
    
    validation_report = []
    warnings = []
    
    validation_report.append(f"Flat Field Validation for: {os.path.basename(science_file)}")
    validation_report.append(f"Total extensions: {total_extensions}")
    validation_report.append(f"Matched extensions: {matched_extensions}")
    validation_report.append(f"Perfect matches: {perfect_matches}")
    validation_report.append(f"Unmatched extensions: {total_extensions - matched_extensions}")
    validation_report.append("-" * 60)
    
    # Group extensions by channel for better reporting
    channels = {}
    for ext_idx, mapping in flat_map.items():
        channel = mapping.get('science_channel', 'unknown')
        if channel not in channels:
            channels[channel] = {'matched': 0, 'total': 0, 'extensions': []}
        channels[channel]['total'] += 1
        channels[channel]['extensions'].append((ext_idx, mapping))
        if mapping['match_found']:
            channels[channel]['matched'] += 1
    
    # Report by channel
    for channel, info in sorted(channels.items()):
        validation_report.append(f"\n{channel.upper()} Channel: {info['matched']}/{info['total']} matched")
        for ext_idx, mapping in sorted(info['extensions']):
            if mapping['match_found']:
                flat_name = os.path.basename(mapping['flat_file'])
                score = mapping.get('match_score', 0)
                score_str = f"(score: {score})" if score < 6 else "(perfect)"
                validation_report.append(
                    f"  ✓ Extension {ext_idx:2d} ({mapping['science_key']}): {flat_name} {score_str}"
                )
            else:
                validation_report.append(
                    f"  ✗ Extension {ext_idx:2d} ({mapping['science_key']}): No matching flat found"
                )
                warnings.append(f"Extension {ext_idx} ({mapping['science_key']}) has no flat field correction")
    
    # Check for critical channels missing
    critical_missing = []
    critical_channels = ['red', 'green', 'blue']
    
    for ext_idx, mapping in flat_map.items():
        if (not mapping['match_found'] and 
            mapping['science_channel'] in critical_channels):
            critical_missing.append(mapping['science_key'])
    
    # Determine validity
    coverage_ratio = matched_extensions / total_extensions if total_extensions > 0 else 0
    is_valid = coverage_ratio >= 0.5 and len(critical_missing) == 0  # At least 50% coverage and no critical missing
    
    if critical_missing:
        warnings.append(f"Critical channels missing flat corrections: {critical_missing}")
    
    if coverage_ratio < 0.8:
        warnings.append(f"Low flat field coverage: {coverage_ratio:.1%}")
    
    stats = {
        'total_count': total_extensions,
        'matched_count': matched_extensions,
        'perfect_matches': perfect_matches,
        'coverage_ratio': coverage_ratio,
        'critical_missing': len(critical_missing)
    }
    
    return {
        'valid': is_valid,
        'report': '\n'.join(validation_report),
        'warnings': warnings,
        'stats': stats
    }


def _align_shapes(science_data, flat_data):
    """Align science and flat data shapes by trimming the larger array.

    LLAMAS detectors occasionally produce frames with an extra row or column
    (e.g. 2049x2048 vs 2048x2048).  This trims the larger array to match
    the smaller, keeping data from the origin corner.

    Parameters
    ----------
    science_data : ndarray
        2D science frame.
    flat_data : ndarray
        2D flat field / pixel map.

    Returns
    -------
    science_out, flat_out : ndarray
        Arrays with matching shapes.
    trimmed : bool
        True if any trimming was performed.
    """
    if science_data.shape == flat_data.shape:
        return science_data, flat_data, False

    common_rows = min(science_data.shape[0], flat_data.shape[0])
    common_cols = min(science_data.shape[1], flat_data.shape[1])

    return (science_data[:common_rows, :common_cols],
            flat_data[:common_rows, :common_cols],
            True)


def apply_flat_field_correction(science_file, flat_pixel_maps, output_dir,
                               validate_matching=True, require_all_matches=False):
    """
    Apply flat field corrections to a science frame with robust cross-checking.
    
    Args:
        science_file (str): Path to science FITS file
        flat_pixel_maps (list): List of flat field pixel map files
        output_dir (str): Output directory for corrected files
        validate_matching (bool): Whether to validate matching before processing
        require_all_matches (bool): Whether to require all extensions to have matches
        
    Returns:
        tuple: (output_file_path, correction_statistics)
    """
    
    print(f"Applying flat field correction to: {os.path.basename(science_file)}")
    
    # Step 1: Build flat field mapping with cross-checking
    flat_map = build_flat_field_map(flat_pixel_maps, science_file) # this doesn't seem righ
    
    if not flat_map:
        print("ERROR: Could not build flat field mapping")
        logger.error("Could not build flat field mapping")
        return None, {'corrected': 0, 'skipped': 0, 'errors': 1}
    
    # Step 2: Validate matching if requested
    if validate_matching:
        validation = validate_flat_field_matching(flat_map, science_file)
        print("\n" + validation['report'])
        
        if validation['warnings']:
            for warning in validation['warnings']:
                print(f"WARNING: {warning}")
        
        if require_all_matches and not validation['valid']:
            raise ValueError("Not all extensions have matching flat fields and require_all_matches=True")
        
        print(f"\nProceeding with {validation['stats']['matched_count']}/{validation['stats']['total_count']} extensions matched")
        print("=" * 60)
    
    # Step 3: Apply corrections with verified matching
    try:
        # Open flat MEF file once (outside the loop for efficiency)
        flat_mef_file = flat_pixel_maps[0]

        with fits.open(science_file) as sci_hdul, fits.open(flat_mef_file) as flat_hdul:
            corrected_hdus = [sci_hdul[0].copy()]  # Primary header

            correction_stats = {'corrected': 0, 'skipped': 0, 'errors': 0}

            for ext_idx in range(1, len(sci_hdul)):
                sci_hdu = sci_hdul[ext_idx]
                mapping = flat_map.get(ext_idx, {})

                if mapping.get('match_found') and sci_hdu.data is not None:
                    try:
                        # Get flat data from MEF extension
                        flat_ext_idx = mapping['flat_ext_idx']
                        flat_data = flat_hdul[flat_ext_idx].data

                        # Shape alignment (handles extra-row science frames)
                        sci_aligned, flat_aligned, was_trimmed = _align_shapes(
                            sci_hdu.data, flat_data)
                        if was_trimmed:
                            print(f"  NOTE: Shape mismatch for ext {ext_idx} "
                                  f"(sci: {sci_hdu.data.shape}, flat: {flat_data.shape}). "
                                  f"Trimmed to {sci_aligned.shape}.")

                        # Perform division with NaN protection
                        corrected_trimmed = np.divide(
                            sci_aligned.astype(np.float32),
                            flat_aligned.astype(np.float32),
                            out=np.full_like(sci_aligned, np.nan, dtype=np.float32),
                            where=(flat_aligned > 0)
                        )

                        # Handle bad pixels from division
                        bad_pixels = ~np.isfinite(corrected_trimmed)
                        n_bad = np.sum(bad_pixels)
                        if n_bad > 0:
                            corrected_trimmed[bad_pixels] = sci_aligned[bad_pixels]

                        # Embed corrected data back into original science shape
                        if was_trimmed and sci_hdu.data.shape != sci_aligned.shape:
                            corrected_data = sci_hdu.data.astype(np.float32).copy()
                            corrected_data[:sci_aligned.shape[0],
                                           :sci_aligned.shape[1]] = corrected_trimmed
                        else:
                            corrected_data = corrected_trimmed

                        # Create new HDU with corrected data
                        new_hdu = fits.ImageHDU(
                            data=corrected_data,
                            header=sci_hdu.header.copy(),
                            name=sci_hdu.name
                        )

                        # Add correction metadata
                        new_hdu.header['FLATCORR'] = (True, 'Flat field corrected')
                        new_hdu.header['FLATFILE'] = (os.path.basename(flat_mef_file), 'Flat field MEF file')
                        new_hdu.header['FLATEXT'] = (flat_ext_idx, 'Flat field extension index')
                        new_hdu.header['FLATKEY'] = (mapping['science_key'], 'Flat field matching key')
                        new_hdu.header['BADFPIX'] = (int(n_bad), 'Bad pixels from flat division')

                        # Add statistics
                        valid_pixels = np.isfinite(corrected_data)
                        if np.any(valid_pixels):
                            new_hdu.header['FLATMEAN'] = (float(np.mean(corrected_data[valid_pixels])), 'Mean after flat correction')
                            new_hdu.header['FLATMED'] = (float(np.median(corrected_data[valid_pixels])), 'Median after flat correction')
                            new_hdu.header['FLATVPIX'] = (int(np.sum(valid_pixels)), 'Valid pixels after flat correction')

                        corrected_hdus.append(new_hdu)
                        correction_stats['corrected'] += 1

                        print(f"  ✓ Extension {ext_idx:2d} ({mapping['science_key']}): Corrected (bad pixels: {n_bad})")

                    except Exception as e:
                        print(f"  ✗ Extension {ext_idx:2d} ({mapping.get('science_key', 'UNKNOWN')}): Error - {str(e)}")
                        # Copy original on error
                        new_hdu = sci_hdu.copy()
                        new_hdu.header['FLATCORR'] = (False, f'Flat correction failed: {str(e)}')
                        new_hdu.header['FLATKEY'] = (mapping.get('science_key', 'UNKNOWN'), 'Failed flat correction')
                        corrected_hdus.append(new_hdu)
                        correction_stats['errors'] += 1

                else:
                    # No flat correction applied
                    new_hdu = sci_hdu.copy()
                    new_hdu.header['FLATCORR'] = (False, 'No matching flat field found')
                    if mapping:
                        new_hdu.header['FLATKEY'] = (mapping.get('science_key', 'UNKNOWN'), 'No matching flat found')
                    corrected_hdus.append(new_hdu)
                    correction_stats['skipped'] += 1
                    print(f"  - Extension {ext_idx:2d} ({mapping.get('science_key', 'UNKNOWN')}): Skipped (no flat)")
            
            # Save corrected science frame
            base_name = os.path.splitext(os.path.basename(science_file))[0]
            output_file = os.path.join(output_dir, f"{base_name}_flat_corrected.fits")
            
            corrected_hdul = fits.HDUList(corrected_hdus)
            
            # Add summary to primary header
            corrected_hdul[0].header['FLATSTAT'] = (
                f"{correction_stats['corrected']}C/{correction_stats['skipped']}S/{correction_stats['errors']}E",
                'Flat correction stats: Corrected/Skipped/Errors'
            )
            corrected_hdul[0].header['FLATTIME'] = (
                datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                'Flat field correction timestamp'
            )
            
            corrected_hdul.writeto(output_file, overwrite=True)
            
            print(f"\nFlat field correction summary:")
            print(f"  - Corrected: {correction_stats['corrected']} extensions")
            print(f"  - Skipped: {correction_stats['skipped']} extensions") 
            print(f"  - Errors: {correction_stats['errors']} extensions")
            print(f"  - Output file: {os.path.basename(output_file)}")
            
            return output_file, correction_stats
            
    except Exception as e:
        print(f"ERROR: Failed to process science file {science_file}: {str(e)}")
        logger.error(f"Failed to process science file {science_file}: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
        return None, {'corrected': 0, 'skipped': 0, 'errors': 1}


