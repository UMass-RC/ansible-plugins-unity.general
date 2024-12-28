class ModuleDocFragment(object):
    DOCUMENTATION = r"""
      requirements:
        - linux or macos
        - "for linux: /dev/shm/ must exist"
        - "for macOS: L(TmpDisk,https://github.com/imothee/tmpdisk)"
        - "for macOS: ~/tmpdisk/shm must be created with tmpdisk"
      options:
        cache_timeout_seconds:
          description: |
            cache will be truncated if its mtime is older than this.
            negative number means never timeout.
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
        ramdisk_cache_path:
          description: |
            path to the ramdisk cache for cached lookups.
            defaults to /dev/shm/ on linux and ~/.tmpdisk/shm on macOS. see requirements.
          type: str
          ini:
            - section: ramdisk_cache
              key: path
          env:
            - name: RAMDISK_CACHE_PATH
      extends_documentation_fragment:
      - unity.general.ramdisk_cache_path
    """
