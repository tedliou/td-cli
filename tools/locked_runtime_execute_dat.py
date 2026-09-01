"""Execute DAT entry point for the disposable locked-runtime acceptance project."""

import runpy


def onStart():
    module = runpy.run_path(r"E:\td-cli\tools\locked_runtime_acceptance.py", init_globals=globals())
    module["start"]()
