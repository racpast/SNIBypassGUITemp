import os
import hashlib
import json
import zipfile
import shutil
import time
import sys
import base64

# --- Configuration ---
SOURCE_DIR = 'files'
RULES_SRC_DIR = 'src_rules' 
RULES_META_FILE = 'rules_meta.json'
FINAL_RULES_NAME = 'ProxyRules.json' 

TARGET_REPO_DIR = 'target-repo'
TARGET_FILES_DIR = os.path.join(TARGET_REPO_DIR, 'files')
EXE_NAME = 'SNIBypassGUI.exe'
VERSION_FILE = 'version.txt'
BASE_URL = "https://snib.racpast.com/files/"
CHUNK_SIZE = 20 * 1024 * 1024 # 20MB

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def calculate_sha256(file_path):
    if not os.path.exists(file_path): return None
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(4096), b""):
            sha.update(block)
    return sha.hexdigest()

def split_file(source_path, dest_dir):
    filename = os.path.basename(source_path)
    parts = []
    with open(source_path, 'rb') as f:
        idx = 0
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk: break
            part_name = f"{filename}.part{idx:03d}"
            part_path = os.path.join(dest_dir, part_name)
            with open(part_path, 'wb') as pf:
                pf.write(chunk)
            parts.append(part_name)
            idx += 1
    return parts

def normalize_path(path):
    return path.replace(os.sep, '/')

def compile_rules():
    log("Compiling proxy rules...")
    
    meta_path = os.path.join(RULES_SRC_DIR, RULES_META_FILE)
    if not os.path.exists(meta_path):
        log(f"Warning: Rules meta file not found at {meta_path}. Skipping rule compilation.")
        return

    with open(meta_path, 'r', encoding='utf-8') as f:
        rules_list = json.load(f)

    compiled_list = []
    
    for item in rules_list:
        rule_id = item.get("Id")
        if not rule_id:
            log(f"Error: Rule item missing 'Id': {item}")
            continue

        txt_path = os.path.join(RULES_SRC_DIR, f"{rule_id}.txt")
        hosts_content = ""
        
        if os.path.exists(txt_path):
            with open(txt_path, 'r', encoding='utf-8') as tf:
                hosts_content = tf.read().strip()
        else:
            log(f"Warning: No hosts file found for ID '{rule_id}' (Expected: {txt_path})")

        encoded_hosts = ""
        if hosts_content:
            normalized_content = "\r\n".join(hosts_content.splitlines())
            encoded_bytes = normalized_content.encode('utf-8')
            encoded_hosts = base64.b64encode(encoded_bytes).decode('utf-8')

        final_item = item.copy()
        final_item["Hosts"] = encoded_hosts
        final_item["Status"] = item.get("Status", 0)
        compiled_list.append(final_item)

    data_dir = os.path.join(SOURCE_DIR, 'Data')
    os.makedirs(data_dir, exist_ok=True)

    target_path = os.path.join(data_dir, FINAL_RULES_NAME)
    with open(target_path, 'w', encoding='utf-8') as f:
        json.dump(compiled_list, f, indent=4, ensure_ascii=False)
    
    log(f"Successfully compiled {len(compiled_list)} rules into {target_path}")

def main():
    log("Starting build process...")

    # 0. Compile Rules
    compile_rules()

    # 1. Environment Setup
    if not os.path.exists(SOURCE_DIR):
        log(f"Error: Source directory '{SOURCE_DIR}' missing.")
        sys.exit(1)
    if not os.path.exists(TARGET_FILES_DIR):
        os.makedirs(TARGET_FILES_DIR, exist_ok=True)

    valid_target_files = set()
    
    # 2. Read Version
    version = "V1.0.0"
    ver_path = os.path.join(SOURCE_DIR, VERSION_FILE)
    if os.path.exists(ver_path):
        with open(ver_path, 'r', encoding='utf-8') as f:
            version = f.read().strip()
    
    # 3. Load Existing Manifest
    old_manifest = {}
    manifest_path = os.path.join(TARGET_REPO_DIR, 'latest.json')
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r') as f:
                old_manifest = json.load(f)
        except:
            pass

    new_manifest = {
        "version": version,
        "timestamp": int(time.time()),
        "executable": {"update_required": False, "hash": "", "parts": []},
        "assets": []
    }

    # 4. Process Executable
    src_exe = os.path.join(SOURCE_DIR, EXE_NAME)
    if os.path.exists(src_exe):
        cur_hash = calculate_sha256(src_exe)
        old_hash = old_manifest.get("executable", {}).get("hash")

        if cur_hash == old_hash:
            log("Executable unchanged. Reusing artifacts.")
            new_manifest["executable"] = old_manifest.get("executable")
            for url in new_manifest["executable"]["parts"]:
                valid_target_files.add(url.split('/')[-1])
        else:
            log("Executable changed. Compressing and slicing...")
            tmp_zip = os.path.join(TARGET_FILES_DIR, "update.zip")
            with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(src_exe, EXE_NAME)
            
            parts = split_file(tmp_zip, TARGET_FILES_DIR)
            if os.path.exists(tmp_zip): os.remove(tmp_zip)

            parts_urls = []
            for p in parts:
                parts_urls.append(BASE_URL + p)
                valid_target_files.add(p)
            
            new_manifest["executable"] = {
                "update_required": True, 
                "hash": cur_hash, 
                "parts": parts_urls
            }

    # 5. Process Assets (Recursive)
    log("Syncing assets...")
    for root, dirs, files in os.walk(SOURCE_DIR):
        for file in files:
            if file == EXE_NAME or file == VERSION_FILE: continue
            
            src_path = os.path.join(root, file)
            rel_path = os.path.relpath(src_path, SOURCE_DIR)
            dst_path = os.path.join(TARGET_FILES_DIR, rel_path)
            
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copy2(src_path, dst_path)
            
            norm_path = normalize_path(rel_path)
            valid_target_files.add(norm_path)
            
            new_manifest["assets"].append({
                "path": norm_path,
                "url": BASE_URL + norm_path,
                "hash": calculate_sha256(src_path)
            })

    # 6. Cleanup Orphan Files
    log("Cleaning up target directory...")
    for root, dirs, files in os.walk(TARGET_FILES_DIR, topdown=False):
        for name in files:
            abs_path = os.path.join(root, name)
            rel_to_target = os.path.relpath(abs_path, TARGET_FILES_DIR)
            if normalize_path(rel_to_target) not in valid_target_files:
                log(f"Removing orphan: {rel_to_target}")
                os.remove(abs_path)
        
        for name in dirs:
            dir_path = os.path.join(root, name)
            if not os.listdir(dir_path):
                os.rmdir(dir_path)

    # 7. Write Manifest
    with open(manifest_path, "w", encoding='utf-8') as f:
        json.dump(new_manifest, f, indent=4)
    
    log("Build completed.")

if __name__ == "__main__":
    main()
