function run_public_smoke
% Seven fail-closed gates. Execution requires separate owner authorization.
sourceDir = fileparts(mfilename('fullpath'));
artifactDir = getenv('MATLAB_CI_ARTIFACT_DIR');
assert(~isempty(artifactDir), 'matlab_ci:artifactDir', 'An explicit artifact directory is required.');
if ~isfolder(artifactDir), mkdir(artifactDir); end
buildDir = fullfile(artifactDir, 'build');
modelDir = fullfile(artifactDir, 'generated-models');
names = {'release_and_products', 'minimal_simulink', 'numeric_command', ...
    'vs2022_c_and_cpp_mex', 'direct_cpp_core', 'sfunction_equivalence', 'diagnostics_complete'};
gates = repmat(struct('name','','status','NOT_RUN','seconds',0,'detail',''), 1, numel(names));
for i = 1:numel(names), gates(i).name = names{i}; end
writeJson(fullfile(artifactDir,'gates.json'), gates);
writeText(fullfile(artifactDir,'status.txt'), 'RUNNING');
activeGate = 0;
try
    if ~isfolder(buildDir), mkdir(buildDir); end
    if ~isfolder(modelDir), mkdir(modelDir); end
    oldPath = path;
    oldDir = pwd;
    cleanup = onCleanup(@() restoreSession(oldPath, oldDir)); %#ok<NASGU>
    diary(fullfile(artifactDir, 'matlab-diary.txt'));
    diary on
    fprintf('PUBLIC_MATLAB_CI_SMOKE started at %s UTC\n', char(datetime('now','TimeZone','UTC')));
    cd(buildDir);
    addpath(buildDir);
    activeGate = 1; timer = tic;
    release = version('-release');
    installed = ver;
    inventory = struct('release',release,'version',version,'architecture',computer('arch'), ...
        'matlab_root',matlabroot,'products',installed);
    writeJson(fullfile(artifactDir,'products.json'), inventory);
    fprintf('Release: %s; version: %s; architecture: %s\n',release,version,computer('arch'));
    disp(struct2table(installed));
    assert(strcmp(release,'2025a'), 'matlab_ci:release', 'Expected R2025a.');
    assert(ispc && strcmp(computer('arch'),'win64'), 'matlab_ci:platform', 'Expected Windows x64.');
    assert(any(strcmp({installed.Name},'MATLAB')) && any(strcmp({installed.Name},'Simulink')), ...
        'matlab_ci:products', 'MATLAB and Simulink must both be installed.');
    assert(license('test','MATLAB') && license('test','Simulink'), ...
        'matlab_ci:license', 'MATLAB and Simulink license checks must succeed.');
    gates = passGate(gates,activeGate,toc(timer),'Actual R2025a, win64, product inventory and license tests verified.',artifactDir);

    activeGate = 2; timer = tic;
    Simulink.fileGenControl('set', 'CacheFolder', fullfile(buildDir,'sl-cache'), ...
        'CodeGenFolder', fullfile(buildDir,'sl-codegen'), 'createDir', true);
    runMinimalSimulink(modelDir, artifactDir);
    gates = passGate(gates,activeGate,toc(timer),'Generated Constant(2) to Gain(3) model simulated; all outputs equal 6.',artifactDir);

    activeGate = 3; timer = tic;
    A = [3 1;1 2]; b = [9;8]; x = A\b;
    residual = norm(A*x-b,inf);
    assert(norm(x-[2;3],inf) < 1e-12 && residual < 1e-12, ...
        'matlab_ci:numeric', 'Numeric command assertion failed.');
    writeJson(fullfile(artifactDir,'numeric-command.json'),struct('solution',x,'residual_inf',residual));
    gates = passGate(gates,activeGate,toc(timer),'MATLAB linear solve and residual assertions passed.',artifactDir);

    activeGate = 4; timer = tic;
    configureVS2022(artifactDir);
    mex('-v','-R2018a','-outdir',buildDir,'-output','ci_add_one_c',fullfile(sourceDir,'smoke','add_one.c'));
    mex('-v','-R2018a','-outdir',buildDir,'-output','ci_add_one_cpp',fullfile(sourceDir,'smoke','add_one_cpp.cpp'));
    assert(ci_add_one_c(41)==42 && ci_add_one_cpp(41)==42, 'matlab_ci:mexSmoke', 'C and C++ MEX execution must both return 42.');
    gates = passGate(gates,activeGate,toc(timer),'Selected and rechecked VS2022 separately for C and C++; both MEX binaries built, loaded and executed.',artifactDir);

    activeGate = 5; timer = tic;
    includeCore = ['-I' fullfile(sourceDir,'core')];
    coreSource = fullfile(sourceDir,'core','accumulator.cpp');
    mex('-v','-R2018a','-outdir',buildDir,'-output','accumulator_mex',includeCore, ...
        fullfile(sourceDir,'wrappers','accumulator_mex.cpp'),coreSource);
    cases = makeCases;
    for c = 1:numel(cases)
        v = cases(c);
        direct = accumulator_mex(v.u,v.reset,[v.lower v.upper v.initial]);
        oracle = referenceTrace(v);
        assert(isequal(direct,oracle),'matlab_ci:coreOracle','Direct core differs from independent oracle for %s.',v.name);
        assert(isequal(direct,accumulator_mex(v.u,v.reset,[v.lower v.upper v.initial])), ...
            'matlab_ci:determinism','Repeated direct execution differs.');
        writematrix(direct,fullfile(artifactDir,[v.name '-direct.csv']));
    end
    assertThrows(@() accumulator_mex([NaN;1],[0;0],[-3 3 0]), 'matlab_ci:core:value');
    assertThrows(@() accumulator_mex([1;2],[0;2],[-3 3 0]), 'matlab_ci:core:value');
    gates = passGate(gates,activeGate,toc(timer),'Direct MEX uses shared portable core; known and deterministic fixtures, independent oracle, repeatability and invalid-input rejection passed.',artifactDir);

    activeGate = 6; timer = tic;
    mex('-v','-R2018a','-outdir',buildDir,'-output','accumulator_sfun',includeCore, ...
        ['-I' fullfile(matlabroot,'simulink','include')], ...
        fullfile(sourceDir,'wrappers','accumulator_sfun.cpp'),coreSource);
    metrics = struct('case_name',{},'samples',{},'max_abs_error',{},'rmse',{}, ...
        'event_mismatches',{},'final_committed_state_error',{},'reset_samples',{}, ...
        'lower_saturation_samples',{},'upper_saturation_samples',{});
    for c = 1:numel(cases)
        v = cases(c);
        direct = accumulator_mex(v.u,v.reset,[v.lower v.upper v.initial]);
        [times, simulated, finalState] = runAccumulatorModel(v,modelDir);
        expectedTimes = (0:numel(v.u)-1)';
        assert(isequal(size(simulated),size(direct)) && isequal(times,expectedTimes), ...
            'matlab_ci:samples','Sample count, shape or fixed-step times differ.');
        assert(all(isfinite(simulated),'all'),'matlab_ci:finite','Simulation contains nonfinite values.');
        error = simulated - direct;
        numericError = error(:,1:3);
        metric = struct('case_name',v.name,'samples',numel(v.u), ...
            'max_abs_error',max(abs(numericError),[],1), ...
            'rmse',sqrt(mean(numericError.^2,1)), ...
            'event_mismatches',sum(error(:,4)~=0), ...
            'final_committed_state_error',abs(finalState-direct(end,3)), ...
            'reset_samples',sum(bitand(uint32(direct(:,4)),uint32(1))~=0), ...
            'lower_saturation_samples',sum(bitand(uint32(direct(:,4)),uint32(2))~=0), ...
            'upper_saturation_samples',sum(bitand(uint32(direct(:,4)),uint32(4))~=0));
        metrics(c) = metric; %#ok<AGROW>
        writeJson(fullfile(artifactDir,'equivalence-metrics.json'),metrics);
        trace = array2table([times,v.u,v.reset,direct,simulated,error], 'VariableNames', ...
            {'time','increment','reset','core_y','core_state_before','core_state_after','core_event', ...
             'sim_y','sim_state_before','sim_state_after','sim_event','error_y','error_state_before','error_state_after','error_event'});
        writetable(trace,fullfile(artifactDir,[v.name '-comparison.csv']));
        assert(all(metric.max_abs_error<=1e-12) && all(metric.rmse<=1e-12) && metric.event_mismatches==0 && metric.final_committed_state_error<=1e-12, ...
            'matlab_ci:equivalence','Outputs, actual states or event bitmasks differ for %s.',v.name);
    end
    assert(metrics(1).reset_samples>0 && metrics(1).lower_saturation_samples>0 && metrics(1).upper_saturation_samples>0, ...
        'matlab_ci:eventCoverage','Reset and both saturation events must be exercised.');
    gates = passGate(gates,activeGate,toc(timer),'Same vectors agree across direct MEX and fixed-step Level-2 S-function: output, pre/post state, events, max error and RMSE.',artifactDir);

    activeGate = 7; timer = tic;
    diary off
    required = {'matlab-diary.txt','products.json','numeric-command.json','compiler-configurations.json', ...
        'equivalence-metrics.json','known-comparison.csv','deterministic-comparison.csv'};
    for i = 1:numel(required)
        info = dir(fullfile(artifactDir,required{i}));
        assert(~isempty(info) && info.bytes>0,'matlab_ci:evidence','Missing or empty evidence: %s',required{i});
    end
    gates = passGate(gates,activeGate,toc(timer),'Required diary, product/compiler inventories and numeric traces are present; artifact upload remains a separate workflow step.',artifactDir);
    writeText(fullfile(artifactDir,'status.txt'),'PASS_ALL_SEVEN_MATLAB_GATES');
    files = dir(fullfile(artifactDir,'**','*'));
    files = files(~[files.isdir]);
    manifest = struct('relative_path',{},'bytes',{});
    for i = 1:numel(files)
        absolute = fullfile(files(i).folder,files(i).name);
        manifest(i) = struct('relative_path',strrep(absolute,[artifactDir filesep],''),'bytes',files(i).bytes); %#ok<AGROW>
    end
    writeJson(fullfile(artifactDir,'artifact-manifest.json'),manifest);
catch exception
    diary on
    report = getReport(exception,'extended','hyperlinks','off');
    fprintf(2,'%s\n',report);
    writeText(fullfile(artifactDir,'failure.txt'),report);
    if activeGate>0
        gates(activeGate).status = 'FAIL';
        gates(activeGate).detail = exception.message;
    end
    writeJson(fullfile(artifactDir,'gates.json'),gates);
    writeText(fullfile(artifactDir,'status.txt'),'FAILED_MATLAB_GATE');
    diary off
    rethrow(exception);
end
end

function configureVS2022(artifactDir)
% Empty arrays must carry the same field schema as indexed assignments.
records = struct('language',{},'name',{},'short_name',{}, ...
    'manufacturer',{},'version',{},'location',{},'options_file',{});
languages = {'C','C++'};
for i=1:numel(languages)
    lang = languages{i};
    available = mex.getCompilerConfigurations(lang,'Installed');
    % Record every discovered candidate before selection assertions can fail.
    installedInventory = struct('language',{},'name',{},'short_name',{}, ...
        'manufacturer',{},'version',{},'location',{},'options_file',{});
    for j=1:numel(available)
        candidate = available(j);
        installedInventory(j) = struct('language',lang,'name',candidate.Name, ...
            'short_name',candidate.ShortName,'manufacturer',candidate.Manufacturer, ...
            'version',candidate.Version,'location',candidate.Location, ...
            'options_file',candidate.MexOpt); %#ok<AGROW>
    end
    writeJson(fullfile(artifactDir,['installed-compilers-' strrep(lang,'+','p') '.json']),installedInventory);
    names = {available.Name};
    expected = ['Microsoft Visual C++ 2022 (' lang ')'];
    matches = find(strcmp(names,expected) | strcmp(names,'Microsoft Visual C++ 2022'));
    assert(numel(matches)==1,'matlab_ci:compiler','Expected exactly one installed VS2022 %s compiler; found %d.',lang,numel(matches));
    chosen = available(matches);
    assert(contains(chosen.Manufacturer,'Microsoft'),'matlab_ci:compiler','Compiler manufacturer mismatch.');
    assert(isfile(chosen.MexOpt),'matlab_ci:compiler','Compiler options file is missing.');
    % The -setup:<options-file> syntax is emitted by MATLAB's own compiler-selection links.
    mex(['-setup:' chosen.MexOpt],lang);
    selected = mex.getCompilerConfigurations(lang,'Selected');
    assert(numel(selected)==1 && strcmp(selected.Name,chosen.Name) && strcmp(selected.Location,chosen.Location), ...
        'matlab_ci:compiler','VS2022 selection did not take effect for %s; no fallback is allowed.',lang);
    records(i) = struct('language',lang,'name',selected.Name,'short_name',selected.ShortName, ...
        'manufacturer',selected.Manufacturer,'version',selected.Version,'location',selected.Location, ...
        'options_file',selected.MexOpt); %#ok<AGROW>
    writeJson(fullfile(artifactDir,'compiler-configurations.json'),records);
    fprintf('Selected %s: %s (%s) at %s\n',lang,selected.Name,selected.Version,selected.Location);
end
end

function runMinimalSimulink(modelDir, artifactDir)
model = 'ci_minimal_runtime';
new_system(model);
cleanup = onCleanup(@() close_system(model,0)); %#ok<NASGU>
set_param(model,'SolverType','Fixed-step','Solver','FixedStepDiscrete','FixedStep','1', ...
    'StartTime','0','StopTime','2','ReturnWorkspaceOutputs','on');
add_block('simulink/Sources/Constant',[model '/Constant'],'Value','2','SampleTime','1');
add_block('simulink/Math Operations/Gain',[model '/Gain'],'Gain','3');
add_block('simulink/Sinks/To Workspace',[model '/Result'],'VariableName','minimalResult','SaveFormat','Timeseries');
add_line(model,'Constant/1','Gain/1');
add_line(model,'Gain/1','Result/1');
save_system(model,fullfile(modelDir,[model '.slx']));
out = sim(model);
result = out.get('minimalResult');
assert(isequal(result.Time,[0;1;2]) && all(result.Data(:)==6),'matlab_ci:minimal','Minimal Simulink output is incorrect.');
writematrix([result.Time,result.Data(:)],fullfile(artifactDir,'minimal-simulink.csv'));
end

function cases = makeCases
cases(1) = struct('name','known','u',[1;2;5;-1;-9;4;0;2;-2;0], ...
    'reset',[0;0;0;0;0;1;0;0;0;1],'lower',-3,'upper',3,'initial',0);
% Deterministic dyadic inputs avoid hidden RNG state and represent exactly in binary.
% The first sample does not reset, so the nonzero initial state is observable.
n = (0:256)';
cases(2) = struct('name','deterministic','u',(mod(37*n+11,65)-32)/8, ...
    'reset',double(mod(n,17)==0 & n>0),'lower',-3,'upper',3,'initial',1.25);
end

function trace = referenceTrace(v)
state = v.initial;
trace = zeros(numel(v.u),4);
for i=1:numel(v.u)
    before = state;
    event = 0;
    if v.reset(i)==1, state=0; event=1; end
    raw = state+v.u(i);
    if raw<v.lower, state=v.lower; event=event+2;
    elseif raw>v.upper, state=v.upper; event=event+4;
    else, state=raw;
    end
    trace(i,:) = [state,before,state,event];
end
if strcmp(v.name,'known')
    assert(isequal(trace(:,1),[1;3;3;2;-3;3;3;3;1;0]));
    assert(isequal(trace(:,4),[0;0;4;0;2;5;0;4;0;1]));
end
end

function [times, values, finalState] = runAccumulatorModel(v,modelDir)
model = ['ci_accumulator_' v.name];
new_system(model);
cleanup = onCleanup(@() close_system(model,0)); %#ok<NASGU>
set_param(model,'SolverType','Fixed-step','Solver','FixedStepDiscrete','FixedStep','1', ...
    'StartTime','0','StopTime',num2str(numel(v.u)),'ReturnWorkspaceOutputs','on', ...
    'SimulationMode','normal');
workspace = get_param(model,'ModelWorkspace');
% A final zero-input observation sample exposes the last meaningful committed state.
time = (0:numel(v.u))';
assignin(workspace,'incrementSignal',[time,[v.u;0]]);
assignin(workspace,'resetSignal',[time,[v.reset;0]]);
add_block('simulink/Sources/From Workspace',[model '/Increment'],'VariableName','incrementSignal', ...
    'Interpolate','off','SampleTime','1','OutputAfterFinalValue','Holding final value');
add_block('simulink/Sources/From Workspace',[model '/Reset'],'VariableName','resetSignal', ...
    'Interpolate','off','SampleTime','1','OutputAfterFinalValue','Holding final value');
add_block('simulink/User-Defined Functions/S-Function',[model '/Accumulator'], ...
    'FunctionName','accumulator_sfun','Parameters',sprintf('%.17g,%.17g,1,%.17g',v.lower,v.upper,v.initial));
add_block('simulink/Sinks/To Workspace',[model '/Trace'],'VariableName','accumulatorTrace', ...
    'SaveFormat','Timeseries','MaxDataPoints','inf','Decimation','1');
add_line(model,'Increment/1','Accumulator/1');
add_line(model,'Reset/1','Accumulator/2');
add_line(model,'Accumulator/1','Trace/1');
save_system(model,fullfile(modelDir,[model '.slx']));
out = sim(model);
trace = out.get('accumulatorTrace');
times = trace.Time(:);
values = reshape(trace.Data,numel(times),4);
assert(isequal(times,(0:numel(v.u))'),'matlab_ci:terminalSample','Missing final state observation sample.');
finalState = values(end,2);
times = times(1:end-1);
values = values(1:end-1,:);
end

function assertThrows(callable,identifier)
try
    ignored = callable(); %#ok<NASGU>
catch exception
    assert(strcmp(exception.identifier,identifier),'matlab_ci:wrongError','Expected %s, got %s.',identifier,exception.identifier);
    return
end
error('matlab_ci:missingError','Expected input validation failure: %s.',identifier);
end

function gates = passGate(gates,index,seconds,detail,artifactDir)
gates(index).status='PASS'; gates(index).seconds=seconds; gates(index).detail=detail;
writeJson(fullfile(artifactDir,'gates.json'),gates);
fprintf('Gate %d PASS: %s\n',index,gates(index).name);
end

function writeJson(filename,value)
writeText(filename,jsonencode(value,'PrettyPrint',true));
end

function writeText(filename,value)
fid=fopen(filename,'w');
assert(fid>=0,'matlab_ci:write','Cannot write %s.',filename);
cleanup=onCleanup(@() fclose(fid)); %#ok<NASGU>
fprintf(fid,'%s\n',value);
end

function restoreSession(oldPath,oldDir)
diary off
cd(oldDir);
path(oldPath);
end
