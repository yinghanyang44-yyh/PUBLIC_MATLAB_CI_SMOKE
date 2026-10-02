#include "mex.h"
/* A separate C++ translation unit proves that both language configurations work. */
void mexFunction(int nlhs, mxArray *plhs[], int nrhs, const mxArray *prhs[]) {
    if (nrhs != 1 || nlhs != 1 || !mxIsDouble(prhs[0]) || mxIsComplex(prhs[0]) ||
        mxIsSparse(prhs[0]) || mxGetNumberOfElements(prhs[0]) != 1 ||
        !mxIsFinite(mxGetScalar(prhs[0]))) {
        mexErrMsgIdAndTxt("matlab_ci:smoke:input", "Expected one finite real double scalar and one output.");
    }
    plhs[0] = mxCreateDoubleScalar(mxGetScalar(prhs[0]) + 1.0);
}
