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
    for asset in release_data.get('assets', []):
        if asset['name'].startswith(apk_name_prefix) and asset['name'].endswith('.apk'):
            print(f"Downloading {asset['name']}...")
            r = requests.get(asset['browser_download_url'])
            with open("original.apk", "wb") as f:
                f.write(r.content)
            return asset['name'], release_data['tag_name']
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

    # Ensure tools are in PATH or current dir
    # We will download them in the GHA workflow

    for app in apps:
        repo_url = app['repo_url']
        apk_prefix = app['apk_name_prefix']
        new_name = app['new_display_name']
        release_tag_prefix = app['release_tag_prefix']
        
        print(f"\n--- Processing {repo_url} ---")
        latest = get_latest_release(repo_url)
        if not latest:
            print(f"No release found for {repo_url}")
            continue
        
        tag = latest['tag_name']
        target_tag = f"{release_tag_prefix}-{tag}"
        
        # Check if release exists in current repo
        check_url = f"https://api.github.com/repos/{my_repo}/releases/tags/{target_tag}"
        res = requests.get(check_url, headers={"Authorization": f"token {token}"})
        if res.status_code == 200:
            print(f"Release {target_tag} already exists, skipping.")
            continue

        apk_filename, version = download_apk(latest, apk_prefix)
        if apk_filename:
            # Clean up previous runs
            if os.path.exists("decompiled"):
                import shutil
                shutil.rmtree("decompiled")
            if os.path.exists("output"):
                import shutil
                shutil.rmtree("output")
            if os.path.exists("original.apk"):
                os.remove("original.apk")
            if os.path.exists("modified_unsigned.apk"):
                os.remove("modified_unsigned.apk")

            # Re-download since I just deleted it for cleanup (oops, logic order)
            # Actually download_apk creates original.apk. Let's fix the loop.
            
            # (Re-running download if I moved cleanup)
            apk_filename, version = download_apk(latest, apk_prefix)

            modified_path = modify_apk(new_name)
            if modified_path:
                final_name = f"{release_tag_prefix}_{version}.apk"
                if os.path.exists(final_name): os.remove(final_name)
                os.rename(modified_path, final_name)
                print(f"Successfully created {final_name}")
                
                upload_to_release(my_repo, token, target_tag, f"{release_tag_prefix} {version}", final_name)
            else:
                print("Failed to modify APK")
        else:
            print("No matching APK found in latest release")

if __name__ == "__main__":
    main()
