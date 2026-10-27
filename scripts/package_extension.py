"""
package_extension.py: Package the DecisionAI Chrome Extension for Distribution

Validates Manifest V3 components and builds a clean zip distribution in dist/.
"""

import json
import os
import zipfile
from pathlib import Path


def package_extension():
    root_dir = Path(__file__).resolve().parent.parent
    extension_dir = root_dir / "extension"
    dist_dir = root_dir / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dist_dir / "decisionai-chrome-extension.zip"

    manifest_file = extension_dir / "manifest.json"
    if not manifest_file.exists():
        raise FileNotFoundError(f"manifest.json not found at {manifest_file}")

    with open(manifest_file, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    name = manifest_data.get("name", "DecisionAI Extension")
    version = manifest_data.get("version", "1.0.0")
    print(f"Packaging {name} v{version}...")

    # Required files check
    required_files = [
        "manifest.json",
        "config.js",
        "popup.html",
        "popup.css",
        "popup.js",
        "offscreen.html",
        "offscreen.js",
        "pcm-processor.js",
        "permission.html",
        "permission.js",
        "service-worker.js",
    ]

    for req in required_files:
        p = extension_dir / req
        if not p.exists():
            raise FileNotFoundError(f"Required extension file missing: {req}")

    if zip_path.exists():
        zip_path.unlink()

    file_count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in extension_dir.rglob("*"):
            if file_path.is_file():
                # Avoid hidden or temporary files
                if file_path.name.startswith("."):
                    continue
                rel_path = file_path.relative_to(extension_dir)
                zipf.write(file_path, arcname=str(rel_path))
                file_count += 1
                print(f"  + Added: {rel_path}")

    zip_size_kb = os.path.getsize(zip_path) / 1024
    print(f"\nSuccessfully packaged {file_count} files into:")
    print(f"  {zip_path} ({zip_size_kb:.1f} KB)")
    return zip_path


if __name__ == "__main__":
    package_extension()
