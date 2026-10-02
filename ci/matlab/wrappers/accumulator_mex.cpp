#include "mex.h"
#include "accumulator.hpp"
#include <cmath>

static void requireRealDenseDouble(const mxArray* a) {
    if (!mxIsDouble(a) || mxIsComplex(a) || mxIsSparse(a)) {
        mexErrMsgIdAndTxt("matlab_ci:core:input", "Inputs must be real, dense double arrays.");
    }
}

void mexFunction(int nlhs, mxArray *plhs[], int nrhs, const mxArray *prhs[]) {
    // trace = accumulator_mex(increments, resets, [lower upper initial]);
    // Columns: output, pre-update state, post-update state, event bitmask.
    if (nrhs != 3 || nlhs != 1) {
        mexErrMsgIdAndTxt("matlab_ci:core:arity", "Expected three inputs and one output.");
    }
    for (int j = 0; j < nrhs; ++j) requireRealDenseDouble(prhs[j]);
    const mwSize n = mxGetNumberOfElements(prhs[0]);
    if (n == 0 || n > 10000 || mxGetNumberOfElements(prhs[1]) != n ||
        mxGetNumberOfDimensions(prhs[0]) != 2 || mxGetNumberOfDimensions(prhs[1]) != 2 ||
        mxGetNumberOfElements(prhs[2]) != 3 ||
        (mxGetM(prhs[0]) != 1 && mxGetN(prhs[0]) != 1) ||
        (mxGetM(prhs[1]) != 1 && mxGetN(prhs[1]) != 1)) {
        mexErrMsgIdAndTxt("matlab_ci:core:shape", "Expected equal nonempty vectors (at most 10000 samples) and three parameters.");
    }
    const double* u = mxGetPr(prhs[0]);
    const double* reset = mxGetPr(prhs[1]);
    const double* p = mxGetPr(prhs[2]);
    if (!std::isfinite(p[0]) || !std::isfinite(p[1]) || !std::isfinite(p[2]) ||
        p[0] > 0 || p[1] < 0 || p[0] > p[1] || p[2] < p[0] || p[2] > p[1]) {
        mexErrMsgIdAndTxt("matlab_ci:core:parameters", "Finite bounds must contain zero and the initial state.");
    }
    for (mwSize i = 0; i < n; ++i) {
        if (!std::isfinite(u[i]) || (reset[i] != 0 && reset[i] != 1)) {
            mexErrMsgIdAndTxt("matlab_ci:core:value", "Increments must be finite; reset must be zero or one.");
        }
    }
    plhs[0] = mxCreateDoubleMatrix(n, 4, mxREAL);
    double* result = mxGetPr(plhs[0]);
    matlab_ci::State state{p[2]};
    const matlab_ci::Params params{p[0], p[1]};
    for (mwSize i = 0; i < n; ++i) {
        const matlab_ci::Input input{u[i], reset[i] != 0};
        const matlab_ci::StepResult step = matlab_ci::step(state, input, params);
        result[i] = step.output;
        result[i + n] = state.value;
        result[i + 2*n] = step.state;
        result[i + 3*n] = static_cast<double>(step.event);
        state.value = step.state;
    }
}
