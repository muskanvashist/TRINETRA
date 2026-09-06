# src/utils/cleanup.py
#
# Keeps the live dashboard fast by treating every new address trace as
# fresh -- clears temporary per-trace files before starting and after
# finishing, so disk usage never accumulates across sessions.
#
# IMPORTANT: this does NOT touch the ML training pipeline's persistent
# files (address_features.csv, address_labels.csv, the trained model
# .pkl) -- those are built deliberately over time from fetch_trongrid.py
# runs and should not be wiped by a live dashboard trace.

import os
import glob
import shutil


# Files/folders that are safe to wipe before every live trace --
# purely transient, regenerated fresh each time.
TEMP_RAW_JSON_DIR = "data/raw/trongrid_transactions"
TEMP_TRACE_REPORT = "data/processed/trace_report.json"
TEMP_GRAPH_HTML = "data/processed/neon_fundflow.html"
TEMP_DASHBOARD_HTML = "temp_fundflow.html"
TEMP_FREEZE_NOTICES_DIR = "data/processed/freeze_notices"


def clear_live_trace_temp_data():
    """
    Call this RIGHT BEFORE starting a new live trace (e.g. when the user
    clicks 'Run Trace' on a new address in the dashboard). Removes
    everything left over from the previous trace so nothing lingers
    and disk I/O doesn't slow future traces down.
    """
    cleared = []

    # Per-address raw JSON dump (only relevant to fetch_trongrid.py's
    # offline collection script, not the live in-memory dashboard trace,
    # but cleared anyway in case both were used together)
    if os.path.isdir(TEMP_RAW_JSON_DIR):
        shutil.rmtree(TEMP_RAW_JSON_DIR, ignore_errors=True)
        cleared.append(TEMP_RAW_JSON_DIR)

    # Previous trace's generated report/graph
    for path in [TEMP_TRACE_REPORT, TEMP_GRAPH_HTML, TEMP_DASHBOARD_HTML]:
        if os.path.exists(path):
            os.remove(path)
            cleared.append(path)

    return cleared


def clear_freeze_notices():
    """
    Optional: clear previously generated freeze notice PDFs. Call this
    separately (not automatically) since these are often meant to be
    kept/downloaded by the investigator, not silently deleted.
    """
    if os.path.isdir(TEMP_FREEZE_NOTICES_DIR):
        shutil.rmtree(TEMP_FREEZE_NOTICES_DIR, ignore_errors=True)
        return [TEMP_FREEZE_NOTICES_DIR]
    return []


def clear_all_pycache():
    """
    Optional housekeeping: remove __pycache__ folders across the project.
    Not related to trace speed, but keeps the repo clean if run
    periodically (e.g. before a Git commit).
    """
    cleared = []
    for pycache_dir in glob.glob("**/__pycache__", recursive=True):
        shutil.rmtree(pycache_dir, ignore_errors=True)
        cleared.append(pycache_dir)
    return cleared


if __name__ == "__main__":
    cleared = clear_live_trace_temp_data()
    if cleared:
        print("Cleared the following temporary files/folders:")
        for path in cleared:
            print(f"  - {path}")
    else:
        print("Nothing to clear -- already clean.")