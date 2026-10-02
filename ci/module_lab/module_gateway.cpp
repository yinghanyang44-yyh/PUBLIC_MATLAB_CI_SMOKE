/* Trusted direct adapter for cpp-module-v1. No candidate MATLAB is executed.
 * MATLAB API:
 *   trace = module_gateway(U, initialState, parameters, I, S, O)
 * U is N-by-I; trace is N-by-(O+2*S+1), ordered as
 * [output, actual_prestate, next_state, event_flags]. Reset handling belongs
 * to the candidate's step implementation; actual_prestate is its incoming
 * committed state, including on reset samples. This is not a native sandbox.
 */
#include "mex.h"
#include "module.h"

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace {
constexpr std::size_t kMaxWidth = 16;
constexpr mwSize kMaxSamples = 2000;
constexpr double kGuard = 9.876543210123456e200;
using Buffer = std::array<double, kMaxWidth + 2>;

void require(bool condition, const char* message) {
    if (!condition) mexErrMsgIdAndTxt("module_lab:gateway:validation", "%s", message);
}

void requireDouble(const mxArray* value, const char* message) {
    require(mxIsDouble(value) && !mxIsComplex(value) && !mxIsSparse(value)
            && mxGetNumberOfDimensions(value) == 2, message);
}

std::size_t width(const mxArray* value) {
    requireDouble(value, "Widths must be full real double scalars.");
    require(mxGetNumberOfElements(value) == 1, "Widths must be scalar.");
    const double number = mxGetScalar(value);
    require(std::isfinite(number) && number >= 1 && number <= kMaxWidth
            && std::floor(number) == number, "Widths must be integers in [1,16].");
    return static_cast<std::size_t>(number);
}

void requireFinite(const double* values, mwSize count, const char* message) {
    for (mwSize i = 0; i < count; ++i) require(std::isfinite(values[i]), message);
}

Buffer guarded(std::size_t count, double value) {
    Buffer buffer;
    buffer.fill(kGuard);
    for (std::size_t i = 0; i < count; ++i) buffer[i + 1] = value;
    return buffer;
}

void checkGuards(const Buffer& buffer, std::size_t count) {
    require(buffer.front() == kGuard, "Candidate overwrote a leading buffer guard.");
    for (std::size_t i = count + 1; i < buffer.size(); ++i)
        require(buffer[i] == kGuard, "Candidate overwrote a trailing buffer guard.");
}

void checkUnchanged(const Buffer& actual, const Buffer& expected) {
    require(std::memcmp(actual.data(), expected.data(), sizeof(Buffer)) == 0,
            "Candidate mutated a const input, state, or parameter buffer.");
}
}  // namespace

void mexFunction(int nlhs, mxArray* plhs[], int nrhs, const mxArray* prhs[]) {
    require(nrhs == 6 && nlhs == 1,
            "Expected one output and U, initialState, parameters, I, S, O.");
    const std::size_t inputWidth = width(prhs[3]);
    const std::size_t stateWidth = width(prhs[4]);
    const std::size_t outputWidth = width(prhs[5]);
    requireDouble(prhs[0], "U must be a full real double matrix.");
    requireDouble(prhs[1], "initialState must be a full real double vector.");
    requireDouble(prhs[2], "parameters must be a full real double vector.");
    const mwSize samples = mxGetM(prhs[0]);
    require(samples >= 1 && samples <= kMaxSamples && mxGetN(prhs[0]) == inputWidth,
            "U must have 1..2000 rows and input_width columns.");
    require(mxGetNumberOfElements(prhs[1]) == stateWidth
            && (mxGetM(prhs[1]) == 1 || mxGetN(prhs[1]) == 1),
            "initialState must be a state_width-element vector.");
    const mwSize parameterCount = mxGetNumberOfElements(prhs[2]);
    require(parameterCount <= kMaxWidth && (parameterCount == 0
            || mxGetM(prhs[2]) == 1 || mxGetN(prhs[2]) == 1),
            "parameters must be an empty or at most 16-element vector.");
    const double* inputs = mxGetDoubles(prhs[0]);
    const double* initial = mxGetDoubles(prhs[1]);
    const double* parameters = mxGetDoubles(prhs[2]);
    requireFinite(inputs, samples * inputWidth, "U contains a nonfinite value.");
    requireFinite(initial, stateWidth, "initialState contains a nonfinite value.");
    requireFinite(parameters, parameterCount, "parameters contains a nonfinite value.");

    Buffer state = guarded(stateWidth, 0.0);
    Buffer parameterBuffer = guarded(parameterCount, 0.0);
    for (std::size_t i = 0; i < stateWidth; ++i) state[i + 1] = initial[i];
    for (mwSize i = 0; i < parameterCount; ++i) parameterBuffer[i + 1] = parameters[i];
    const Buffer originalParameters = parameterBuffer;
    const mwSize traceWidth = outputWidth + 2 * stateWidth + 1;
    plhs[0] = mxCreateDoubleMatrix(samples, traceWidth, mxREAL);
    double* trace = mxGetDoubles(plhs[0]);
    for (mwSize row = 0; row < samples; ++row) {
        Buffer input = guarded(inputWidth, 0.0);
        for (std::size_t column = 0; column < inputWidth; ++column)
            input[column + 1] = inputs[row + samples * column];
        const Buffer originalInput = input;
        const Buffer before = state;
        Buffer output = guarded(outputWidth, std::numeric_limits<double>::quiet_NaN());
        Buffer next = guarded(stateWidth, std::numeric_limits<double>::quiet_NaN());
        // Two event guard words make ordinary adjacent writes observable.
        std::array<std::uint32_t, 3> event = {{0x5a5aa5a5u, 0u, 0x5a5aa5a5u}};
        module_step_v1(input.data() + 1, state.data() + 1,
                       parameterCount == 0 ? nullptr : parameterBuffer.data() + 1,
                       output.data() + 1, next.data() + 1, event.data() + 1);
        checkUnchanged(input, originalInput);
        checkUnchanged(state, before);
        checkUnchanged(parameterBuffer, originalParameters);
        checkGuards(output, outputWidth);
        checkGuards(next, stateWidth);
        require(event.front() == 0x5a5aa5a5u && event.back() == 0x5a5aa5a5u,
                "Candidate overwrote an event buffer guard.");
        requireFinite(output.data() + 1, outputWidth, "Candidate output is nonfinite or unwritten.");
        requireFinite(next.data() + 1, stateWidth, "Candidate next_state is nonfinite or unwritten.");
        for (std::size_t i = 0; i < outputWidth; ++i)
            trace[row + samples * i] = output[i + 1];
        for (std::size_t i = 0; i < stateWidth; ++i) {
            trace[row + samples * (outputWidth + i)] = before[i + 1];
            trace[row + samples * (outputWidth + stateWidth + i)] = next[i + 1];
            state[i + 1] = next[i + 1];
        }
        trace[row + samples * (traceWidth - 1)] = static_cast<double>(event[1]);
    }
}
