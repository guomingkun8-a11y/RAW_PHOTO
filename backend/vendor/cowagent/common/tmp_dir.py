from common.state_dir import tmp_dir


class TmpDir(object):
    """Temporary directory for transient artifacts (e.g. synthesized voice).

    Resolves under the routed Agent's workspace rather than a CWD-relative
    ``./tmp``, which is unreliable when CWD is not stable.
    """

    def __init__(self):
        self.tmpFilePath = str(tmp_dir())

    def path(self):
        return str(self.tmpFilePath) + "/"
