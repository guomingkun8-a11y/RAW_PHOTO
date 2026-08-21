"""Make stdlib TLS work in builds that ship no OpenSSL CA store.

A PyInstaller bundle carries its own OpenSSL, whose compiled-in CA paths point
at the build machine and do not exist on the user's. Anything going through
``ssl.create_default_context`` — urllib, websockets, the Feishu SDK — then fails
with CERTIFICATE_VERIFY_FAILED, while ``requests`` keeps working because it
passes certifi's bundle explicitly. Exporting the bundle through OpenSSL's env
vars fixes every stdlib caller in one place.
"""

import os
import ssl
from typing import Optional


def _store_is_usable() -> bool:
    paths = ssl.get_default_verify_paths()
    if paths.cafile and os.path.exists(paths.cafile):
        return True
    return bool(paths.capath and os.path.isdir(paths.capath))


def ensure_ca_bundle() -> Optional[str]:
    """Fall back to certifi's bundle when OpenSSL has no usable CA store.

    Returns the bundle that was installed, or None when nothing was changed.
    Must run before the first TLS connection: ``create_default_context`` reads
    these variables each time it loads the default certificates, but contexts
    built earlier keep the store they were created with.
    """
    # An explicit store (a corporate CA, say) is the caller's decision to keep.
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return None
    if _store_is_usable():
        return None
    try:
        import certifi
    except ImportError:
        return None
    bundle = certifi.where()
    if not os.path.exists(bundle):
        return None
    os.environ["SSL_CERT_FILE"] = bundle
    return bundle
