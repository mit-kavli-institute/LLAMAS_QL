"""Single, process-wide Ray session for the persistent reduction worker.

The GUI runs reduction jobs serially in one long-lived subprocess (see
``QLook/workers/reduction_process.py``), so Ray should be initialised exactly
once and kept warm — not shut down, re-bundled and re-initialised on every frame
(the previous behaviour in ``GUI_extract``), which was both the largest
per-frame cost and a recurring source of init/shutdown flakiness (the reason for
the ``DeferSigint`` signal monkey-patch in ``llamas_ql/__init__``).

``ensure_ray()`` is idempotent and shared by every job kind (science extraction
and afternoon calibration tracing) so one session serves them all.
"""
import os
import logging
from pathlib import Path

import ray
import psutil
import pkg_resources

from llamas_ql.config import DATA_DIR

logger = logging.getLogger(__name__)

#: Patterns kept out of the Ray ``py_modules`` bundle (must stay under Ray's
#: 512 MB limit): large data/products, VCS, and calibration artefacts.
_RAY_EXCLUDES = [
    str(Path(DATA_DIR) / "**"),
    "**/*.fits",
    "**/*.pkl",
    "**/.git/**",
    "**/*.zip/**",
    "**/*.tar.gz/**",
    "**/mastercalib*/**",
    "**/*.zip",
    "**/reduced/**", "**/extractions/**", "**/cubes/**", "**/traces/**",
    "**/testing/**",
]


def _runtime_env():
    """Ray runtime env that ships the repo root so workers can ``import llamas_ql``."""
    package_path = pkg_resources.resource_filename("llamas_ql", "")
    package_root = os.path.dirname(package_path)
    return {
        "py_modules": [package_root],
        "env_vars": {"PYTHONPATH": f"{package_root}:{os.environ.get('PYTHONPATH', '')}"},
        "excludes": list(_RAY_EXCLUDES),
    }


def ensure_ray():
    """Initialise Ray once for this process (idempotent); reuse a running session.

    Honours ``LLAMAS_RAY_CPUS`` (default 8) and ``LLAMAS_RAY_OBJECT_STORE_MB``
    (default 2048, clamped to ≤30 % of RAM so the object store can't swallow
    Ray's scheduler memory pool on macOS with the large-object-store guard off).
    """
    if ray.is_initialized():
        return

    num_cpus = int(os.environ.get("LLAMAS_RAY_CPUS", 8))
    object_store_mb = int(os.environ.get("LLAMAS_RAY_OBJECT_STORE_MB", 2048))
    os.environ["RAY_ENABLE_MAC_LARGE_OBJECT_STORE"] = "1"
    os.environ["RAY_local_fs_capacity_threshold"] = "0.99"

    total_mb = psutil.virtual_memory().total // (1024 * 1024)
    store_cap_mb = int(total_mb * 0.30)
    if object_store_mb > store_cap_mb:
        logger.warning(
            f"Clamping Ray object store {object_store_mb}→{store_cap_mb} MB "
            f"(30% of {total_mb} MB RAM) to preserve the scheduler memory pool")
        object_store_mb = store_cap_mb

    logger.info(
        f"[ray] initialising once: num_cpus={num_cpus} "
        f"object_store={object_store_mb}MB "
        f"free_RAM={psutil.virtual_memory().available // (1024 * 1024)}MB")

    ray.init(
        num_cpus=num_cpus,
        object_store_memory=object_store_mb * 1024 * 1024,
        runtime_env=_runtime_env(),
        ignore_reinit_error=True,
    )
