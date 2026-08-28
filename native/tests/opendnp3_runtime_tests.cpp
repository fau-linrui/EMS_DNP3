#include <opendnp3/DNP3Manager.h>

#include <exception>
#include <iostream>

int main()
{
    try {
        opendnp3::DNP3Manager manager(1U);
        manager.Shutdown();
        return 0;
    }
    catch (const std::exception& error) {
        std::cerr << "OpenDNP3 runtime smoke test failed: " << error.what() << '\n';
    }
    catch (...) {
        std::cerr << "OpenDNP3 runtime smoke test failed with an unknown exception\n";
    }
    return 1;
}
