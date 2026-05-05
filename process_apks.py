import os
import json
import requests
import subprocess
import re
import sys
import shutil
import logging
from typing import Optional, Dict, List, Tuple

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("APKProcessor")

class APKProcessor:
    """Handles the full lifecycle of APK modification and release automation."""
    
    def __init__(self, my_repo: str, token: str):
        self.my_repo = my_repo
        self.token = token
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "APK-Automation-Agent"
        }

    def get_latest_release(self, repo_url: str) -> Optional[Dict]:
        """Fetches latest release metadata from a GitHub repository URL."""
        repo_path = repo_url.replace("https://github.com/", "").strip("/")
        api_url = f"https://api.github.com/repos/{repo_path}/releases/latest"
        try:
            response = requests.get(api_url, timeout=30, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching release for {repo_url}: {e}")
            return None

    def download_apk(self, release_data: Dict, apk_name_prefix: str) -> Tuple[Optional[str], Optional[str]]:
        """Identifies and downloads the most suitable APK from a release."""
        assets = [a for a in release_data.get('assets', []) if a['name'].endswith('.apk')]
        if not assets:
            logger.warning("No APK assets found in release.")
            return None, None
        
        selected_asset = None
        release_title = release_data.get('name', '')
        
        # Matching Strategy
        if apk_name_prefix:
            # 1. Match in Release Title
            if release_title and apk_name_prefix.lower() in release_title.lower():
                logger.info(f"Prefix '{apk_name_prefix}' matched release title.")
                selected_asset = max(assets, key=lambda x: x['size'])
            
            # 2. Match in Filenames
            if not selected_asset:
                matches = [a for a in assets if apk_name_prefix.lower() in a['name'].lower()]
                if matches:
                    selected_asset = max(matches, key=lambda x: x['size'])
                    logger.info(f"Prefix '{apk_name_prefix}' matched filename: {selected_asset['name']}")

        # 3. Final Fallback (Largest APK)
        if not selected_asset:
            selected_asset = max(assets, key=lambda x: x['size'])
            logger.info(f"Fallback: selecting largest asset {selected_asset['name']}")

        try:
            logger.info(f"Downloading {selected_asset['name']} ({selected_asset['size']} bytes)...")
            with requests.get(selected_asset['browser_download_url'], stream=True, timeout=60) as r:
                r.raise_for_status()
                with open("original.apk", "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            return selected_asset['name'], release_data['tag_name']
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return None, None

    def get_apk_version(self, apk_path: str) -> str:
        """Extracts the version name from an APK using apktool."""
        logger.info("Extracting APK version...")
        temp_dir = "temp_version_extract"
        try:
            # Perform a fast, resource-only decode to get metadata
            subprocess.run(["java", "-jar", "apktool.jar", "d", apk_path, "-o", temp_dir, "-f", "-s"], 
                           check=True, capture_output=True)
            yml_path = os.path.join(temp_dir, "apktool.yml")
            if os.path.exists(yml_path):
                with open(yml_path, "r", encoding="utf-8") as f:
                    match = re.search(r"versionName: ['\"]?([^'\"\n\s]+)['\"]?", f.read())
                    if match:
                        return match.group(1).strip()
        except Exception as e:
            logger.error(f"Version extraction error: {e}")
        finally:
            self._safe_rmtree(temp_dir)
        return "unknown"

    def modify_apk(self, new_name: str, old_name_hint: str) -> Optional[str]:
        """Modifies the app label and rebuilds/signs the APK."""
        logger.info(f"Modifying app label to: {new_name}")
        decomp_dir = "decompiled_source"
        try:
            # Decompile
            subprocess.run(["java", "-jar", "APKEditor.jar", "d", "-i", "original.apk", "-o", decomp_dir], check=True)
            
            manifest_path = self._find_file(decomp_dir, "AndroidManifest.xml")
            if not manifest_path:
                logger.error("AndroidManifest.xml not found after decompile.")
                return None

            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest_content = f.read()
            
            label_match = re.search(r'android:label="([^"]+)"', manifest_content)
            if not label_match:
                logger.error("Could not find android:label in manifest.")
                return None
                
            label_ref = label_match.group(1)
            found_and_replaced = False

            # Search and update string resources
            for root, _, files in os.walk(decomp_dir):
                if "strings.xml" in files:
                    s_path = os.path.join(root, "strings.xml")
                    with open(s_path, "r", encoding="utf-8") as f:
                        s_content = f.read()
                    
                    orig_content = s_content
                    # Replacement logic
                    if label_ref.startswith("@string/"):
                        s_key = label_ref.split("/")[-1]
                        s_content = re.sub(f'<string name="{re.escape(s_key)}">.*?</string>', 
                                          f'<string name="{s_key}">{new_name}</string>', 
                                          s_content, flags=re.DOTALL)
                    
                    if old_name_hint and old_name_hint in s_content:
                        s_content = re.sub(f'<string([^>]*)>{re.escape(old_name_hint)}</string>',
                                          f'<string\\1>{new_name}</string>', s_content)
                    
                    if s_content != orig_content:
                        with open(s_path, "w", encoding="utf-8") as f:
                            f.write(s_content)
                        found_and_replaced = True

            # Literal label replacement in Manifest if no strings were found
            if not found_and_replaced and not label_ref.startswith("@string/"):
                with open(manifest_path, "w", encoding="utf-8") as f:
                    f.write(manifest_content.replace(f'android:label="{label_ref}"', f'android:label="{new_name}"', 1))
                found_and_replaced = True

            # Build and Sign
            logger.info("Rebuilding and signing APK...")
            subprocess.run(["java", "-jar", "APKEditor.jar", "b", "-i", decomp_dir, "-o", "unsigned_mod.apk"], check=True)
            subprocess.run(["java", "-jar", "uber-apk-signer.jar", "--apks", "unsigned_mod.apk", "--out", "final_output"], check=True)
            
            for f in os.listdir("final_output"):
                if f.endswith(".apk"):
                    return os.path.join("final_output", f)
                    
        except Exception as e:
            logger.error(f"Modification process failed: {e}")
        return None

    def upload_to_release(self, tag: str, file_path: str, source_url: str) -> bool:
        """Uploads the modified APK to a static GitHub release."""
        logger.info(f"Publishing to release: {tag}")
        try:
            # Create or identify release
            release_body = f"Original Repo: [Link]({source_url})"
            data = {
                "tag_name": tag, "name": tag, "body": release_body,
                "draft": False, "prerelease": False, "make_latest": "false"
            }
            res = requests.post(f"https://api.github.com/repos/{self.my_repo}/releases", 
                                headers=self.headers, json=data, timeout=30)
            
            if res.status_code != 201:
                res = requests.get(f"https://api.github.com/repos/{self.my_repo}/releases/tags/{tag}", 
                                   headers=self.headers, timeout=30)
                res.raise_for_status()

            release_info = res.json()
            upload_base_url = release_info['upload_url'].split('{')[0]
            
            # Clean existing APK assets for this release
            for asset in release_info.get('assets', []):
                if asset['name'].endswith('.apk'):
                    requests.delete(f"https://api.github.com/repos/{self.my_repo}/releases/assets/{asset['id']}", 
                                    headers=self.headers, timeout=30)

            # Upload fresh asset
            filename = os.path.basename(file_path)
            upload_headers = self.headers.copy()
            upload_headers["Content-Type"] = "application/vnd.android.package-archive"
            
            with open(file_path, "rb") as f:
                r = requests.post(f"{upload_base_url}?name={filename}", headers=upload_headers, data=f, timeout=120)
                return r.status_code == 201
        except Exception as e:
            logger.error(f"Failed to upload release asset: {e}")
            return False

    def cleanup(self):
        """Removes all temporary build files and directories."""
        targets = ["decompiled_source", "final_output", "original.apk", "unsigned_mod.apk", "temp_version_extract"]
        for target in targets:
            if os.path.exists(target):
                if os.path.isdir(target): self._safe_rmtree(target)
                else: os.remove(target)

    def _find_file(self, start_dir: str, target_name: str) -> Optional[str]:
        """Utility to find a specific file recursively."""
        for root, _, files in os.walk(start_dir):
            if target_name in files:
                return os.path.join(root, target_name)
        return None

    def _safe_rmtree(self, path: str):
        """Safely removes a directory tree, handling permission issues."""
        try:
            shutil.rmtree(path)
        except Exception as e:
            logger.warning(f"Failed to remove directory {path}: {e}")

def main():
    if not os.path.exists("apps.json"):
        logger.error("Configuration file 'apps.json' not found.")
        return

    with open("apps.json", "r", encoding="utf-8") as f:
        apps = json.load(f)

    my_repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not my_repo or not token:
        logger.error("Required environment variables (GITHUB_REPOSITORY/GITHUB_TOKEN) are missing.")
        sys.exit(1)

    processor = APKProcessor(my_repo, token)
    change_detected = False

    for app in apps:
        logger.info(f"--- Starting Process for {app['repo_url']} ---")
        processor.cleanup()
        
        latest_rel = processor.get_latest_release(app['repo_url'])
        if not latest_rel: continue

        apk_name, _ = processor.download_apk(latest_rel, app['apk_name_prefix'])
        if not apk_name: continue

        current_ver = processor.get_apk_version("original.apk")
        if current_ver == app.get('latest_version'):
            logger.info(f"Version {current_ver} is already processed and up-to-date.")
            continue

        logger.info(f"New version detected: {current_ver} (Previous: {app.get('latest_version', 'None')})")
        mod_result_path = processor.modify_apk(app['new_display_name'], app['apk_name_prefix'])
        
        if mod_result_path:
            final_apk_name = f"{app['release_tag_prefix']}-v{current_ver}.apk"
            os.rename(mod_result_path, final_apk_name)
            
            if processor.upload_to_release(app['release_tag_prefix'], final_apk_name, app['repo_url']):
                app['latest_version'] = current_ver
                change_detected = True
                logger.info(f"Successfully processed and released {final_apk_name}")
                if os.path.exists(final_apk_name): os.remove(final_apk_name)

    if change_detected:
        with open("apps.json", "w", encoding="utf-8") as f:
            json.dump(apps, f, indent=2)
            
        if os.environ.get("GITHUB_ACTIONS") == "true":
            logger.info("Committing updated apps.json to repository...")
            subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
            subprocess.run(["git", "config", "user.email", "github-actions@github.com"], check=True)
            subprocess.run(["git", "add", "apps.json"], check=True)
            # Only commit if there are actual changes in the index
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
            if "apps.json" in status:
                subprocess.run(["git", "commit", "-m", "chore: update tracked app versions [skip ci]"], check=True)
                subprocess.run(["git", "push"], check=True)
            else:
                logger.info("No functional changes to apps.json, skipping git push.")
    
    processor.cleanup()
    logger.info("Workflow completed successfully.")

if __name__ == "__main__":
    main()
