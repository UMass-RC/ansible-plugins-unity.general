class ModuleDocFragment(object):
    DOCUMENTATION = r"""
      requirements:
        - linux or macos
        - "for linux: /dev/shm/ must exist"
        - "for macos: L(TmpDisk,https://github.com/imothee/tmpdisk)"
        - "for macos: ~/tmpdisk/shm must be created with tmpdisk"
      options:
        cache_timeout_seconds:
          description: cache will be truncated if its mtime is older than this
          type: int
          default: 3600
          ini:
            - section: ramdisk_cache
              key: timeout_seconds
          env:
            - name: RAMDISK_CACHE_TIMEOUT_SECONDS
        enable_cache:
          description: enable ramdisk cache
          type: bool
          default: true
          ini:
            - section: ramdisk_cache
              key: enable
          env:
            - name: RAMDISK_CACHE_ENABLE
        cache_path:
          description: ignore /dev/shm or ~/tmpdisk/shm and create tempfiles in a different directory
          type: str
          # since I want to be able to access this value without actually invoking the plugin
          # properly, I use os.environ instead of get_option() for this option
          # so it can't be configured via ini
          env:
            - name: RAMDISK_CACHE_PATH
    """