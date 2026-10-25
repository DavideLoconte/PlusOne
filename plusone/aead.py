"""AES-256-GCM, for the camera's and the microphone's datagrams (media.py),
through OpenSSL's libcrypto: there already for the PC's certificate (openssl),
in the GNOME runtime too, and nothing more to install."""
import ctypes
import ctypes.util

TAG = 16
_GCM_SET_IVLEN, _GCM_GET_TAG, _GCM_SET_TAG = 0x9, 0x10, 0x11

_lib = ctypes.CDLL(ctypes.util.find_library("crypto") or "libcrypto.so.3")
_lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
_lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
_lib.EVP_aes_256_gcm.restype = ctypes.c_void_p
for _name in ("EVP_EncryptInit_ex", "EVP_DecryptInit_ex"):
    getattr(_lib, _name).argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p,
                                     ctypes.c_char_p]
for _name in ("EVP_EncryptUpdate", "EVP_DecryptUpdate"):
    getattr(_lib, _name).argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                     ctypes.c_char_p, ctypes.c_int]
for _name in ("EVP_EncryptFinal_ex", "EVP_DecryptFinal_ex"):
    getattr(_lib, _name).argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
_lib.EVP_CIPHER_CTX_ctrl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]


class Key:
    """One AES-256-GCM key (32 bytes); nonces of 12 bytes, never used twice
    with it (the sender's sequence numbers make sure)."""

    def __init__(self, key):
        if len(key) != 32:
            raise ValueError("an AES-256 key is 32 bytes")
        self.key = bytes(key)

    def seal(self, nonce, data, aad=b""):
        """data encrypted, its tag after it."""
        ctx = _lib.EVP_CIPHER_CTX_new()
        try:
            out = ctypes.create_string_buffer(len(data) + TAG)
            n = ctypes.c_int()
            ok = (_lib.EVP_EncryptInit_ex(ctx, _lib.EVP_aes_256_gcm(), None, None, None)
                  and _lib.EVP_CIPHER_CTX_ctrl(ctx, _GCM_SET_IVLEN, len(nonce), None)
                  and _lib.EVP_EncryptInit_ex(ctx, None, None, self.key, bytes(nonce))
                  and (not aad or _lib.EVP_EncryptUpdate(ctx, None, ctypes.byref(n), bytes(aad), len(aad)))
                  and _lib.EVP_EncryptUpdate(ctx, out, ctypes.byref(n), bytes(data), len(data)))
            written = n.value
            ok = ok and _lib.EVP_EncryptFinal_ex(ctx, ctypes.byref(out, written), ctypes.byref(n))
            tag = ctypes.create_string_buffer(TAG)
            ok = ok and _lib.EVP_CIPHER_CTX_ctrl(ctx, _GCM_GET_TAG, TAG, tag)
            if not ok:
                raise ValueError("AES-GCM failed")
            return out.raw[:written] + tag.raw
        finally:
            _lib.EVP_CIPHER_CTX_free(ctx)

    def open(self, nonce, sealed, aad=b""):
        """The data, or None if it is not what was sealed with this key, nonce and aad."""
        if len(sealed) < TAG:
            return None
        data, tag = bytes(sealed[:-TAG]), bytes(sealed[-TAG:])
        ctx = _lib.EVP_CIPHER_CTX_new()
        try:
            out = ctypes.create_string_buffer(len(data) + 1)
            n = ctypes.c_int()
            ok = (_lib.EVP_DecryptInit_ex(ctx, _lib.EVP_aes_256_gcm(), None, None, None)
                  and _lib.EVP_CIPHER_CTX_ctrl(ctx, _GCM_SET_IVLEN, len(nonce), None)
                  and _lib.EVP_DecryptInit_ex(ctx, None, None, self.key, bytes(nonce))
                  and (not aad or _lib.EVP_DecryptUpdate(ctx, None, ctypes.byref(n), bytes(aad), len(aad)))
                  and _lib.EVP_DecryptUpdate(ctx, out, ctypes.byref(n), data, len(data)))
            written = n.value
            ok = ok and _lib.EVP_CIPHER_CTX_ctrl(ctx, _GCM_SET_TAG, TAG, ctypes.c_char_p(tag))
            ok = ok and _lib.EVP_DecryptFinal_ex(ctx, ctypes.byref(out, written), ctypes.byref(n))
            return out.raw[:written] if ok else None
        finally:
            _lib.EVP_CIPHER_CTX_free(ctx)
