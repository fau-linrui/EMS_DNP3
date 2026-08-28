# Third-party dependency staging

This directory contains fixed, reviewed dependency sources and source archives.

## Current inventory

| Dependency | Local path | Fixed revision | Status |
|---|---|---|---|
| OpenDNP3 | `opendnp3/` | tag `3.1.2`; commit `26b4c01e4839bbbda8866655e086471c4917ee53` | Source and official ZIP downloaded; license recorded |
| OpenDNP3 source archive | `distfiles/opendnp3-3.1.2.zip` | SHA-256 recorded in `opendnp3.lock.json` | Present |
| nlohmann/json | `nlohmann_json/single_include/nlohmann/json.hpp` | v3.12.0; commit `55f93686c01528224f448c19128836e7df245f72` | Official release header and MIT license staged; SHA-256 recorded |
| Asio | `asio/` | `asio-1-16-0` | Archive SHA-1/SHA-256, source-tree SHA-256 and BSL-1.0 texts locked |
| exe4cpp | `exe4cpp/` | `fb878a4de598ba9d6e4338afebf83f96e03af1b8` | Archive SHA-1/SHA-256, source-tree SHA-256 and BSD-3-Clause text locked |
| ser4cpp | `ser4cpp/` | `3c449734dc530a8f465eb0982de29165cc4e23d5` | Archive SHA-1/SHA-256, source-tree SHA-256 and BSD-3-Clause text locked |

OpenDNP3 and nlohmann/json must not be taken from floating branches or downloaded during a normal build. `opendnp3-dependencies.lock.json` is the machine-checked lock for the three OpenDNP3 build dependencies. CMake forces `FETCHCONTENT_FULLY_DISCONNECTED` and redirects all three FetchContent inputs to these local source trees.

Validate the staged artifacts without network access:

```powershell
.\.venv\Scripts\python.exe scripts\validate_dependencies.py third_party\opendnp3-dependencies.lock.json
```

The current OpenDNP3 checkout intentionally retains its nested Git metadata so the tag and commit can be independently re-verified. Before publishing the parent project, it must be represented deliberately as a Git submodule or converted to a reviewed vendored source tree; it must not be accidentally committed as an unconfigured embedded repository.
