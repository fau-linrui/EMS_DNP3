#pragma once

#include "dnp3host/Backend.h"

#include <memory>
#include <cstddef>

namespace dnp3host {

std::unique_ptr<IMasterBackend> make_opendnp3_backend(
    std::size_t unsolicited_queue_capacity = 4096);

}  // namespace dnp3host
