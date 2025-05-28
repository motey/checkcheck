import inspect
import os
import sys
import types
import __main__
from pathlib import Path


def run_all_tests_if_test_file_called():
    # Get the caller's frame
    entry_script = __main__.__file__
    entry_script_name = os.path.basename(entry_script)
    if not entry_script_name.startswith("tests_"):
        return

    MODULE_DIR = Path(__file__).parent
    MODULE_PARENT_DIR = MODULE_DIR.parent.absolute()
    sys.path.insert(0, os.path.normpath(MODULE_PARENT_DIR))
    import main

    main.run_single_test_file(entry_script, authorize_before=True, exit_on_success=True)
