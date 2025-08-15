# fmt: off
import json
# curl https://raw.githubusercontent.com/archspec/archspec-json/master/cpu/microarchitectures.json | jq '.microarchitectures |= with_entries(.value |= del(.compilers))'
# duplicating 3rd party deps into your collections is unfortunately best practice
# https://forum.ansible.com/t/how-to-handle-module-dependencies-during-development/4563
UARCH_DB = json.loads(
"""
{
  "microarchitectures": {
    "x86": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "i686": {
      "from": [
        "x86"
      ],
      "vendor": "GenuineIntel",
      "features": []
    },
    "pentium2": {
      "from": [
        "i686"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx"
      ]
    },
    "pentium3": {
      "from": [
        "pentium2"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse"
      ]
    },
    "pentium4": {
      "from": [
        "pentium3"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2"
      ]
    },
    "prescott": {
      "from": [
        "pentium4"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "sse3"
      ]
    },
    "x86_64": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "x86_64_v2": {
      "from": [
        "x86_64"
      ],
      "vendor": "generic",
      "features": [
        "cx16",
        "lahf_lm",
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt"
      ]
    },
    "x86_64_v3": {
      "from": [
        "x86_64_v2"
      ],
      "vendor": "generic",
      "features": [
        "cx16",
        "lahf_lm",
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "avx",
        "avx2",
        "bmi1",
        "bmi2",
        "f16c",
        "fma",
        "abm",
        "movbe",
        "xsave"
      ]
    },
    "x86_64_v4": {
      "from": [
        "x86_64_v3"
      ],
      "vendor": "generic",
      "features": [
        "cx16",
        "lahf_lm",
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "avx",
        "avx2",
        "bmi1",
        "bmi2",
        "f16c",
        "fma",
        "abm",
        "movbe",
        "xsave",
        "avx512f",
        "avx512bw",
        "avx512cd",
        "avx512dq",
        "avx512vl"
      ]
    },
    "nocona": {
      "from": [
        "x86_64"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "sse3"
      ]
    },
    "core2": {
      "from": [
        "nocona"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3"
      ]
    },
    "nehalem": {
      "from": [
        "core2",
        "x86_64_v2"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt"
      ]
    },
    "westmere": {
      "from": [
        "nehalem"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq"
      ]
    },
    "sandybridge": {
      "from": [
        "westmere"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx"
      ]
    },
    "ivybridge": {
      "from": [
        "sandybridge"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c"
      ]
    },
    "haswell": {
      "from": [
        "ivybridge",
        "x86_64_v3"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c",
        "movbe",
        "fma",
        "avx2",
        "bmi1",
        "bmi2"
      ]
    },
    "broadwell": {
      "from": [
        "haswell"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c",
        "movbe",
        "fma",
        "avx2",
        "bmi1",
        "bmi2",
        "rdseed",
        "adx"
      ]
    },
    "skylake": {
      "from": [
        "broadwell"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c",
        "movbe",
        "fma",
        "avx2",
        "bmi1",
        "bmi2",
        "rdseed",
        "adx",
        "clflushopt",
        "xsavec",
        "xsaveopt"
      ]
    },
    "mic_knl": {
      "from": [
        "broadwell"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c",
        "movbe",
        "avx2",
        "fma",
        "avx2",
        "bmi1",
        "bmi2",
        "rdseed",
        "adx",
        "avx512f",
        "avx512pf",
        "avx512er",
        "avx512cd"
      ]
    },
    "skylake_avx512": {
      "from": [
        "skylake",
        "x86_64_v4"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c",
        "movbe",
        "fma",
        "avx2",
        "bmi1",
        "bmi2",
        "rdseed",
        "adx",
        "clflushopt",
        "xsavec",
        "xsaveopt",
        "avx512f",
        "clwb",
        "avx512vl",
        "avx512bw",
        "avx512dq",
        "avx512cd"
      ]
    },
    "cannonlake": {
      "from": [
        "skylake"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c",
        "movbe",
        "fma",
        "avx2",
        "bmi1",
        "bmi2",
        "rdseed",
        "adx",
        "clflushopt",
        "xsavec",
        "xsaveopt",
        "avx512f",
        "avx512vl",
        "avx512bw",
        "avx512dq",
        "avx512cd",
        "avx512vbmi",
        "avx512ifma",
        "sha"
      ]
    },
    "cascadelake": {
      "from": [
        "skylake_avx512"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c",
        "movbe",
        "fma",
        "avx2",
        "bmi1",
        "bmi2",
        "rdseed",
        "adx",
        "clflushopt",
        "xsavec",
        "xsaveopt",
        "avx512f",
        "clwb",
        "avx512vl",
        "avx512bw",
        "avx512dq",
        "avx512cd",
        "avx512_vnni"
      ]
    },
    "icelake": {
      "from": [
        "cascadelake",
        "cannonlake"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c",
        "movbe",
        "fma",
        "avx2",
        "bmi1",
        "bmi2",
        "rdseed",
        "adx",
        "clflushopt",
        "xsavec",
        "xsaveopt",
        "avx512f",
        "avx512vl",
        "avx512bw",
        "avx512dq",
        "avx512cd",
        "avx512vbmi",
        "avx512ifma",
        "sha_ni",
        "clwb",
        "rdpid",
        "gfni",
        "avx512_vbmi2",
        "avx512_vpopcntdq",
        "avx512_bitalg",
        "avx512_vnni",
        "vpclmulqdq",
        "vaes"
      ]
    },
    "sapphirerapids": {
      "from": [
        "icelake"
      ],
      "vendor": "GenuineIntel",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "popcnt",
        "aes",
        "pclmulqdq",
        "avx",
        "rdrand",
        "f16c",
        "movbe",
        "fma",
        "avx2",
        "bmi1",
        "bmi2",
        "rdseed",
        "adx",
        "clflushopt",
        "xsavec",
        "xsaveopt",
        "avx512f",
        "avx512vl",
        "avx512bw",
        "avx512dq",
        "avx512cd",
        "avx512vbmi",
        "avx512ifma",
        "sha_ni",
        "clwb",
        "rdpid",
        "gfni",
        "avx512_vbmi2",
        "avx512_vpopcntdq",
        "avx512_bitalg",
        "avx512_vnni",
        "vpclmulqdq",
        "vaes",
        "avx512_bf16",
        "cldemote",
        "movdir64b",
        "movdiri",
        "serialize",
        "waitpkg",
        "amx_bf16",
        "amx_tile",
        "amx_int8"
      ]
    },
    "k10": {
      "from": [
        "x86_64"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "sse4a",
        "abm",
        "cx16",
        "3dnow",
        "3dnowext"
      ]
    },
    "bulldozer": {
      "from": [
        "x86_64_v2"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "sse4a",
        "abm",
        "avx",
        "xop",
        "fma4",
        "aes",
        "pclmulqdq",
        "cx16",
        "ssse3",
        "sse4_1",
        "sse4_2"
      ]
    },
    "piledriver": {
      "from": [
        "bulldozer"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "sse4a",
        "abm",
        "avx",
        "xop",
        "fma4",
        "aes",
        "pclmulqdq",
        "cx16",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "bmi1",
        "f16c",
        "fma",
        "tbm"
      ]
    },
    "steamroller": {
      "from": [
        "piledriver"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "sse4a",
        "abm",
        "avx",
        "xop",
        "fma4",
        "aes",
        "pclmulqdq",
        "cx16",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "bmi1",
        "f16c",
        "fma",
        "fsgsbase",
        "tbm"
      ]
    },
    "excavator": {
      "from": [
        "steamroller",
        "x86_64_v3"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "mmx",
        "sse",
        "sse2",
        "sse4a",
        "abm",
        "avx",
        "xop",
        "fma4",
        "aes",
        "pclmulqdq",
        "cx16",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "bmi1",
        "f16c",
        "fma",
        "fsgsbase",
        "bmi2",
        "avx2",
        "movbe",
        "tbm"
      ]
    },
    "zen": {
      "from": [
        "x86_64_v3"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "bmi1",
        "bmi2",
        "f16c",
        "fma",
        "fsgsbase",
        "avx",
        "avx2",
        "rdseed",
        "clzero",
        "aes",
        "pclmulqdq",
        "cx16",
        "movbe",
        "mmx",
        "sse",
        "sse2",
        "sse4a",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "abm",
        "xsavec",
        "xsaveopt",
        "clflushopt",
        "popcnt"
      ]
    },
    "zen2": {
      "from": [
        "zen"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "bmi1",
        "bmi2",
        "f16c",
        "fma",
        "fsgsbase",
        "avx",
        "avx2",
        "rdseed",
        "clzero",
        "aes",
        "pclmulqdq",
        "cx16",
        "movbe",
        "mmx",
        "sse",
        "sse2",
        "sse4a",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "abm",
        "xsavec",
        "xsaveopt",
        "clflushopt",
        "popcnt",
        "clwb"
      ]
    },
    "zen3": {
      "from": [
        "zen2"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "bmi1",
        "bmi2",
        "f16c",
        "fma",
        "fsgsbase",
        "avx",
        "avx2",
        "rdseed",
        "clzero",
        "aes",
        "pclmulqdq",
        "cx16",
        "movbe",
        "mmx",
        "sse",
        "sse2",
        "sse4a",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "abm",
        "xsavec",
        "xsaveopt",
        "clflushopt",
        "popcnt",
        "clwb",
        "vaes",
        "vpclmulqdq",
        "pku"
      ]
    },
    "zen4": {
      "from": [
        "zen3",
        "x86_64_v4"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "bmi1",
        "bmi2",
        "f16c",
        "fma",
        "fsgsbase",
        "avx",
        "avx2",
        "rdseed",
        "clzero",
        "aes",
        "pclmulqdq",
        "cx16",
        "movbe",
        "mmx",
        "sse",
        "sse2",
        "sse4a",
        "ssse3",
        "sse4_1",
        "sse4_2",
        "abm",
        "xsavec",
        "xsaveopt",
        "clflushopt",
        "popcnt",
        "clwb",
        "vaes",
        "vpclmulqdq",
        "pku",
        "gfni",
        "flush_l1d",
        "avx512f",
        "avx512dq",
        "avx512ifma",
        "avx512cd",
        "avx512bw",
        "avx512vl",
        "avx512_bf16",
        "avx512vbmi",
        "avx512_vbmi2",
        "avx512_vnni",
        "avx512_bitalg",
        "avx512_vpopcntdq"
      ]
    },
    "zen5": {
      "from": [
        "zen4"
      ],
      "vendor": "AuthenticAMD",
      "features": [
        "abm",
        "aes",
        "avx",
        "avx2",
        "avx512_bf16",
        "avx512_bitalg",
        "avx512bw",
        "avx512cd",
        "avx512dq",
        "avx512f",
        "avx512ifma",
        "avx512vbmi",
        "avx512_vbmi2",
        "avx512vl",
        "avx512_vnni",
        "avx512_vp2intersect",
        "avx512_vpopcntdq",
        "avx_vnni",
        "bmi1",
        "bmi2",
        "clflushopt",
        "clwb",
        "clzero",
        "cppc",
        "cx16",
        "f16c",
        "flush_l1d",
        "fma",
        "fsgsbase",
        "gfni",
        "ibrs_enhanced",
        "mmx",
        "movbe",
        "movdir64b",
        "movdiri",
        "pclmulqdq",
        "popcnt",
        "rdseed",
        "sse",
        "sse2",
        "sse4_1",
        "sse4_2",
        "sse4a",
        "ssse3",
        "tsc_adjust",
        "vaes",
        "vpclmulqdq",
        "xsavec",
        "xsaveopt"
      ]
    },
    "ppc64": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "power7": {
      "from": [
        "ppc64"
      ],
      "vendor": "IBM",
      "generation": 7,
      "features": []
    },
    "power8": {
      "from": [
        "power7"
      ],
      "vendor": "IBM",
      "generation": 8,
      "features": []
    },
    "power9": {
      "from": [
        "power8"
      ],
      "vendor": "IBM",
      "generation": 9,
      "features": []
    },
    "power10": {
      "from": [
        "power9"
      ],
      "vendor": "IBM",
      "generation": 10,
      "features": []
    },
    "ppc64le": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "power8le": {
      "from": [
        "ppc64le"
      ],
      "vendor": "IBM",
      "generation": 8,
      "features": []
    },
    "power9le": {
      "from": [
        "power8le"
      ],
      "vendor": "IBM",
      "generation": 9,
      "features": []
    },
    "power10le": {
      "from": [
        "power9le"
      ],
      "vendor": "IBM",
      "generation": 10,
      "features": []
    },
    "aarch64": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "armv8.1a": {
      "from": [
        "aarch64"
      ],
      "vendor": "generic",
      "features": []
    },
    "armv8.2a": {
      "from": [
        "armv8.1a"
      ],
      "vendor": "generic",
      "features": []
    },
    "armv8.3a": {
      "from": [
        "armv8.2a"
      ],
      "vendor": "generic",
      "features": []
    },
    "armv8.4a": {
      "from": [
        "armv8.3a"
      ],
      "vendor": "generic",
      "features": []
    },
    "armv8.5a": {
      "from": [
        "armv8.4a"
      ],
      "vendor": "generic",
      "features": []
    },
    "armv9.0a": {
      "from": [
        "armv8.5a"
      ],
      "vendor": "generic",
      "features": []
    },
    "thunderx2": {
      "from": [
        "armv8.1a"
      ],
      "vendor": "Cavium",
      "features": [
        "fp",
        "asimd",
        "evtstrm",
        "aes",
        "pmull",
        "sha1",
        "sha2",
        "crc32",
        "atomics",
        "cpuid",
        "asimdrdm"
      ],
      "cpupart": "0x0af"
    },
    "a64fx": {
      "from": [
        "armv8.2a"
      ],
      "vendor": "Fujitsu",
      "features": [
        "fp",
        "asimd",
        "evtstrm",
        "sha1",
        "sha2",
        "crc32",
        "atomics",
        "cpuid",
        "asimdrdm",
        "fphp",
        "asimdhp",
        "fcma",
        "dcpop",
        "sve"
      ],
      "cpupart": "0x001"
    },
    "cortex_a72": {
      "from": [
        "aarch64"
      ],
      "vendor": "ARM",
      "features": [
        "fp",
        "asimd",
        "evtstrm",
        "aes",
        "pmull",
        "sha1",
        "sha2",
        "crc32",
        "cpuid"
      ],
      "cpupart": "0xd08"
    },
    "neoverse_n1": {
      "from": [
        "cortex_a72",
        "armv8.2a"
      ],
      "vendor": "ARM",
      "features": [
        "fp",
        "asimd",
        "evtstrm",
        "aes",
        "pmull",
        "sha1",
        "sha2",
        "crc32",
        "atomics",
        "fphp",
        "asimdhp",
        "cpuid",
        "asimdrdm",
        "lrcpc",
        "dcpop",
        "asimddp"
      ],
      "cpupart": "0xd0c"
    },
    "neoverse_v1": {
      "from": [
        "neoverse_n1",
        "armv8.4a"
      ],
      "vendor": "ARM",
      "features": [
        "fp",
        "asimd",
        "evtstrm",
        "aes",
        "pmull",
        "sha1",
        "sha2",
        "crc32",
        "atomics",
        "fphp",
        "asimdhp",
        "cpuid",
        "asimdrdm",
        "jscvt",
        "fcma",
        "lrcpc",
        "dcpop",
        "sha3",
        "asimddp",
        "sha512",
        "sve",
        "asimdfhm",
        "dit",
        "uscat",
        "ilrcpc",
        "flagm",
        "dcpodp",
        "svei8mm",
        "svebf16",
        "i8mm",
        "bf16",
        "dgh",
        "rng"
      ],
      "cpupart": "0xd40"
    },
    "neoverse_v2": {
      "from": [
        "neoverse_n1",
        "armv9.0a"
      ],
      "vendor": "ARM",
      "features": [
        "fp",
        "asimd",
        "evtstrm",
        "aes",
        "pmull",
        "sha1",
        "sha2",
        "crc32",
        "atomics",
        "fphp",
        "asimdhp",
        "cpuid",
        "asimdrdm",
        "jscvt",
        "fcma",
        "lrcpc",
        "dcpop",
        "sha3",
        "asimddp",
        "sha512",
        "sve",
        "asimdfhm",
        "uscat",
        "ilrcpc",
        "flagm",
        "sb",
        "dcpodp",
        "sve2",
        "flagm2",
        "frint",
        "svei8mm",
        "svebf16",
        "i8mm",
        "bf16"
      ],
      "cpupart": "0xd4f"
    },
    "neoverse_n2": {
      "from": [
        "neoverse_n1",
        "armv9.0a"
      ],
      "vendor": "ARM",
      "features": [
        "fp",
        "asimd",
        "evtstrm",
        "aes",
        "pmull",
        "sha1",
        "sha2",
        "crc32",
        "atomics",
        "fphp",
        "asimdhp",
        "cpuid",
        "asimdrdm",
        "jscvt",
        "fcma",
        "lrcpc",
        "dcpop",
        "sha3",
        "asimddp",
        "sha512",
        "sve",
        "asimdfhm",
        "uscat",
        "ilrcpc",
        "flagm",
        "sb",
        "dcpodp",
        "sve2",
        "flagm2",
        "frint",
        "svei8mm",
        "svebf16",
        "i8mm",
        "bf16"
      ],
      "cpupart": "0xd49"
    },
    "m1": {
      "from": [
        "armv8.4a"
      ],
      "vendor": "Apple",
      "features": [
        "fp",
        "asimd",
        "evtstrm",
        "aes",
        "pmull",
        "sha1",
        "sha2",
        "crc32",
        "atomics",
        "fphp",
        "asimdhp",
        "cpuid",
        "asimdrdm",
        "jscvt",
        "fcma",
        "lrcpc",
        "dcpop",
        "sha3",
        "asimddp",
        "sha512",
        "asimdfhm",
        "dit",
        "uscat",
        "ilrcpc",
        "flagm",
        "ssbs",
        "sb",
        "paca",
        "pacg",
        "dcpodp",
        "flagm2",
        "frint"
      ],
      "cpupart": "0x022"
    },
    "m2": {
      "from": [
        "m1",
        "armv8.5a"
      ],
      "vendor": "Apple",
      "features": [
        "fp",
        "asimd",
        "evtstrm",
        "aes",
        "pmull",
        "sha1",
        "sha2",
        "crc32",
        "atomics",
        "fphp",
        "asimdhp",
        "cpuid",
        "asimdrdm",
        "jscvt",
        "fcma",
        "lrcpc",
        "dcpop",
        "sha3",
        "asimddp",
        "sha512",
        "asimdfhm",
        "dit",
        "uscat",
        "ilrcpc",
        "flagm",
        "ssbs",
        "sb",
        "paca",
        "pacg",
        "dcpodp",
        "flagm2",
        "frint",
        "ecv",
        "bf16",
        "i8mm",
        "bti"
      ],
      "cpupart": "0x032"
    },
    "arm": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "ppc": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "ppcle": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "sparc": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "sparc64": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "riscv64": {
      "from": [],
      "vendor": "generic",
      "features": []
    },
    "u74mc": {
      "from": [
        "riscv64"
      ],
      "vendor": "SiFive",
      "features": []
    }
  },
  "feature_aliases": {
    "sse3": {
      "reason": "ssse3 is a superset of sse3 and might be the only one listed",
      "any_of": [
        "ssse3"
      ]
    },
    "avx512": {
      "reason": "avx512 indicates generic support for any of the avx512 instruction sets",
      "any_of": [
        "avx512f",
        "avx512vl",
        "avx512bw",
        "avx512dq",
        "avx512cd"
      ]
    },
    "altivec": {
      "reason": "altivec is supported by Power PC architectures, but might not be listed in features",
      "families": [
        "ppc64le",
        "ppc64"
      ]
    },
    "vsx": {
      "reason": "VSX alitvec extensions are supported by PowerISA from v2.06 (Power7+), but might not be listed in features",
      "families": [
        "ppc64le",
        "ppc64"
      ]
    },
    "fma": {
      "reason": "FMA has been supported by PowerISA since Power1, but might not be listed in features",
      "families": [
        "ppc64le",
        "ppc64"
      ]
    },
    "sse4.1": {
      "reason": "permits to refer to sse4_1 also as sse4.1",
      "any_of": [
        "sse4_1"
      ]
    },
    "sse4.2": {
      "reason": "permits to refer to sse4_2 also as sse4.2",
      "any_of": [
        "sse4_2"
      ]
    },
    "neon": {
      "reason": "NEON is required in all standard ARMv8 implementations",
      "families": [
        "aarch64"
      ]
    }
  },
  "conversions": {
    "description": "Conversions that map some platform specific values to canonical values",
    "arm_vendors": {
      "0x41": "ARM",
      "0x42": "Broadcom",
      "0x43": "Cavium",
      "0x44": "DEC",
      "0x46": "Fujitsu",
      "0x48": "HiSilicon",
      "0x49": "Infineon Technologies AG",
      "0x4d": "Motorola",
      "0x4e": "Nvidia",
      "0x50": "APM",
      "0x51": "Qualcomm",
      "0x53": "Samsung",
      "0x56": "Marvell",
      "0x61": "Apple",
      "0x66": "Faraday",
      "0x68": "HXT",
      "0x69": "Intel"
    },
    "darwin_flags": {
      "sse4.1": "sse4_1",
      "sse4.2": "sse4_2",
      "avx1.0": "avx",
      "clfsopt": "clflushopt",
      "xsave": "xsavec xsaveopt"
    }
  }
}
"""
)
