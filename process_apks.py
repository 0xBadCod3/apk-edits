import os
import json
import requests
import subprocess
import re
import sys

def get_latest_release(repo_url):
    # Convert https://github.com/owner/repo to owner/repo
    repo_path = repo_url.replace("https://github.com/", "").strip("/")
    api_url = f"https://api.github.com/repos/{repo_path}/releases/latest"
    response = requests.get(api_url)
    if response.status_code == 200:
        return response.json()
    return None

def download_apk(release_data, apk_name_prefix):
    assets = [a for a in release_data.get('assets', []) if a['name'].endswith('.apk')]
    if not assets:
        return None, None
    
    selected_asset = None
    release_name = release_data.get('name', '')
    
    # Priority 1: If prefix matches the Release Name (Title)
    if apk_name_prefix and release_name and apk_name_prefix.lower() in release_name.lower():
        print(f"Prefix '{apk_name_prefix}' matched release title: '{release_name}'")
        # In this case, just pick the largest APK as the intended one
        selected_asset = max(assets, key=lambda x: x['size'])
    
    # Priority 2: If prefix matches an APK filename
    if not selected_asset and apk_name_prefix:
        matches = [a for a in assets if apk_name_prefix.lower() in a['name'].lower()]
        if matches:
            selected_asset = max(matches, key=lambda x: x['size'])
            print(f"Prefix '{apk_name_prefix}' matched APK filename: '{selected_asset['name']}'")

    # Priority 3: Fallback to largest APK
    if not selected_asset:
        selected_asset = max(assets, key=lambda x: x['size'])
        print(f"No specific prefix match found in title or filename. Selected largest APK: {selected_asset['name']}")

    if selected_asset:
        print(f"Downloading {selected_asset['name']} ({selected_asset['size']} bytes)...")
        r = requests.get(selected_asset['browser_download_url'])
        with open("original.apk", "wb") as f:
            f.write(r.content)
        return selected_asset['name'], release_data['tag_name']
    
    return None, None

def modify_apk(new_display_name):
    print(f"Modifying APK label to: {new_display_name}")
    
    # REAndroid APKEditor can rename the app label using the 'refactor' command or by editing resources
    # However, for the most robust 'effortless' experience like ApkTool M, 
    # we can use the 'refactor' command if it supports renaming, 
    # or use it to decompile/rebuild if it's more reliable.
    
    # Actually, APKEditor has a 'refactor --rename-app' feature in some versions, 
    # but let's use its robust resource handling.
    # An even better tool specifically for renaming is 'APKEditor's 'edit' command or similar.
    
    # Let's try the REAndroid APKEditor 'm' (modify) or 'd' (decode) approach.
    # Most reliable: Decompile, change strings.xml (which APKEditor handles better), Rebuild.
    
    # 1. Decompile with APKEditor (it's often faster and more robust with resources)
    try:
        # APKEditor d -i original.apk -o decompiled
        subprocess.run(["java", "-jar", "APKEditor.jar", "d", "-i", "original.apk", "-o", "decompiled"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Decompile failed: {e}")
        return None
    
    # 2. Update Label
    # APKEditor keeps resources in a more accessible way.
    # We still need to find the label.
    manifest_path = "decompiled/AndroidManifest.xml"
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = f.read()
        
        app_tag_match = re.search(r'<application[^>]+android:label="([^"]+)"', manifest)
        if app_tag_match:
            label_val = app_tag_match.group(1)
            if label_val.startswith("@string/"):
                string_name = label_val.split("/")[-1]
                # APKEditor decodes resources into res/values/strings.xml or similar
                # We'll search for all strings.xml files to be sure
                found_string = False
                for root, dirs, files in os.walk("decompiled/res"):
                    if "strings.xml" in files:
                        s_path = os.path.join(root, "strings.xml")
                        with open(s_path, "r", encoding="utf-8") as f:
                            s_content = f.read()
                        if f'name="{string_name}"' in s_content:
                            new_s_content = re.sub(
                                f'<string name="{string_name}">.*?</string>',
                                f'<string name="{string_name}">{new_display_name}</string>',
                                s_content,
                                flags=re.DOTALL
                            )
                            with open(s_path, "w", encoding="utf-8") as f:
                                f.write(new_s_content)
                            print(f"Updated {string_name} in {s_path}")
                            found_string = True
                if not found_string:
                    print(f"Could not find string resource {string_name}")
            else:
                new_manifest = manifest.replace(f'android:label="{label_val}"', f'android:label="{new_display_name}"', 1)
                with open(manifest_path, "w", encoding="utf-8") as f:
                    f.write(new_manifest)
                print("Updated literal label in manifest")

    # 3. Build with APKEditor
    try:
        # APKEditor b -i decompiled -o modified_unsigned.apk
        subprocess.run(["java", "-jar", "APKEditor.jar", "b", "-i", "decompiled", "-o", "modified_unsigned.apk"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}")
        return None
    
    # 4. Sign with uber-apk-signer (the 'debug key' part)
    try:
        subprocess.run([
            "java", "-jar", "uber-apk-signer.jar", 
            "--apks", "modified_unsigned.apk", 
            "--out", "output"
        ], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Signing failed: {e}")
        return None
    
    if os.path.exists("output"):
        for f in os.listdir("output"):
            if f.endswith(".apk"):
                return os.path.join("output", f)
    return None

def upload_to_release(repo, token, tag, release_name, file_path):
    print(f"Creating release {tag}...")
    create_url = f"https://api.github.com/repos/{repo}/releases"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "tag_name": tag,
        "name": release_name,
        "body": f"Automated release for {release_name}",
        "draft": False,
        "prerelease": False
    }
    res = requests.post(create_url, headers=headers, json=data)
    if res.status_code != 201:
        print(f"Failed to create release: {res.text}")
        # Maybe it already exists? Try to get it.
        if res.status_code == 422: # Unprocessable Entity, often means tag exists
            get_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
            res = requests.get(get_url, headers=headers)
            if res.status_code != 200:
                return False
        else:
            return False
    
    release_id = res.json()['id']
    upload_url = res.json()['upload_url'].split('{')[0]
    
    filename = os.path.basename(file_path)
    upload_url = f"{upload_url}?name={filename}"
    
    print(f"Uploading {filename} to release...")
    with open(file_path, "rb") as f:
        headers["Content-Type"] = "application/vnd.android.package-archive"
        res = requests.post(upload_url, headers=headers, data=f)
    
    if res.status_code == 201:
        print("Upload successful")
        return True
    else:
        print(f"Upload failed: {res.text}")
        return False

def get_apk_version(apk_path):
    print(f"Extracting version from {apk_path}...")
    try:
        # We can use apktool's internal aapt or just dump the manifest info
        # Using 'apktool d' is already done in modify_apk, but we need it BEFORE that to decide.
        # Let's use 'aapt dump badging' if available, or just a quick decode.
        result = subprocess.run(["apktool", "d", apk_path, "-o", "temp_version_check", "-f"], check=True, capture_output=True)
        yml_path = "temp_version_check/apktool.yml"
        if os.path.exists(yml_path):
            with open(yml_path, "r") as f:
                content = f.read()
                version_match = re.search(r"versionName: ['\"]?([^'\"\n]+)['\"]?", content)
                if version_match:
                    version = version_match.group(1)
                    import shutil
                    shutil.rmtree("temp_version_check")
                    return version
        if os.path.exists("temp_version_check"):
            import shutil
            shutil.rmtree("temp_version_check")
    except Exception as e:
        print(f"Failed to get version: {e}")
    return "unknown"

def main():
    if not os.path.exists("apps.json"):
        print("apps.json not found")
        return

    with open("apps.json", "r") as f:
        apps = json.load(f)

    my_repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")

    if not my_repo or not token:
        print("GITHUB_REPOSITORY or GITHUB_TOKEN not set")
        sys.exit(1)

    updated_apps = []
    any_changes = False

    for app in apps:
        repo_url = app['repo_url']
        apk_prefix = app['apk_name_prefix']
        new_name = app['new_display_name']
        release_tag_prefix = app['release_tag_prefix']
        last_known_version = app.get('latest_version', '')
        
        print(f"\n--- Processing {repo_url} ---")
        latest = get_latest_release(repo_url)
        if not latest:
            print(f"No release found for {repo_url}")
            updated_apps.append(app)
            continue
        
        apk_filename, github_tag = download_apk(latest, apk_prefix)
        if not apk_filename:
            print("No matching APK found")
            updated_apps.append(app)
            continue

        actual_version = get_apk_version("original.apk")
        print(f"Source Version: {actual_version} (Last known: {last_known_version})")

        if actual_version == last_known_version and last_known_version != "":
            print(f"Version {actual_version} already processed. Skipping.")
            updated_apps.append(app)
            if os.path.exists("original.apk"): os.remove("original.apk")
            continue

        # Process new version
        modified_path = modify_apk(new_name)
        if modified_path:
            final_name = f"{release_tag_prefix}_{actual_version}.apk"
            if os.path.exists(final_name): os.remove(final_name)
            os.rename(modified_path, final_name)
            
            target_tag = f"{release_tag_prefix}-{actual_version}"
            if upload_to_release(my_repo, token, target_tag, f"{release_tag_prefix} {actual_version}", final_name):
                app['latest_version'] = actual_version
                any_changes = True
        
        updated_apps.append(app)
        
        # Cleanup
        for path in ["decompiled", "output", "original.apk", "modified_unsigned.apk"]:
            if os.path.exists(path):
                if os.path.isdir(path):
                    import shutil
                    shutil.rmtree(path)
                else:
                    os.remove(path)

    if any_changes:
        with open("apps.json", "w") as f:
            json.dump(updated_apps, f, indent=2)
        print("\nUpdated apps.json with new versions.")
        
        # In GHA, we need to commit these changes back to the repo
        if os.environ.get("GITHUB_ACTIONS") == "true":
            subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
            subprocess.run(["git", "config", "user.email", "github-actions@github.com"], check=True)
            subprocess.run(["git", "add", "apps.json"], check=True)
            subprocess.run(["git", "commit", "-m", "Update apps.json with latest versions [skip ci]"], check=True)
            subprocess.run(["git", "push"], check=True)
    else:
        print("\nNo new versions to process.")

if __name__ == "__main__":
    main()
