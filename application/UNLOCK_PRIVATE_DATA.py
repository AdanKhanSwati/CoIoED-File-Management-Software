"""Unlock CoIoED's already-encrypted master payload without PowerShell scripts.

Windows runtime: Python 3.11+ stdlib, native Windows CNG (bcrypt.dll).
The key is never stored in this source or in command-line arguments.
Existing data is never overwritten. Confidential data should be extracted only
on an institutionally authorized, encrypted disk.
"""
from __future__ import annotations

import ctypes
import getpass
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
PAYLOAD = HERE / 'private_data.coioed'
DATA_DIR = HERE / 'data'
EXPECTED = ('coioed.db', 'private_original_master.xlsx', 'DATA_VALIDATION.json')


def cng_decrypt_aes_cbc(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """Decrypt AES-256-CBC/PKCS7 using Windows CNG (bcrypt.dll), not external modules."""
    if os.name != 'nt':
        raise RuntimeError('Windows Cryptography API is required on the target computer.')
    if len(key) != 32 or len(iv) != 16 or len(ciphertext) % 16:
        raise ValueError('Invalid AES parameters or ciphertext size.')
    BCRYPT_BLOCK_PADDING = 0x00000001
    # NTSTATUS is signed 32 bits on Windows.
    STATUS = ctypes.c_long
    HANDLE = ctypes.c_void_p
    ULONG = ctypes.c_ulong
    lib = ctypes.WinDLL('bcrypt.dll')
    open_alg = lib.BCryptOpenAlgorithmProvider
    open_alg.argtypes = [ctypes.POINTER(HANDLE), ctypes.c_wchar_p, ctypes.c_wchar_p, ULONG]
    open_alg.restype = STATUS
    setprop = lib.BCryptSetProperty
    setprop.argtypes = [HANDLE, ctypes.c_wchar_p, ctypes.c_void_p, ULONG, ULONG]
    setprop.restype = STATUS
    getprop = lib.BCryptGetProperty
    getprop.argtypes = [HANDLE, ctypes.c_wchar_p, ctypes.c_void_p, ULONG, ctypes.POINTER(ULONG), ULONG]
    getprop.restype = STATUS
    make_key = lib.BCryptGenerateSymmetricKey
    make_key.argtypes = [HANDLE, ctypes.POINTER(HANDLE), ctypes.c_void_p, ULONG, ctypes.c_void_p, ULONG, ULONG]
    make_key.restype = STATUS
    decrypt = lib.BCryptDecrypt
    decrypt.argtypes = [HANDLE, ctypes.c_void_p, ULONG, ctypes.c_void_p, ctypes.c_void_p, ULONG, ctypes.c_void_p, ULONG, ctypes.POINTER(ULONG), ULONG]
    decrypt.restype = STATUS
    destroy_key = lib.BCryptDestroyKey
    destroy_key.argtypes = [HANDLE]
    destroy_key.restype = STATUS
    close_alg = lib.BCryptCloseAlgorithmProvider
    close_alg.argtypes = [HANDLE, ULONG]
    close_alg.restype = STATUS

    def check(code: int, operation: str) -> None:
        if code != 0:
            raise OSError(f'{operation} failed (Windows NTSTATUS 0x{code & 0xFFFFFFFF:08X})')

    algorithm = HANDLE()
    aes_key = HANDLE()
    check(open_alg(ctypes.byref(algorithm), 'AES', None, 0), 'Initialize Windows AES')
    try:
        chaining_mode = ctypes.create_unicode_buffer('ChainingModeCBC')
        check(setprop(algorithm, 'ChainingMode', chaining_mode,
                      ctypes.sizeof(chaining_mode), 0), 'Set AES-CBC mode')
        object_size = ULONG()
        returned = ULONG()
        check(getprop(algorithm, 'ObjectLength', ctypes.byref(object_size),
                      ctypes.sizeof(object_size), ctypes.byref(returned), 0), 'Get AES key-object size')
        key_buffer = ctypes.create_string_buffer(key)
        key_object = ctypes.create_string_buffer(object_size.value)
        check(make_key(algorithm, ctypes.byref(aes_key), key_object,
                       object_size.value, key_buffer, len(key), 0), 'Create AES key')
        try:
            # BCryptDecrypt updates the IV in-place. The first/only call receives a copy.
            iv_buffer = ctypes.create_string_buffer(iv)
            input_buffer = ctypes.create_string_buffer(ciphertext)
            output_buffer = ctypes.create_string_buffer(len(ciphertext))
            output_size = ULONG()
            check(decrypt(aes_key, input_buffer, len(ciphertext), None,
                          iv_buffer, len(iv), output_buffer, len(ciphertext),
                          ctypes.byref(output_size), BCRYPT_BLOCK_PADDING), 'Decrypt private payload')
            if output_size.value <= 0 or output_size.value > len(ciphertext):
                raise ValueError('Invalid decrypted payload length.')
            return output_buffer.raw[:output_size.value]
        finally:
            destroy_key(aes_key)
    finally:
        close_alg(algorithm, 0)


def decrypt_payload(raw: bytes, secret: bytes) -> bytes:
    if len(raw) < 72 or raw[:8] != b'COIOED1\x00':
        raise ValueError('The encrypted case-data payload is invalid or truncated.')
    iv, tag, ciphertext = raw[8:24], raw[24:56], raw[56:]
    enc_key = hashlib.sha256(b'COIOED-ENC-V1' + secret).digest()
    mac_key = hashlib.sha256(b'COIOED-MAC-V1' + secret).digest()
    actual = hmac.new(mac_key, raw[:24] + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(actual, tag):
        raise ValueError('Wrong unlock key or damaged encrypted case-data file. No records were installed.')
    return cng_decrypt_aes_cbc(ciphertext, enc_key, iv)


def validate_staging(staging: Path) -> dict:
    meta = json.loads((staging / 'DATA_VALIDATION.json').read_text(encoding='utf-8'))
    expected_hash = meta.get('original_workbook_sha256')
    with (staging / 'private_original_master.xlsx').open('rb') as fin:
        hasher = hashlib.file_digest(fin, 'sha256')
    if hasher.hexdigest().lower() != str(expected_hash).lower():
        raise ValueError('Original workbook checksum mismatch; nothing was installed.')
    con = sqlite3.connect(str(staging / 'coioed.db'))
    try:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed.')
        n = con.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
        main = con.execute('SELECT COUNT(*) FROM master_source_rows WHERE sheet_index=0').fetchone()[0]
        references = con.execute('SELECT COUNT(*) FROM master_source_rows WHERE sheet_index>0').fetchone()[0]
        if (n, main, references) != (11005, 11005, 2432):
            raise ValueError('The imported case-record counts do not match the expected manifest.')
        if con.execute('SELECT COUNT(*) FROM users').fetchone()[0] != 0:
            raise ValueError('Preloaded user accounts are not permitted.')
    finally:
        con.close()
    return meta


def install() -> int:
    if (DATA_DIR / 'coioed.db').exists():
        print('The case database is already installed. No changes made.')
        return 0
    if not PAYLOAD.is_file():
        print('ERROR: private_data.coioed was not found next to this launcher.')
        return 1
    print('\nCoIoED — encrypted comprehensive register installation')
    print('The unlocked database will be stored in a normal SQLite file.')
    print('Only continue on an IT-approved Windows computer with approved disk encryption.')
    try:
        confirm = input('Type ENCRYPTED to confirm approved encrypted storage: ').strip()
        if confirm != 'ENCRYPTED':
            print('Installation cancelled. No files changed.')
            return 1
        key_hex = getpass.getpass('Enter private unlock key (hidden): ').strip()
        if len(key_hex) != 64:
            raise ValueError('The private key must contain 64 hexadecimal characters.')
        secret = bytes.fromhex(key_hex)
        key_hex = ''
        raw = PAYLOAD.read_bytes()
        plain = decrypt_payload(raw, secret)
        # No arbitrary ZIP extraction; extract only these three fixed filenames.
        with tempfile.TemporaryDirectory(prefix='_coioed_staging_', dir=HERE) as temp:
            stage = Path(temp)
            with zipfile.ZipFile(io.BytesIO(plain)) as z:
                names = set(z.namelist())
                if names != set(EXPECTED):
                    raise ValueError('Unexpected files in private payload; refusing to install.')
                for filename in EXPECTED:
                    source = z.getinfo(filename)
                    if source.is_dir() or source.file_size > 120_000_000:
                        raise ValueError('Invalid payload entry.')
                    with z.open(source) as fin, (stage / filename).open('wb') as fout:
                        import shutil
                        shutil.copyfileobj(fin, fout)
            meta = validate_staging(stage)
            DATA_DIR.mkdir(exist_ok=True)
            if any(DATA_DIR.iterdir()):
                raise ValueError('The data folder is not empty. Refusing to overwrite existing information.')
            transferred = []
            try:
                for filename in EXPECTED:
                    dest = DATA_DIR / filename
                    (stage / filename).replace(dest)
                    transferred.append(dest)
            except Exception:
                for target in transferred:
                    try:
                        target.replace(stage / target.name)
                    except Exception:
                        pass
                raise
        print('Data installation completed. Verified: 11,005 case entries and 2,432 reference rows.')
        print('Original workbook checksum validated. No default administrator account.')
        print('WARNING: the working SQLite database is not application-encrypted.')
        return 0
    except (ValueError, OSError, zipfile.BadZipFile, sqlite3.DatabaseError, EOFError, KeyboardInterrupt) as exc:
        print('ERROR:', str(exc))
        print('The encrypted payload is unchanged; do not share the private key or case data.')
        return 1


if __name__ == '__main__':
    sys.exit(install())