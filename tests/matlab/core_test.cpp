#include "../../ci/matlab/core/accumulator.hpp"

#include <cstddef>
#include <iostream>

namespace {

using matlab_ci::Input;
using matlab_ci::Params;
using matlab_ci::State;
using matlab_ci::StepResult;
using matlab_ci::step;

unsigned checks = 0u;
unsigned failures = 0u;

void expect(bool condition, const char* description) {
    ++checks;
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << description << '\n';
    }
}

void expect_step(const State& state, const Input& input, const Params& params,
                 double expected, unsigned event, const char* description) {
    const double original_state = state.value;
    const double original_increment = input.increment;
    const bool original_reset = input.reset;
    const double original_lower = params.lower;
    const double original_upper = params.upper;
    const StepResult result = step(state, input, params);
    expect(result.output == expected, description);
    expect(result.state == expected, "returned state equals expected output");
    expect(result.event == event, "event bits match expected event");
    expect(state.value == original_state, "step does not mutate state");
    expect(input.increment == original_increment && input.reset == original_reset,
           "step does not mutate input");
    expect(params.lower == original_lower && params.upper == original_upper,
           "step does not mutate parameters");

    const StepResult repeated = step(state, input, params);
    expect(repeated.output == result.output && repeated.state == result.state &&
               repeated.event == result.event,
           "identical arguments produce identical results");
}

void test_known_sequence() {
    const double increments[] = {1.0, 2.0, 5.0, -1.0, -9.0,
                                 4.0, 0.0, 2.0, -2.0, 0.0};
    const bool resets[] = {false, false, false, false, false,
                           true, false, false, false, true};
    const double expected[] = {1.0, 3.0, 3.0, 2.0, -3.0,
                               3.0, 3.0, 3.0, 1.0, 0.0};
    const unsigned events[] = {0u, 0u, 4u, 0u, 2u, 5u, 0u, 4u, 0u, 1u};
    const Params params = {-3.0, 3.0};
    State state = {0.0};
    for (std::size_t i = 0; i < sizeof(increments) / sizeof(increments[0]); ++i) {
        const Input input = {increments[i], resets[i]};
        expect_step(state, input, params, expected[i], events[i], "known sequence output");
        state.value = step(state, input, params).state;
    }
}

void test_exact_boundaries() {
    const Params params = {-3.0, 3.0};
    expect_step(State{0.0}, Input{3.0, false}, params, 3.0, 0u,
                "arriving exactly at upper bound is not saturation");
    expect_step(State{0.0}, Input{-3.0, false}, params, -3.0, 0u,
                "arriving exactly at lower bound is not saturation");
    expect_step(State{3.0}, Input{0.0, false}, params, 3.0, 0u,
                "remaining at upper bound is not saturation");
    expect_step(State{-3.0}, Input{0.0, false}, params, -3.0, 0u,
                "remaining at lower bound is not saturation");
    expect_step(State{3.0}, Input{-6.0, false}, params, -3.0, 0u,
                "crossing exactly to lower bound is not saturation");
    expect_step(State{-3.0}, Input{6.0, false}, params, 3.0, 0u,
                "crossing exactly to upper bound is not saturation");
    expect_step(State{2.0}, Input{3.0, true}, params, 3.0, 1u,
                "reset to exact upper bound has only reset event");
    expect_step(State{-2.0}, Input{-3.0, true}, params, -3.0, 1u,
                "reset to exact lower bound has only reset event");
}

void test_reset_and_initial_state() {
    const Params params = {-3.0, 3.0};
    expect_step(State{2.0}, Input{0.5, false}, params, 2.5, 0u,
                "nonzero initial state is used");
    expect_step(State{-2.0}, Input{-0.5, false}, params, -2.5, 0u,
                "negative initial state is used");
    expect_step(State{2.0}, Input{0.0, true}, params, 0.0, 1u,
                "reset clears a nonzero initial state");
    expect_step(State{-2.0}, Input{1.0, true}, params, 1.0, 1u,
                "reset precedes addition");
    expect_step(State{3.0}, Input{-4.0, true}, params, -3.0, 3u,
                "reset combines with lower saturation");
    expect_step(State{-3.0}, Input{4.0, true}, params, 3.0, 5u,
                "reset combines with upper saturation");

    State state = {2.5};
    for (unsigned i = 0u; i < 5u; ++i) {
        expect_step(state, Input{1.0, true}, params, 1.0, 1u,
                    "repeated resets do not accumulate");
        state.value = step(state, Input{1.0, true}, params).state;
    }
    expect_step(state, Input{1.0, false}, params, 2.0, 0u,
                "accumulation resumes after repeated resets");
}

void test_alternate_bounds() {
    expect_step(State{5.0}, Input{0.0, true}, Params{2.0, 6.0}, 2.0, 3u,
                "reset base is zero even when zero is below lower bound");
    expect_step(State{-5.0}, Input{0.0, true}, Params{-6.0, -2.0}, -2.0, 5u,
                "reset base is zero even when zero is above upper bound");
    expect_step(State{0.0}, Input{0.25, false}, Params{-0.5, 0.5}, 0.25, 0u,
                "fractional values are preserved");
    expect_step(State{2.0}, Input{0.0, false}, Params{2.0, 2.0}, 2.0, 0u,
                "equal bounds with exact raw value have no saturation event");
    expect_step(State{2.0}, Input{-1.0, false}, Params{2.0, 2.0}, 2.0, 2u,
                "equal bounds report strict lower saturation");
    expect_step(State{2.0}, Input{1.0, false}, Params{2.0, 2.0}, 2.0, 4u,
                "equal bounds report strict upper saturation");
}

}  // namespace

static_assert(noexcept(matlab_ci::step(matlab_ci::State{0.0},
                                      matlab_ci::Input{0.0, false},
                                      matlab_ci::Params{-3.0, 3.0})),
              "the core step must be noexcept");

int main() {
    test_known_sequence();
    test_exact_boundaries();
    test_reset_and_initial_state();
    test_alternate_bounds();
    if (failures != 0u) {
        std::cerr << failures << " of " << checks << " checks failed\n";
        return 1;
    }
    std::cout << "PASS: " << checks << " deterministic accumulator checks\n";
    return 0;
}
