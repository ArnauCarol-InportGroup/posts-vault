#!/usr/bin/env python3
"""Encrypted storage for the post folders.

Every post folder is zipped and encrypted with AES-256-GCM into vault/<id>.bin,
where <id> is an HMAC of the folder name, so neither content nor names are
readable in the repo. The tool state is kept encrypted in vault/state.bin.

Usage:
    python tools/vault.py encrypt [--prune]  # local folders -> vault/
    python tools/vault.py decrypt            # vault/ -> local folders
    python tools/vault.py list               # show what the vault holds

The password comes from POSTS_PASSWORD (environment or .env) or is prompted.
"""

import argparse
import functools
import getpass
import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"
STATE_BLOB = VAULT / "state.bin"
MAGIC = b"PV1"
SCRYPT = dict(n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def load_dotenv():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def password():
    load_dotenv()
    pw = os.environ.get("POSTS_PASSWORD")
    if not pw:
        if not sys.stdin.isatty():
            sys.exit("POSTS_PASSWORD is not set.")
        pw = getpass.getpass("Contraseña de los posts: ")
    return pw.encode()


# ------------------------------------------------------------------ crypto


def encrypt(pw, data):
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = hashlib.scrypt(pw, salt=salt, **SCRYPT)
    return MAGIC + salt + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def decrypt(pw, blob):
    if blob[:3] != MAGIC:
        raise ValueError("not a vault file")
    salt, nonce, ct = blob[3:19], blob[19:31], blob[31:]
    key = hashlib.scrypt(pw, salt=salt, **SCRYPT)
    try:
        return AESGCM(key).decrypt(nonce, ct, MAGIC)
    except InvalidTag:
        sys.exit("Contraseña incorrecta (o fichero del vault dañado).")


@functools.cache
def name_key(pw):
    return hashlib.scrypt(pw, salt=b"posts-vault/names", **SCRYPT)


def blob_name(pw, folder_name):
    return hmac.new(name_key(pw), folder_name.encode(), hashlib.sha256).hexdigest()[:24] + ".bin"


# ------------------------------------------------------------------- posts


def zip_folder(folder):
    """Deterministic zip, so an unchanged folder produces identical bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(p for p in folder.iterdir() if p.is_file()):
            info = zipfile.ZipInfo(f"{folder.name}/{f.name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            zf.writestr(info, f.read_bytes())
    return buf.getvalue()


def unzip(data):
    """Return (folder_name, {file_name: bytes})."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        files = {}
        folder = None
        for name in zf.namelist():
            folder, file_name = name.split("/", 1)
            files[file_name] = zf.read(name)
    return folder, files


def local_folders():
    return sorted(p for p in ROOT.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and (p / "post.md").exists())


def load_posts(pw):
    """All posts in the vault as [(folder_name, files)], sorted by folder name."""
    posts = [unzip(decrypt(pw, p.read_bytes()))
             for p in VAULT.glob("*.bin") if p != STATE_BLOB]
    return sorted(posts, key=lambda post: post[0])


def load_state(pw):
    if not STATE_BLOB.exists():
        return {"next": None, "history": []}
    return json.loads(decrypt(pw, STATE_BLOB.read_bytes()))


def save_state(pw, state):
    if STATE_BLOB.exists() and json.loads(decrypt(pw, STATE_BLOB.read_bytes())) == state:
        return
    VAULT.mkdir(exist_ok=True)
    STATE_BLOB.write_bytes(encrypt(pw, json.dumps(state, ensure_ascii=False).encode()))


# --------------------------------------------------------------- commands


def cmd_encrypt(pw, prune):
    VAULT.mkdir(exist_ok=True)
    folders = local_folders()
    if not folders:
        sys.exit("No hay carpetas de posts en local.")
    keep = set()
    added = updated = 0
    for folder in folders:
        name = blob_name(pw, folder.name)
        keep.add(name)
        target = VAULT / name
        data = zip_folder(folder)
        if target.exists():
            if decrypt(pw, target.read_bytes()) == data:
                continue
            updated += 1
            print(f"actualizado  {folder.name}")
        else:
            added += 1
            print(f"nuevo        {folder.name}")
        target.write_bytes(encrypt(pw, data))

    stale = [p for p in VAULT.glob("*.bin") if p != STATE_BLOB and p.name not in keep]
    if prune:
        for p in stale:
            p.unlink()
    print(f"{added} nuevos, {updated} actualizados, {len(folders) - added - updated} sin cambios"
          + (f", {len(stale)} eliminados" if prune else f", {len(stale)} en el vault sin carpeta local (usa --prune para borrarlos)" if stale else ""))


def cmd_decrypt(pw):
    for folder_name, files in load_posts(pw):
        target = ROOT / folder_name
        target.mkdir(exist_ok=True)
        for file_name, data in files.items():
            (target / file_name).write_bytes(data)
        print(f"restaurado   {folder_name}")


def cmd_list(pw):
    posts = load_posts(pw)
    state = load_state(pw)
    for folder_name, files in posts:
        marker = "->" if folder_name == state.get("next") else "  "
        print(f"{marker} {folder_name}  ({', '.join(sorted(files))})")
    print(f"{len(posts)} posts. Siguiente: {state.get('next')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    enc = sub.add_parser("encrypt", help="cifra las carpetas locales en vault/")
    enc.add_argument("--prune", action="store_true", help="borra del vault los posts que ya no existen en local")
    sub.add_parser("decrypt", help="restaura las carpetas desde vault/")
    sub.add_parser("list", help="lista el contenido del vault")
    args = parser.parse_args()

    pw = password()
    if args.cmd == "encrypt":
        cmd_encrypt(pw, args.prune)
    elif args.cmd == "decrypt":
        cmd_decrypt(pw)
    else:
        cmd_list(pw)


if __name__ == "__main__":
    main()
