# License staging

Reviewed third-party license texts are stored in dependency-specific directories.

| Dependency | License | Files |
|---|---|---|
| OpenDNP3 3.1.2 | Apache-2.0 | `opendnp3/LICENSE`, `opendnp3/NOTICE` |
| Asio asio-1-16-0 | BSL-1.0 | `asio/COPYING`, `asio/LICENSE_1_0.txt` |
| exe4cpp fixed commit | BSD-3-Clause | `exe4cpp/LICENSE` |
| ser4cpp fixed commit | BSD-3-Clause | `ser4cpp/LICENSE` |
| nlohmann/json 3.12.0 | MIT | `nlohmann_json/LICENSE.MIT` |

`dnp3-master-host` uses the fixed nlohmann/json single-header library. The fixed OpenDNP3 library and its dependencies are compiled and linked into a dedicated runtime smoke test, but are not linked into the host yet.
