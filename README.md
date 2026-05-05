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

Add your apps to `apps.json`:

```json
[
  {
    "repo_url": "https://github.com/owner/repo",
    "apk_name_prefix": "app-release",
    "new_display_name": "My Modified App",
    "release_tag_prefix": "OriginalAppName"
  }
]
```

- `repo_url`: The GitHub URL of the source repository.
- `apk_name_prefix`: The prefix of the APK file in the source release (e.g., `app-arm64-v8a`).
- `new_display_name`: The name you want to see after installation.
- `release_tag_prefix`: The name used for the release tag in this repository.

## Requirements

The GitHub Actions runner handles all dependencies:
- Java 17
- Python 3
- Apktool
- Uber-apk-signer
