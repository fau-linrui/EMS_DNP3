#pragma once

#include "dnp3host/Backend.h"

#include <memory>

namespace dnp3host {

std::unique_ptr<IMasterBackend> make_opendnp3_backend();

}  // namespace dnp3host
