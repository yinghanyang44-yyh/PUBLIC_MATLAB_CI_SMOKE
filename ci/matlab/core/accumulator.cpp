#include "accumulator.hpp"

namespace matlab_ci {

StepResult step(const State& state, const Input& input,
                const Params& params) noexcept {
    const double base = input.reset ? 0.0 : state.value;
    const double raw = base + input.increment;
    double next = raw;
    unsigned event = input.reset ? 1u : 0u;

    if (raw < params.lower) {
        next = params.lower;
        event |= 2u;
    } else if (raw > params.upper) {
        next = params.upper;
        event |= 4u;
    }

    return StepResult{next, next, event};
}

}  // namespace matlab_ci
