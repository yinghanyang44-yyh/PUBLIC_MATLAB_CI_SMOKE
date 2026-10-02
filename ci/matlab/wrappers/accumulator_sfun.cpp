#define S_FUNCTION_NAME accumulator_sfun
#define S_FUNCTION_LEVEL 2
#include "simstruc.h"
#include "accumulator.hpp"
#include <cmath>

// Thin simulation-only adapter. Algorithm and saturation semantics live in core/.
// Parameters: lower, upper, sample period, initial state. No code generation TLC.
#define MDL_CHECK_PARAMETERS
static void mdlCheckParameters(SimStruct* S) {
    for (int_T i = 0; i < 4; ++i) {
        const mxArray* p = ssGetSFcnParam(S, i);
        if (!mxIsDouble(p) || mxIsComplex(p) || mxIsSparse(p) ||
            mxGetNumberOfElements(p) != 1 || !mxIsFinite(mxGetScalar(p))) {
            ssSetErrorStatus(S, "Parameters must be finite real double scalars.");
            return;
        }
    }
    const double lower = mxGetScalar(ssGetSFcnParam(S, 0));
    const double upper = mxGetScalar(ssGetSFcnParam(S, 1));
    const double period = mxGetScalar(ssGetSFcnParam(S, 2));
    const double initial = mxGetScalar(ssGetSFcnParam(S, 3));
    if (lower > 0 || upper < 0 || lower > upper || period <= 0 || initial < lower || initial > upper) {
        ssSetErrorStatus(S, "Bounds must contain zero and initial state; sample period must be positive.");
    }
}

static void mdlInitializeSizes(SimStruct* S) {
    ssSetNumSFcnParams(S, 4);
    if (ssGetNumSFcnParams(S) != ssGetSFcnParamsCount(S)) return;
    mdlCheckParameters(S);
    if (ssGetErrorStatus(S) != nullptr) return;
    for (int_T i = 0; i < 4; ++i) ssSetSFcnParamTunable(S, i, 0);
    ssSetNumContStates(S, 0);
    ssSetNumDiscStates(S, 1);
    if (!ssSetNumInputPorts(S, 2)) return;
    for (int_T i = 0; i < 2; ++i) {
        ssSetInputPortWidth(S, i, 1);
        ssSetInputPortDataType(S, i, SS_DOUBLE);
        ssSetInputPortComplexSignal(S, i, COMPLEX_NO);
        ssSetInputPortDirectFeedThrough(S, i, 1);
        ssSetInputPortRequiredContiguous(S, i, 1);
    }
    if (!ssSetNumOutputPorts(S, 1)) return;
    ssSetOutputPortWidth(S, 0, 4);
    ssSetOutputPortDataType(S, 0, SS_DOUBLE);
    ssSetOutputPortComplexSignal(S, 0, COMPLEX_NO);
    ssSetNumSampleTimes(S, 1);
    ssSetNumRWork(S, 0); ssSetNumIWork(S, 0); ssSetNumPWork(S, 0);
    ssSetNumModes(S, 0); ssSetNumNonsampledZCs(S, 0);
    ssSetOptions(S, 0);
}

static void mdlInitializeSampleTimes(SimStruct* S) {
    ssSetSampleTime(S, 0, mxGetScalar(ssGetSFcnParam(S, 2)));
    ssSetOffsetTime(S, 0, 0.0);
}

#define MDL_INITIALIZE_CONDITIONS
static void mdlInitializeConditions(SimStruct* S) {
    ssGetRealDiscStates(S)[0] = mxGetScalar(ssGetSFcnParam(S, 3));
}

static bool readStep(SimStruct* S, matlab_ci::StepResult& result) {
    const real_T u = *static_cast<const real_T*>(ssGetInputPortSignal(S, 0));
    const real_T reset = *static_cast<const real_T*>(ssGetInputPortSignal(S, 1));
    if (!std::isfinite(u) || (reset != 0.0 && reset != 1.0)) {
        ssSetErrorStatus(S, "Increment must be finite and reset must be zero or one.");
        return false;
    }
    const matlab_ci::State state{ssGetRealDiscStates(S)[0]};
    const matlab_ci::Input input{u, reset != 0.0};
    const matlab_ci::Params params{mxGetScalar(ssGetSFcnParam(S, 0)), mxGetScalar(ssGetSFcnParam(S, 1))};
    result = matlab_ci::step(state, input, params);
    return true;
}

static void mdlOutputs(SimStruct* S, int_T /*tid*/) {
    matlab_ci::StepResult result{};
    if (!readStep(S, result)) return;
    real_T* y = ssGetOutputPortRealSignal(S, 0);
    y[0] = result.output;
    y[1] = ssGetRealDiscStates(S)[0]; // Actual engine-managed state before update.
    y[2] = result.state;
    y[3] = static_cast<real_T>(result.event);
    // Do not mutate state in mdlOutputs: Simulink can evaluate outputs repeatedly.
}

#define MDL_UPDATE
static void mdlUpdate(SimStruct* S, int_T /*tid*/) {
    matlab_ci::StepResult result{};
    if (readStep(S, result)) ssGetRealDiscStates(S)[0] = result.state;
}

static void mdlTerminate(SimStruct* /*S*/) {}
#ifdef MATLAB_MEX_FILE
#include "simulink.c"
#else
#error This Level-2 adapter is for normal-mode MEX simulation only.
#endif
