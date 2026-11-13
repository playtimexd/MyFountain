"""KimodoDeps: runs once when the Unreal editor starts (after the PythonScriptPlugin's pip install).

Checks that the Kimodo packages are installed in the project's Python environment and logs the result to the Output Log
(category LogPython).  Nothing heavy is imported here: torch is only looked up, not loaded, so the editor start stays fast.
Run `import kimodo_deps_check; kimodo_deps_check.full()` from the Python console for a full check including CUDA.
"""
import importlib.metadata as md

import unreal

REQUIRED = {"torch": "+cu", "numpy": "1.", "scipy": None, "hydra-core": None, "omegaconf": None, "transformers": None,
            "peft": None, "einops": None, "trimesh": None, "bvhio": None, "av": None, "tqdm": None, "antlr4-python3-runtime": "4.9."}


def _check():
    missing, wrong = [], []
    for name, want in REQUIRED.items():
        try:
            ver = md.version(name)
        except md.PackageNotFoundError:
            missing.append(name)
            continue
        if want and want not in ver:
            wrong.append(f"{name} {ver} (expected {want}...)")
    return missing, wrong


missing, wrong = _check()
if not missing and not wrong:
    unreal.log(f"KimodoDeps: all Kimodo Python packages present (torch {md.version('torch')}, numpy {md.version('numpy')}).")
else:
    if missing:
        unreal.log_warning("KimodoDeps: missing Python packages: " + ", ".join(missing))
    if wrong:
        unreal.log_warning("KimodoDeps: unexpected versions: " + "; ".join(wrong))
    unreal.log_warning("KimodoDeps: the editor installs them on start-up (Project Settings > Plugins > Python > "
                       "'Run Pip Install on Startup' must be on). Check the pip output above in the Output Log, or run "
                       "install_kimodo_deps.ps1 -InstallNow with the editor closed.")
