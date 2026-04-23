# =============================================================================
# EXTELLA EXPERT: tw_install_playwright
# =============================================================================
# DESCRIPTION: One-time setup: installs playwright Python package and downloads Chromium browser binary. Run once if getting 'No module named playwright' errors in tw_discover, tw_posts, tw_monitor, or tw_test_browser_http.
#
# KWARGS (default parameters):
# {}
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_install_playwright    # sync this file only
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
include("import subprocess", [])

def tw_install_playwright() -> dict:
    import subprocess
    import sys
    from pathlib import Path

    print("[1/4] 📦 Installing playwright Python package...")
    r1 = subprocess.run(
        [sys.executable, "-m", "pip", "install", "playwright"],
        capture_output=False  # show output
    )
    print(f"[2/4] pip install result: {r1.returncode}")

    print("[3/4] 🌐 Downloading Chromium browser binary...")
    r2 = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"]
    )
    print(f"[3/4] chromium install result: {r2.returncode}")

    # Verify
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            path = pw.chromium.executable_path()
        chromium_ok = Path(path).exists() if path else False
        print(f"[4/4] ✅ Verification: playwright imported OK, chromium={chromium_ok}")
        return {
            "status": "success",
            "playwright_installed": True,
            "chromium_binary_found": chromium_ok,
            "chromium_path": str(path)[:60] + "..." if path else "unknown",
            "message": "Playwright ready. You can now run tw_test_browser_http."
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Installation completed but import failed: {e}",
            "pip_returncode": r1.returncode,
            "chromium_returncode": r2.returncode
        }
