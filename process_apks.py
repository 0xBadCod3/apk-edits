import os
import json
import requests
import subprocess
import re
import sys
import shutil
import logging

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class APKProcessor:
    def __init__(self, repo, token):
        self.my_repo = repo
        self.token = token
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

    def get_latest_release(self, repo_url):
        repo_path = repo_url.replace("https://github.com/", "").strip("/")
        api_url = f"https://api.github.com/repos/{repo_path}/releases/latest"
        try:
            response = requests.get(api_url, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch latest release for {repo_url}: {e}")
            return None

    def download_apk(self, release_data, apk_name_prefix):
        assets = [a for a in release_data.get('assets', []) if a['name'].endswith('.apk')]
        if not assets:
            return None, None
        
        selected_asset = None
        release_title = release_data.get('name', '')
        
        # Matching logic
        if apk_name_prefix:
            # Check Title
            if release_title and apk_name_prefix.lower() in release_title.lower():
                logger.info(f"Matched prefix '{apk_name_prefix}' in release title")
                selected_asset = max(assets, key=lambda x: x['size'])
            # Check Filenames
            if not selected_asset:
                matches = [a for a in assets if apk_name_prefix.lower() in a['name'].lower()]
                if matches:
                    selected_asset = max(matches, key=lambda x: x['size'])
                    logger.info(f"Matched prefix '{apk_name_prefix}' in filename: {selected_asset['name']}")

        # Fallback to largest
        if not selected_asset:
            selected_asset = max(assets, key=lambda x: x['size'])
            logger.info(f"Using fallback: selected largest APK {selected_asset['name']}")

        try:
            logger.info(f"Downloading {selected_asset['name']}...")
            r = requests.get(selected_asset['browser_download_url'], stream=True, timeout=60)
            r.raise_for_status()
            with open("original.apk", "wb") as f:
                shutil.copyfileobj(r.raw, f)
            return selected_asset['name'], release_data['tag_name']
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return None, None

    def get_apk_version(self, apk_path):
        logger.info("Extracting APK version...")
        temp_dir = "temp_ver"
        try:
            subprocess.run(["java", "-jar", "apktool.jar", "d", apk_path, "-o", temp_dir, "-f"], 
                           check=True, capture_output=True)
            yml_path = os.path.join(temp_dir, "apktool.yml")
            if os.path.exists(yml_path):
                with open(yml_path, "r") as f:
                    match = re.search(r"versionName: ['\"]?([^'\"\n]+)['\"]?", f.read())
                    if match:
                        return match.group(1)
        except Exception as e:
            logger.error(f"Version extraction failed: {e}")
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
        return "unknown"

    def modify_apk(self, new_name, old_name_hint):
        logger.info(f"Modifying label: {old_name_hint} -> {new_name}")
        decompiled_dir = "decompiled"
        try:
            # Decode
            subprocess.run(["java", "-jar", "APKEditor.jar", "d", "-i", "original.apk", "-o", decompiled_dir], check=True)
            
            # Find and Replace
            manifest_path = self._find_file(decompiled_dir, "AndroidManifest.xml")
            if not manifest_path:
                return None

            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = f.read()
            
            label_match = re.search(r'android:label="([^"]+)"', manifest)
            if not label_match:
                return None
                
            label_ref = label_match.group(1)
            found_replaced = False

            # Scan strings.xml files
            for root, _, files in os.walk(decompiled_dir):
                if "strings.xml" in files:
                    path = os.path.join(root, "strings.xml")
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    new_content = content
                    if label_ref.startswith("@string/"):
                        s_name = label_ref.split("/")[-1]
                        new_content = re.sub(f'<string name="{s_name}">.*?</string>', 
                                            f'<string name="{s_name}">{new_name}</string>', 
                                            new_content, flags=re.DOTALL)
                    
                    if old_name_hint in content:
                        new_content = re.sub(f'<string([^>]*)>{re.escape(old_name_hint)}</string>',
                                            f'<string\\1>{new_name}</string>', new_content)
                    
                    if new_content != content:
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(new_content)
                        found_replaced = True

            # Literal manifest fallback
            if not found_replaced and not label_ref.startswith("@string/"):
                with open(manifest_path, "w", encoding="utf-8") as f:
                    f.write(manifest.replace(f'android:label="{label_ref}"', f'android:label="{new_name}"', 1))
                found_replaced = True

            # Build and Sign
            subprocess.run(["java", "-jar", "APKEditor.jar", "b", "-i", decompiled_dir, "-o", "unsigned.apk"], check=True)
            subprocess.run(["java", "-jar", "uber-apk-signer.jar", "--apks", "unsigned.apk", "--out", "output"], check=True)
            
            for f in os.listdir("output"):
                if f.endswith(".apk"):
                    return os.path.join("output", f)
        except Exception as e:
            logger.error(f"Modification failed: {e}")
        return None

    def upload_to_release(self, tag, file_path, source_url):
        logger.info(f"Uploading to release: {tag}")
        try:
            # Create/Get Release
            data = {"tag_name": tag, "name": tag, "body": f"Original Repo: [Link]({source_url})", 
                    "draft": False, "prerelease": False, "make_latest": "false"}
            res = requests.post(f"https://api.github.com/repos/{self.my_repo}/releases", 
                                headers=self.headers, json=data)
            
            if res.status_code != 201:
                res = requests.get(f"https://api.github.com/repos/{self.my_repo}/releases/tags/{tag}", headers=self.headers)
                res.raise_for_status()

            release_data = res.json()
            upload_url = release_data['upload_url'].split('{')[0]
            
            # Clean existing APKs
            for asset in release_data.get('assets', []):
                if asset['name'].endswith('.apk'):
                    requests.delete(f"https://api.github.com/repos/{self.my_repo}/releases/assets/{asset['id']}", 
                                    headers=self.headers)

            # Upload
            filename = os.path.basename(file_path)
            with open(file_path, "rb") as f:
                r = requests.post(f"{upload_url}?name={filename}", headers=self.headers, 
                                  data=f, headers={"Content-Type": "application/vnd.android.package-archive"})
                return r.status_code == 201
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return False

    def _find_file(self, start_dir, target_name):
        for root, _, files in os.walk(start_dir):
            if target_name in files:
                return os.path.join(root, target_name)
        return None

    def cleanup(self):
        for path in ["decompiled", "output", "original.apk", "unsigned.apk", "temp_ver"]:
            if os.path.exists(path):
                if os.path.isdir(path): shutil.rmtree(path)
                else: os.remove(path)

def main():
    if not os.path.exists("apps.json"):
        logger.error("apps.json not found")
        return

    with open("apps.json", "r") as f:
        apps = json.load(f)

    my_repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not my_repo or not token:
        logger.error("Environment variables missing")
        sys.exit(1)

    processor = APKProcessor(my_repo, token)
    any_changes = False

    for app in apps:
        logger.info(f"--- Processing {app['repo_url']} ---")
        processor.cleanup()
        
        latest = processor.get_latest_release(app['repo_url'])
        if not latest: continue

        apk_file, _ = processor.download_apk(latest, app['apk_name_prefix'])
        if not apk_file: continue

        ver = processor.get_apk_version("original.apk")
        if ver == app.get('latest_version'):
            logger.info(f"Version {ver} is up to date.")
            continue

        mod_apk = processor.modify_apk(app['new_display_name'], app['apk_name_prefix'])
        if mod_apk:
            final_name = f"{app['release_tag_prefix']}_{ver}.apk"
            os.rename(mod_apk, final_name)
            if processor.upload_to_release(app['release_tag_prefix'], final_name, app['repo_url']):
                app['latest_version'] = ver
                any_changes = True
                os.remove(final_name)

    if any_changes:
        with open("apps.json", "w") as f:
            json.dump(apps, f, indent=2)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
            subprocess.run(["git", "config", "user.email", "github-actions@github.com"], check=True)
            subprocess.run(["git", "add", "apps.json"], check=True)
            subprocess.run(["git", "commit", "-m", "chore: update app versions [skip ci]"], check=True)
            subprocess.run(["git", "push"], check=True)
    
    processor.cleanup()

if __name__ == "__main__":
    main()
