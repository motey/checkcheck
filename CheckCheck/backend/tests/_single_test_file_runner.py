import os
import sys
import __main__
from pathlib import Path


def run_all_tests_if_test_file_called():
    """When a tests_*.py file is run directly, boot the server and execute its tests."""
    entry_script = getattr(__main__, "__file__", None)
    if not entry_script:
        return
    if not os.path.basename(entry_script).startswith("tests_"):
        return

    MODULE_DIR = Path(__file__).parent
    MODULE_PARENT_DIR = MODULE_DIR.parent.absolute()
    sys.path.insert(0, os.path.normpath(MODULE_PARENT_DIR))
    sys.path.insert(0, str(MODULE_DIR))

    import main

    main.run_single_test_file(entry_script, authorize_before=True, exit_on_success=True)
