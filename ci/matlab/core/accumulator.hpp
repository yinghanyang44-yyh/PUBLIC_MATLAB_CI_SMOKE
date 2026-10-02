#ifndef MATLAB_CI_ACCUMULATOR_HPP
#define MATLAB_CI_ACCUMULATOR_HPP

namespace matlab_ci {

struct State {
    double value;
};

struct Input {
    double increment;
    bool reset;
};

struct Params {
    double lower;
    double upper;
};

struct StepResult {
    double output;
    double state;
    unsigned event;
};

// Pure discrete step. Callers validate finite inputs and lower <= upper.
// Event bits: 1 = reset, 2 = strict lower saturation, 4 = strict upper saturation.
// The supplied State is never mutated; callers explicitly commit result.state.
StepResult step(const State& state, const Input& input,
                const Params& params) noexcept;

}  // namespace matlab_ci

#endif  // MATLAB_CI_ACCUMULATOR_HPP
