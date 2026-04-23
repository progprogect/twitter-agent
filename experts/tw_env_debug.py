# =============================================================================
# EXTELLA EXPERT: tw_env_debug
# =============================================================================
# DESCRIPTION: Diagnostic: shows Python environment info (sys.executable, sys.path, installed packages) to understand why playwright import fails. One-time debugging tool.
#
# KWARGS (default parameters):
# {}
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_env_debug    # sync this file only
#   python sync_to_extella.py --all     # sync all experts
#
# RULES (see EXPERT_RULES.md):
# - $extens("include.py") must be the first code line
# - Dependencies via: include("import X", ["extella-pip install X"])
# - Function name = filename without .py
# - All params with type hints + default values -> dict
# - NO hardcoded credentials, paths, or personal data
# =============================================================================

$extens("include.py")
include("import requests", ["extella-pip install requests"])

def tw_env_debug() -> dict:
    import sys
    import os
    import subprocess
    import importlib.util

    print("[1/4] 🔬 Environment diagnostics")
    print(f"[1/4] sys.executable: {sys.executable}")
    print(f"[1/4] sys.version: {sys.version[:50]}")

    # Check sys.path
    print("[2/4] 📂 sys.path:")
    for p in sys.path[:10]:
        print(f"  {p}")

    # Check if playwright package files exist
    print("[3/4] 🔍 Looking for playwright...")
    pw_spec = importlib.util.find_spec("playwright")
    print(f"  find_spec('playwright'): {pw_spec}")

    # Try to find playwright in common locations
    possible_locations = []
    for p in sys.path:
        pw_path = os.path.join(p, "playwright")
        if os.path.exists(pw_path):
            possible_locations.append(pw_path)
            print(f"  Found playwright at: {pw_path}")

    # Check pip list
    print("[3/4] 📦 Checking pip list for playwright...")
    r = subprocess.run([sys.executable, "-m", "pip", "show", "playwright"],
                       capture_output=True, text=True)
    pip_output = r.stdout.strip()
    print(f"  pip show playwright:\n{pip_output}")

    # Try import with sys.path manipulation
    print("[4/4] 🧪 Trying import...")
    import_ok = False
    try:
        from playwright.sync_api import sync_playwright
        import_ok = True
        print("  ✅ playwright imported successfully!")
    except ImportError as e:
        print(f"  ❌ ImportError: {e}")

        # Try adding site-packages from sys.executable
        site_result = subprocess.run(
            [sys.executable, "-c", "import site; [print(p) for p in site.getsitepackages()]"],
            capture_output=True, text=True
        )
        site_packages = [p.strip() for p in site_result.stdout.strip().split("\n") if p.strip()]
        print(f"  site-packages from sys.executable: {site_packages}")

        for sp in site_packages:
            if sp not in sys.path:
                sys.path.insert(0, sp)
                print(f"  Added to sys.path: {sp}")

        import importlib
        importlib.invalidate_caches()

        try:
            from playwright.sync_api import sync_playwright
            import_ok = True
            print("  ✅ playwright imported after sys.path fix!")
        except ImportError as e2:
            print(f"  ❌ Still failing: {e2}")

    print("[4/4] ✅ Done")
    return {
        "status": "success",
        "sys_executable": sys.executable,
        "sys_path": sys.path[:8],
        "playwright_spec": str(pw_spec),
        "playwright_found_at": possible_locations,
        "pip_show": pip_output[:300],
        "import_ok": import_ok
    }
