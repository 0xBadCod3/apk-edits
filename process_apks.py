import os
import json
import subprocess
import re
import sys
import shutil
import logging
import fnmatch
from typing import Optional, Dict, Tuple, List
from curl_cffi.requests import Session

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("APKProcessor")

# Chrome 131 — better Cloudflare bypass for binary downloads than Safari
SESSION = Session(impersonate="chrome131")


# ---------------------------------------------------------------------------
# Scrapers for non-GitHub sources
# ---------------------------------------------------------------------------

def scrape_apktool_m(url: str) -> Tuple[Optional[str], Optional[str]]:
    import time

    NAV_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }

    try:
        logger.info("Apktool_M: loading main page...")
        r = SESSION.get(url, headers=NAV_HEADERS, timeout=30)
        r.raise_for_status()
        html = r.text

        _dl = re.search(r'(https?://maximoff\.su/apktool/dl/[^\s"\'<>]+\.apk[^\s"\'<>]*)', html)
        m = _dl


        if not m:
            logger.error("Apktool_M: could not find download link in page.")
            return None, None

        raw_url = m.group(1).replace("&amp;", "&")

        ver_m   = re.search(r'Apktool_M_v([\d]+\.[\d]+\.[\d]+)', raw_url)
        build_m = re.search(r'(?:%28|\()(\d+)(?:%29|\))', raw_url)
        version   = ver_m.group(1)   if ver_m   else None
        build_num = build_m.group(1) if build_m else None

        if build_num:
            agree_url = f"https://maximoff.su/apktool/agreement/?b={build_num}"
            logger.info(f"Apktool_M: visiting agreement page...")
            agree_hdrs = {
                **NAV_HEADERS,
                "Referer": url,
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
            }
            try:
                SESSION.get(agree_url, headers=agree_hdrs, timeout=15)
            except Exception as ae:
                logger.warning(f"Apktool_M: agreement page visit failed (continuing): {ae}")

        time.sleep(1)  # let cookie state settle before download
        logger.info(f"Apktool_M: URL={raw_url}, version={version}")
        return raw_url, version

    except Exception as e:
        logger.error(f"Apktool_M scrape failed: {e}")
        return None, None


def scrape_apktool_m_telegram(version: str) -> Optional[str]:
    tg_url = "https://t.me/s/apktool_m"
    try:
        r = SESSION.get(tg_url, timeout=30)
        r.raise_for_status()
        html = r.text

        messages = html.split("tgme_widget_message_wrap")

        logger.info(f"ApktoolM Telegram: scanning {len(messages)} message chunks for v{version}")

        for msg in reversed(messages):
            if version not in msg:
                continue

            doc_m = re.search(r"tgme_widget_message_document[^>]+href=[\"']([^\"']+)[\"']", msg)
            if not doc_m:
                doc_m = re.search(r"href=[\"']([^\"']+)[\"'][^>]*tgme_widget_message_document", msg)
            if doc_m:
                file_url = doc_m.group(1)
                logger.info(f"ApktoolM Telegram: found file URL {file_url}")
                return file_url

            ver_link = re.search(
                r"href=[\"']([^\"']*" + re.escape(version) + r"[^\"']*\.apk[^\"']*)[\"']",
                msg
            )
            if ver_link:
                file_url = ver_link.group(1)
                logger.info(f"ApktoolM Telegram: found versioned APK URL {file_url}")
                return file_url

        logger.error(f"ApktoolM Telegram: no message found for v{version}")
        return None

    except Exception as e:
        logger.error(f"ApktoolM Telegram scrape failed: {e}")
        return None


def scrape_mt_manager(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        html = r.text

        ver_m   = re.search(r'<li>[^<]*?v([\d]+\.[\d]+\.[\d]+)\s*</li>', html)

        build_m = re.search(r'pan\.mt2\.cn/apk/(\d{6,})(?:["\'<\s])', html)
        if ver_m and build_m:
            version  = ver_m.group(1)
            build_id = build_m.group(1)
            logger.info(f"MT-Manager (SSR path): version={version}, build_id={build_id}")
            return (
                f"https://pan.mt2.cn/apk/{build_id}",
                f"https://pan.mt2.cn/apk/{build_id}/clone",
                version,
            )

        chunk_urls = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
        base = url.rstrip("/")
        resolved = []
        for cu in chunk_urls:
            if cu.startswith("http"):
                resolved.append(cu)
            elif cu.startswith("/"):
                from urllib.parse import urlparse
                p = urlparse(url)
                resolved.append(f"{p.scheme}://{p.netloc}{cu}")
            else:
                resolved.append(f"{base}/{cu.lstrip('./')}")

        candidates = [
            u for u in resolved
            if re.search(r'/\d+\.[a-f0-9]+\.js$', u)
            or re.search(r'/[a-z][\w-]+\.[a-f0-9]{6,}\.js$', u)
        ]

        logger.info(f"MT-Manager: checking {len(candidates)} chunks for version data")

        for chunk_url in candidates:
            try:
                rc = SESSION.get(chunk_url, timeout=20)
                if rc.status_code != 200:
                    continue
                js = rc.text

                bm = re.search(r'pan\.mt2\.cn/apk[/\\]+(\d{6,})', js)
                if not bm:
                    continue

                build_id = bm.group(1)

                vm = (
                    re.search(r'["\'](\d+\.\d+\.\d+)["\']', js[max(0, bm.start()-200):bm.start()+200])
                    or re.search(r'versionName["\s:,]+["\']?([\d]+\.[\d]+\.[\d]+)', js)
                    or re.search(r'v([\d]+\.[\d]+\.[\d]+)', js[max(0, bm.start()-300):bm.start()+300])
                )
                version = vm.group(1) if vm else None

                if not version:
                    logger.warning(f"MT-Manager: found build_id={build_id} in {chunk_url} but no version.")
                    continue

                logger.info(f"MT-Manager (JS chunk): version={version}, build_id={build_id} from {chunk_url}")
                return (
                    f"https://pan.mt2.cn/apk/{build_id}",
                    f"https://pan.mt2.cn/apk/{build_id}/clone",
                    version,
                )

            except Exception as ce:
                logger.debug(f"Chunk {chunk_url} fetch failed: {ce}")
                continue

        logger.error("MT-Manager: exhausted all JS chunks without finding version/build_id.")
        return None, None, None

    except Exception as e:
        logger.error(f"MT-Manager scrape failed: {e}")
        return None, None, None


# ---------------------------------------------------------------------------
# Generic direct-URL downloader (follows redirects)
# ---------------------------------------------------------------------------

def download_url_to_file(url: str, dest: str, extra_headers: Optional[Dict] = None) -> bool:
    try:
        logger.info(f"Downloading {url} -> {dest}")
        headers = extra_headers or {}
        r = SESSION.get(url, headers=headers, stream=True, timeout=(15, 300))
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content():
                f.write(chunk)
        r.close()
        size = os.path.getsize(dest)
        logger.info(f"Downloaded {size:,} bytes to {dest}")
        return size > 0
    except Exception as e:
        logger.error(f"Download failed for {url}: {e}")
        return False


# ---------------------------------------------------------------------------
# Main processor class
# ---------------------------------------------------------------------------

class APKProcessor:

    def __init__(self, my_repo: str, token: str):
        self.my_repo = my_repo
        self.gh_headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

    # ---- GitHub source helpers ----

    def get_latest_release(self, repo_url: str) -> Optional[Dict]:
        repo_path = repo_url.replace("https://github.com/", "").strip("/")
        try:
            r = SESSION.get(
                f"https://api.github.com/repos/{repo_path}/releases/latest",
                headers=self.gh_headers, timeout=30
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Error fetching release for {repo_url}: {e}")
            return None

    def download_apk(self, release_data: Dict, original_asset_name: str) -> Tuple[Optional[str], Optional[str]]:
        assets = [a for a in release_data.get("assets", []) if a["name"].endswith(".apk")]
        if not assets:
            logger.warning("No APK assets found in release.")
            return None, None

        pattern = original_asset_name.lower() if original_asset_name else ""
        has_wildcards = "*" in pattern or "?" in pattern

        def is_match(text: str, pat: str) -> bool:
            text = text.lower()
            if has_wildcards:
                return fnmatch.fnmatch(text, pat)
            return pat in text

        # Try matching against filenames
        matches = [a for a in assets if is_match(a["name"], pattern)] if pattern else []

        if matches:
            selected = max(matches, key=lambda x: x["size"])
            logger.info(f"Matched asset by filename pattern: {selected['name']}")

        # Fallback: Match against release title
        elif pattern and not has_wildcards and is_match(release_data.get("name", ""), pattern):
            selected = max(assets, key=lambda x: x["size"])
            logger.info(f"Matched release title prefix: '{original_asset_name}'")

        # Final Fallback: If no pattern, pick largest
        else:
            if pattern:
                logger.error(f"No assets in latest release matched pattern: {original_asset_name}")
                return None, None
            selected = max(assets, key=lambda x: x["size"])
            logger.info(f"No pattern provided; selecting largest asset: {selected['name']}")

        try:
            logger.info(f"Downloading {selected['name']} ({selected['size']} bytes)...")
            r = SESSION.get(selected["browser_download_url"], stream=True, timeout=(15, 300))
            r.raise_for_status()
            with open("original.apk", "wb") as f:
                for chunk in r.iter_content():
                    f.write(chunk)
            r.close()
            return selected["name"], release_data["tag_name"]
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return None, None

    # ---- APK tools ----

    def get_apk_version(self, apk_path: str) -> str:
        logger.info("Extracting APK version...")
        temp_dir = "temp_version_extract"
        try:
            subprocess.run(
                ["java", "-jar", "apktool.jar", "d", apk_path, "-o", temp_dir, "-f", "-s"],
                check=True, capture_output=True
            )
            yml_path = os.path.join(temp_dir, "apktool.yml")
            if os.path.exists(yml_path):
                with open(yml_path, encoding="utf-8") as f:
                    m = re.search(r"versionName: ['\"']?([^'\"'\n\s]+)['\"']?", f.read())
                    if m:
                        return m.group(1).strip()
        except Exception as e:
            logger.error(f"Version extraction error: {e}")
        finally:
            self._safe_rmtree(temp_dir)
        return "unknown"

    def modify_apk(self, new_name: str, old_name_hint: str, input_path: str = "original.apk") -> Optional[str]:
        logger.info(f"Modifying app label to: {new_name}")
        decomp_dir = "decompiled_source"
        try:
            subprocess.run(
                ["java", "-jar", "APKEditor.jar", "d", "-i", input_path, "-o", decomp_dir],
                check=True
            )

            manifest_path = self._find_file(decomp_dir, "AndroidManifest.xml")
            if not manifest_path:
                logger.error("AndroidManifest.xml not found after decompile.")
                return None

            with open(manifest_path, encoding="utf-8") as f:
                manifest = f.read()

            m = re.search(r'android:label="([^"]+)"', manifest)
            if not m:
                logger.error("Could not find android:label in manifest.")
                return None

            label_ref = m.group(1)
            replaced = False

            for root, _, files in os.walk(decomp_dir):
                if "strings.xml" not in files:
                    continue
                s_path = os.path.join(root, "strings.xml")
                with open(s_path, encoding="utf-8") as f:
                    s_content = f.read()
                orig = s_content

                if label_ref.startswith("@string/"):
                    key = label_ref.split("/")[-1]
                    s_content = re.sub(
                        f'<string name="{re.escape(key)}">.*?</string>',
                        f'<string name="{key}">{new_name}</string>',
                        s_content, flags=re.DOTALL
                    )
                if old_name_hint and old_name_hint in s_content:
                    s_content = re.sub(
                        f'<string([^>]*)>{re.escape(old_name_hint)}</string>',
                        f'<string\\1>{new_name}</string>',
                        s_content
                    )
                if s_content != orig:
                    with open(s_path, "w", encoding="utf-8") as f:
                        f.write(s_content)
                    replaced = True

            if not replaced and not label_ref.startswith("@string/"):
                with open(manifest_path, "w", encoding="utf-8") as f:
                    f.write(manifest.replace(f'android:label="{label_ref}"', f'android:label="{new_name}"', 1))

            logger.info("Rebuilding and signing APK...")
            subprocess.run(
                ["java", "-jar", "APKEditor.jar", "b", "-i", decomp_dir, "-o", "unsigned_mod.apk"],
                check=True
            )
            subprocess.run(
                ["java", "-jar", "uber-apk-signer.jar", "--apks", "unsigned_mod.apk", "--out", "final_output"],
                check=True
            )

            for f in os.listdir("final_output"):
                if f.endswith(".apk"):
                    return os.path.join("final_output", f)

        except Exception as e:
            logger.error(f"Modification process failed: {e}")
        return None

    # ---- GitHub release upload ----

    def get_or_create_release(self, tag: str, source_url: str) -> Optional[Dict]:
        """Return (or create) a GitHub release for the given tag."""
        try:
            r = SESSION.post(
                f"https://api.github.com/repos/{self.my_repo}/releases",
                headers=self.gh_headers,
                json={
                    "tag_name": tag, "name": tag,
                    "body": f"Source: [Link]({source_url})",
                    "draft": False, "prerelease": False, "make_latest": "false"
                },
                timeout=30
            )
            if r.status_code == 201:
                return r.json()
            # Already exists — fetch it
            r = SESSION.get(
                f"https://api.github.com/repos/{self.my_repo}/releases/tags/{tag}",
                headers=self.gh_headers, timeout=30
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Failed to get/create release {tag}: {e}")
            return None

    def delete_existing_apk_assets(self, release: Dict):
        for asset in release.get("assets", []):
            if asset["name"].endswith(".apk"):
                SESSION.delete(
                    f"https://api.github.com/repos/{self.my_repo}/releases/assets/{asset['id']}",
                    headers=self.gh_headers, timeout=30
                )

    def upload_asset(self, release: Dict, file_path: str, asset_name: str) -> bool:
        upload_url = release["upload_url"].split("{")[0]
        upload_headers = {**self.gh_headers, "Content-Type": "application/vnd.android.package-archive"}
        try:
            with open(file_path, "rb") as f:
                r = SESSION.post(
                    f"{upload_url}?name={asset_name}",
                    headers=upload_headers, data=f.read(), timeout=120
                )
            if r.status_code == 201:
                logger.info(f"Uploaded asset: {asset_name}")
                return True
            logger.error(f"Upload failed ({r.status_code}): {r.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Upload exception for {asset_name}: {e}")
            return False

    def upload_to_release(self, tag: str, file_path: str, source_url: str) -> bool:
        release = self.get_or_create_release(tag, source_url)
        if not release:
            return False
        self.delete_existing_apk_assets(release)
        return self.upload_asset(release, file_path, os.path.basename(file_path))

    # ---- Utilities ----

    def cleanup(self):
        for target in ["decompiled_source", "final_output", "original.apk",
                        "clone.apk", "unsigned_mod.apk", "temp_version_extract"]:
            if not os.path.exists(target):
                continue
            self._safe_rmtree(target) if os.path.isdir(target) else os.remove(target)

    def _find_file(self, start_dir: str, target_name: str) -> Optional[str]:
        for root, _, files in os.walk(start_dir):
            if target_name in files:
                return os.path.join(root, target_name)
        return None

    def _safe_rmtree(self, path: str):
        try:
            shutil.rmtree(path)
        except Exception as e:
            logger.warning(f"Failed to remove {path}: {e}")


# ---------------------------------------------------------------------------
# Per-source processing functions
# ---------------------------------------------------------------------------

def process_github_app(processor: APKProcessor, app: Dict) -> bool:
    """Process a standard GitHub-release app. Returns True if a new version was released."""
    logger.info(f"--- GitHub: {app['repo_url']} ---")
    processor.cleanup()

    latest_rel = processor.get_latest_release(app["repo_url"])
    if not latest_rel:
        return False

    apk_name, _ = processor.download_apk(latest_rel, app.get("original_asset_name"))
    if not apk_name:
        return False

    current_ver = processor.get_apk_version("original.apk")
    if current_ver == app.get("latest_version"):
        logger.info(f"Version {current_ver} is already up-to-date.")
        return False

    logger.info(f"New version detected: {current_ver} (Previous: {app.get('latest_version', 'None')})")
    mod_path = processor.modify_apk(app["new_display_name"], app.get("original_asset_name"))
    if not mod_path:
        return False

    final_apk = f"{app['release_tag_prefix']}-v{current_ver}.apk"
    os.rename(mod_path, final_apk)

    if processor.upload_to_release(app["release_tag_prefix"], final_apk, app["repo_url"]):
        app["latest_version"] = current_ver
        logger.info(f"Successfully processed and released {final_apk}")
        if os.path.exists(final_apk):
            os.remove(final_apk)
        return True

    return False


def process_apktool_m(processor: APKProcessor, app: Dict) -> bool:
    logger.info(f"--- Scrape: Apktool_M ({app['scrape_url']}) ---")
    processor.cleanup()

    apk_url, version = scrape_apktool_m(app["scrape_url"])
    if not apk_url or not version:
        logger.error("Apktool_M: scrape returned no URL or version.")
        return False

    if version == app.get("latest_version"):
        logger.info(f"Apktool_M version {version} is already up-to-date.")
        return False

    logger.info(f"New Apktool_M version: {version} (Previous: {app.get('latest_version', 'None')})")

    downloaded = download_url_to_file(apk_url, "original.apk",
                                      extra_headers={"Referer": app["scrape_url"]})
    if not downloaded:
        logger.warning("Apktool_M: direct download failed — trying Telegram channel fallback...")
        tg_url = scrape_apktool_m_telegram(version)
        if not tg_url:
            logger.error("Apktool_M: Telegram fallback found no URL.")
            return False
        if not download_url_to_file(tg_url, "original.apk"):
            logger.error("Apktool_M: Telegram fallback download also failed.")
            return False

    tag       = app["release_tag_prefix"]          # "Apktool_M"
    final_apk = f"{tag}-v{version}.apk"            # "Apktool_M-v2.4.0.apk"
    os.rename("original.apk", final_apk)

    release = processor.get_or_create_release(tag, app["scrape_url"])
    if not release:
        return False
    processor.delete_existing_apk_assets(release)

    if processor.upload_asset(release, final_apk, final_apk):
        app["latest_version"] = version
        logger.info(f"Successfully released {final_apk}")
        if os.path.exists(final_apk):
            os.remove(final_apk)
        return True

    return False


def process_mt_manager(processor: APKProcessor, app: Dict) -> bool:
    logger.info(f"--- Scrape: MT_Manager ({app['scrape_url']}) ---")
    processor.cleanup()

    main_url, clone_url, version = scrape_mt_manager(app["scrape_url"])
    if not main_url or not version:
        logger.error("MT_Manager: scrape returned no URL or version.")
        return False

    if version == app.get("latest_version"):
        logger.info(f"MT_Manager version {version} is already up-to-date.")
        return False

    logger.info(f"New MT_Manager version: {version} (Previous: {app.get('latest_version', 'None')})")

    tag = app["release_tag_prefix"]   # "MT_Manager"

    # --- Full version ---
    main_final = f"MT_Manager-v{version}.apk"
    if not download_url_to_file(main_url, main_final):
        return False

    # --- Clone version ---
    clone_final = None
    if clone_url:
        _clone_dest = f"MT_Manager_clone-v{version}.apk"
        if download_url_to_file(clone_url, _clone_dest):
            clone_final = _clone_dest
        else:
            logger.warning("Clone APK download failed; skipping clone asset.")

    # --- Upload both to same release ---
    release = processor.get_or_create_release(tag, app["scrape_url"])
    if not release:
        return False
    processor.delete_existing_apk_assets(release)

    success = processor.upload_asset(release, main_final, main_final)
    if clone_final and os.path.exists(clone_final):
        processor.upload_asset(release, clone_final, clone_final)

    if success:
        app["latest_version"] = version
        logger.info(f"Successfully released {main_final}" + (f" + {clone_final}" if clone_final else ""))
        for f in [main_final, clone_final]:
            if f and os.path.exists(f):
                os.remove(f)
        return True

    return False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

SCRAPE_HANDLERS = {
    "maximoff.su": process_apktool_m,
    "mt2.cn":      process_mt_manager,
}


def get_scrape_handler(scrape_url: str):
    for domain, handler in SCRAPE_HANDLERS.items():
        if domain in scrape_url:
            return handler
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not os.path.exists("apps.json"):
        logger.error("Configuration file 'apps.json' not found.")
        return

    with open("apps.json", encoding="utf-8") as f:
        apps = json.load(f)

    my_repo = os.environ.get("GITHUB_REPOSITORY")
    token   = os.environ.get("GITHUB_TOKEN")
    if not my_repo or not token:
        logger.error("Required environment variables (GITHUB_REPOSITORY/GITHUB_TOKEN) are missing.")
        sys.exit(1)

    processor = APKProcessor(my_repo, token)
    change_detected = False

    for app in apps:
        source_type = app.get("source_type", "github")

        if source_type == "scrape":
            handler = get_scrape_handler(app.get("scrape_url", ""))
            if not handler:
                logger.error(f"No scrape handler for URL: {app.get('scrape_url')}")
                continue
            if handler(processor, app):
                change_detected = True

        else:
            if process_github_app(processor, app):
                change_detected = True

    if change_detected:
        with open("apps.json", "w", encoding="utf-8") as f:
            json.dump(apps, f, indent=2)

        if os.environ.get("GITHUB_ACTIONS") == "true":
            logger.info("Committing updated apps.json...")
            subprocess.run(["git", "config", "user.name",  "github-actions"], check=True)
            subprocess.run(["git", "config", "user.email", "github-actions@github.com"], check=True)
            subprocess.run(["git", "add", "apps.json"], check=True)
            status = subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True
            ).stdout
            if "apps.json" in status:
                subprocess.run(
                    ["git", "commit", "-m", "chore: update tracked app versions [skip ci]"],
                    check=True
                )
                subprocess.run(["git", "push"], check=True)
            else:
                logger.info("No changes to apps.json, skipping push.")

    processor.cleanup()
    logger.info("Workflow completed successfully.")


if __name__ == "__main__":
    main()
