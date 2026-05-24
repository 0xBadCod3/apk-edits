# APK Workflow Automation

This repository automates the process of downloading APKs from other GitHub repositories, modifying their display names, and publishing them as releases in this repository.

## How it works

1.  A GitHub Actions workflow runs every 6 hours (or manually).
2.  It reads `apps.json` to find which apps to process.
3.  For each app, it checks the source repository for the latest release.
4.  If a new version is found (not yet released in this repo), it:
    *   Downloads the APK.
    *   Decompiles it using `apktool`.
    *   Changes the `android:label` (the name shown on the phone).
    *   Recompiles and signs the APK.
    *   Creates a new release in this repository with the modified APK.

## Configuration

The `apps.json` file contains a list of applications to be processed.

### Field Definitions

- `repo_url`: (GitHub only) The source repository URL.
- `source_type`: Either `"github"` (default) or `"scrape"`.
- `scrape_url`: (Scrape only) The website URL to monitor for updates.
- `apk_name_prefix`: Text used to identify the correct APK from the source.
- `new_display_name`: The new app label (name) to be applied to the APK.
- `release_tag_prefix`: The tag name used for releases in *this* repository.
- `latest_version`: Automatically updated by the script when a new version is released.
- `extra_assets`: (Optional) Additional assets to download (e.g., clones for MT Manager).

### Examples

**GitHub Source:**
```json
{
  "repo_url": "https://github.com/RetroMusicPlayer/RetroMusicPlayer",
  "apk_name_prefix": "Retro Music",
  "new_display_name": "Music",
  "release_tag_prefix": "RetroMusic"
}
```

**Scrape Source:**
```json
{
  "source_type": "scrape",
  "scrape_url": "https://mt2.cn/download/",
  "apk_name_prefix": "MT_Manager",
  "new_display_name": "MT Manager",
  "release_tag_prefix": "MT-Manager"
}
```

## Requirements

The GitHub Actions runner handles all dependencies:
- Java 17
- Python 3
- Apktool
- Uber-apk-signer
