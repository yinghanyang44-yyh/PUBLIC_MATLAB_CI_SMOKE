function run_module_lab
% Trusted cpp-module-v1 runtime. Launch only after source-bound admission.
% Candidate matlab/build_mex.m and matlab/run_harness.m are proposals only:
% neither is called or placed on the MATLAB path.
%
% Fixed direct API (implemented by the trusted module_gateway.cpp):
% trace = module_gateway(U, initialState, parameters, I, S, O)
% U: N-by-I. trace: N-by-(O+2*S+1), with columns
% [outputs, actual incoming committed state, next_state, event_flags].
% A reset, when present, is processed by module_step_v1 before its step; the
% trace prestate remains the ACTUAL incoming committed state on reset rows.
trustedDir = fileparts(mfilename('fullpath'));
evidenceDir = getenv('MATLAB_MODULE_EVIDENCE_DIR');
assert(~isempty(evidenceDir),'module_lab:evidenceDir','MATLAB_MODULE_EVIDENCE_DIR is required.');
if ~isfolder(evidenceDir), mkdir(evidenceDir); end
evidenceDir = canonicalPath(evidenceDir);
ledger = initialLedger;
writeJson(fullfile(evidenceDir,'matlab-ledger.json'),ledger);
writeText(fullfile(evidenceDir,'matlab-status.txt'),'RUNNING');
receipt = initialReceipt;
activeGate = 'MATLAB_STARTUP';
oldPath = path;
oldDir = pwd;
cleanup = onCleanup(@() restoreSession(oldPath,oldDir)); %#ok<NASGU>
try
    diary(fullfile(evidenceDir,'matlab-diary.txt'));
    diary on
    fprintf('PUBLIC MATLAB MODULE runtime started %s\n',utcNow);
    ledger = recordGate(ledger,'MATLAB_STARTUP','PASS','Trusted MATLAB entrypoint reached.',evidenceDir);
    activeGate = 'MODULE_RUNTIME_INPUTS';
    sourceDir = getenv('MATLAB_MODULE_SOURCE_DIR');
    assert(~isempty(sourceDir) && isfolder(sourceDir),'module_lab:sourceDir', ...
        'MATLAB_MODULE_SOURCE_DIR must name the admitted snapshot directory.');
    sourceDir = canonicalPath(sourceDir);
    assert(~isWithin(evidenceDir,sourceDir),'module_lab:directories', ...
        'The evidence directory must not be inside the immutable source snapshot.');
    admissionPath = fullfile(evidenceDir,'admission.json');
    manifestPath = fullfile(evidenceDir,'source-manifest.json');
    assert(isfile(admissionPath) && isfile(manifestPath),'module_lab:admission', ...
        'The parent must stage admission.json and source-manifest.json before MATLAB starts.');
    admissionText = fileread(admissionPath);
    manifestText = fileread(manifestPath);
    admission = jsondecode(admissionText);
    sourceManifest = jsondecode(manifestText);
    assert(isfield(admission,'schema') && strcmp(admission.schema,'public-matlab-module-admission-v1') ...
        && isfield(sourceManifest,'schema') && strcmp(sourceManifest.schema,'public-module-source-manifest-v1'), ...
        'module_lab:admission','Unexpected admission or source manifest schema.');
    assert(isfield(admission,'module_sha') && isfield(admission,'infrastructure_sha') ...
        && isfield(sourceManifest,'module_sha') && strcmp(admission.module_sha,sourceManifest.module_sha), ...
        'module_lab:admission','Admission must distinguish and bind the module and infrastructure SHAs.');
    assert(isSha(admission.module_sha) && isSha(admission.infrastructure_sha), ...
        'module_lab:admission','Expected full 40-character commit SHAs.');
    % Keep exact upstream JSON text as well as decoded source identities.
    receipt.admission_json_verbatim = admissionText;
    receipt.source_manifest_json_verbatim = manifestText;
    receipt.admission = admission;
    receipt.source_manifest = sourceManifest;
    receipt.module_sha = admission.module_sha;
    receipt.infrastructure_sha = admission.infrastructure_sha;
    receipt.source_dir = sourceDir;
    receipt.trusted_runner = mfilename('fullpath');
    writeJson(fullfile(evidenceDir,'matlab-receipt.json'),receipt);
    contractPath = fullfile(sourceDir,'contract','contract.json');
    contract = jsondecode(fileread(contractPath));
    contract = validateContract(contract);
    [inputs,expected] = readFixtures(sourceDir,contract);
    receipt.module_id = contract.module_id;
    receipt.module_version = contract.version;
    receipt.sample_count = contract.sample_count;
    receipt.sample_time = contract.sample_time;
    writeJson(fullfile(evidenceDir,'runtime-contract.json'),contract);
    writeJson(fullfile(evidenceDir,'matlab-receipt.json'),receipt);
    buildDir = fullfile(evidenceDir,'runtime-build');
    modelDir = fullfile(evidenceDir,'runtime-models');
    assert(~isfolder(buildDir) && ~isfolder(modelDir),'module_lab:staleBuild', ...
        'Runtime build/model directories must be fresh; use a new evidence directory for each attempt.');
    mkdir(buildDir); mkdir(modelDir);
    cd(buildDir);
    restoredefaultpath;
    addpath(buildDir,'-begin');
    clear module_gateway sfun_module
    ledger = recordGate(ledger,activeGate,'PASS', ...
        'Admitted source bindings preserved; bounded contract and fixture shapes validated.',evidenceDir);

    activeGate = 'MATLAB_RELEASE';
    installed = ver;
    release = version('-release');
    inventory = struct('release',release,'version',version,'architecture',computer('arch'), ...
        'matlab_root',matlabroot,'products',installed);
    writeJson(fullfile(evidenceDir,'products.json'),inventory);
    assert(strcmp(release,'2025a') && ispc && strcmp(computer('arch'),'win64'), ...
        'module_lab:release','Only MATLAB R2025a on Windows x64 is admitted.');
    assert(any(strcmp({installed.Name},'MATLAB')) && license('test','MATLAB'), ...
        'module_lab:matlab','MATLAB product and license checks must succeed.');
    ledger = recordGate(ledger,activeGate,'PASS','Actual R2025a Windows x64 release and MATLAB license verified.',evidenceDir);
    activeGate = 'SIMULINK_AVAILABILITY';
    assert(any(strcmp({installed.Name},'Simulink')) && license('test','Simulink'), ...
        'module_lab:simulink','Simulink product and license checks must succeed.');
    load_system('simulink');
    Simulink.fileGenControl('set','CacheFolder',fullfile(buildDir,'sl-cache'), ...
        'CodeGenFolder',fullfile(buildDir,'sl-codegen'),'createDir',true);
    ledger = recordGate(ledger,activeGate,'PASS','Installed/licensed Simulink library loaded.',evidenceDir);

    activeGate = 'MEX_COMPILER';
    configureVS2022(evidenceDir);
    ledger = recordGate(ledger,activeGate,'PASS','VS2022 selected and rechecked independently for C and C++.',evidenceDir);
    coreSource = fullfile(sourceDir,'src','module.cpp');
    wrapperSource = fullfile(sourceDir,'sfunction','sfun_module.cpp');
    includeModule = ['-I' fullfile(sourceDir,'include')];
    assert(isfile(coreSource) && isfile(wrapperSource) && isfile(fullfile(sourceDir,'include','module.h')), ...
        'module_lab:sources','Required admitted C++ sources are missing.');
    activeGate = 'DIRECT_CORE_MEX_BUILD';
    directArgs = {'-v','-R2018a','COMPFLAGS=$COMPFLAGS /std:c++17','-outdir',buildDir,'-output','module_gateway',includeModule, ...
        fullfile(trustedDir,'module_gateway.cpp'),coreSource};
    compileLogged(directArgs,fullfile(evidenceDir,'direct-core-compiler.txt'));
    directBinary = verifyBinary(buildDir,'module_gateway');
    writeJson(fullfile(evidenceDir,'direct-core-build.json'), ...
        struct('source',coreSource,'trusted_adapter',fullfile(trustedDir,'module_gateway.cpp'), ...
        'arguments',{directArgs},'binary',directBinary));
    ledger = recordGate(ledger,activeGate,'PASS','Admitted module.cpp compiled with the trusted direct gateway.',evidenceDir);

    activeGate = 'DIRECT_CORE_MEX';
    direct = module_gateway(inputs,contract.initial_state,contract.parameters, ...
        contract.input_width,contract.state_width,contract.output_width);
    validateTrace(direct,contract.sample_count,contract);
    writeTrace(fullfile(evidenceDir,'direct-trace.csv'),direct,contract);
    ledger = recordGate(ledger,activeGate,'PASS','Exact built gateway loaded and executed; finite outputs and const-buffer checks passed.',evidenceDir);
    activeGate = 'DIRECT_DETERMINISTIC_REPLAY';
    replay = module_gateway(inputs,contract.initial_state,contract.parameters, ...
        contract.input_width,contract.state_width,contract.output_width);
    writeTrace(fullfile(evidenceDir,'direct-replay-trace.csv'),replay,contract);
    assert(isequal(direct,replay),'module_lab:determinism','Repeated direct execution changed its trace.');
    ledger = recordGate(ledger,activeGate,'PASS','Two complete direct traces are exactly equal.',evidenceDir);
    activeGate = 'DIRECT_ORACLE_EQUIVALENCE';
    numericColumns = [1:contract.output_width,contract.output_width+contract.state_width+(1:contract.state_width)];
    oracleMetric = compareTraces('direct_vs_expected',direct(:,numericColumns),expected(:,1:end-1), ...
        direct(:,end),expected(:,end));
    writeJson(fullfile(evidenceDir,'direct-oracle-metrics.json'),oracleMetric);
    assertMetric(oracleMetric);
    verifyPrestate(direct,contract,'direct');
    ledger = recordGate(ledger,activeGate,'PASS','Direct output and next-state match frozen expected CSV; event flags match exactly.',evidenceDir);

    activeGate = 'SFUNCTION_BUILD';
    wrapperArgs = {'-v','-R2018a','COMPFLAGS=$COMPFLAGS /std:c++17','-outdir',buildDir,'-output','sfun_module',includeModule, ...
        ['-I' fullfile(matlabroot,'simulink','include')],wrapperSource,coreSource};
    compileLogged(wrapperArgs,fullfile(evidenceDir,'sfunction-compiler.txt'));
    wrapperBinary = verifyBinary(buildDir,'sfun_module');
    writeJson(fullfile(evidenceDir,'sfunction-build.json'), ...
        struct('source',wrapperSource,'core_source',coreSource,'arguments',{wrapperArgs},'binary',wrapperBinary));
    ledger = recordGate(ledger,activeGate,'PASS', ...
        'Actual candidate sfunction/sfun_module.cpp linked to actual candidate src/module.cpp.',evidenceDir);
    activeGate = 'SFUNCTION_LOAD';
    model = makeHarness(inputs,contract,modelDir);
    modelCleanup = onCleanup(@() closeModel(model)); %#ok<NASGU>
    receipt.candidate_wrapper_execution = 'ATTEMPTED';
    writeJson(fullfile(evidenceDir,'matlab-receipt.json'),receipt);
    updateLog = evalc('updateFailure = updateAttempt(model);');
    writeText(fullfile(evidenceDir,'sfunction-load.txt'),updateLog);
    if ~isempty(updateFailure), rethrow(updateFailure); end
    assert(strcmp(canonicalPath(which('sfun_module')),wrapperBinary),'module_lab:binary', ...
        'S-function resolution changed during model update.');
    receipt.candidate_wrapper_execution = 'MODEL_UPDATE_PASSED';
    writeJson(fullfile(evidenceDir,'matlab-receipt.json'),receipt);
    ledger = recordGate(ledger,activeGate,'PASS','Generated model update loaded and initialized the exact built candidate S-function.',evidenceDir);

    activeGate = 'SIMULINK_HARNESS';
    simulationLog = evalc('[simulation,simulationFailure] = simulationAttempt(model);');
    writeText(fullfile(evidenceDir,'simulink-simulation.txt'),simulationLog);
    if ~isempty(simulationFailure), rethrow(simulationFailure); end
    receipt.candidate_wrapper_execution = 'SIMULATION_PASSED';
    result = simulation.get('moduleTrace');
    [times,simulated,terminalPrestate] = unpackSimulation(result,contract);
    writeTrace(fullfile(evidenceDir,'sfunction-trace.csv'),simulated,contract);
    writeCsv(fullfile(evidenceDir,'terminal-observation.csv'),[contract.sample_count,terminalPrestate], ...
        [{'sample'},numberedNames('x_pre',contract.state_width)]);
    writematrix(times,fullfile(evidenceDir,'simulation-times.csv'));
    save(fullfile(evidenceDir,'numeric-evidence.mat'),'inputs','expected','direct','replay','simulated','terminalPrestate','times','contract');
    ledger = recordGate(ledger,activeGate,'PASS', ...
        'Trusted normal-mode fixed-step harness simulated N frozen steps plus a last-input-repeat observation of committed prestate only.',evidenceDir);

    activeGate = 'CPP_SFUNCTION_EQUIVALENCE';
    metrics = emptyMetrics;
    metrics(1) = oracleMetric;
    metrics(2) = compareTraces('sfunction_vs_direct',simulated(:,1:end-1),direct(:,1:end-1),simulated(:,end),direct(:,end));
    metrics(3) = compareTraces('sfunction_vs_expected',simulated(:,numericColumns),expected(:,1:end-1),simulated(:,end),expected(:,end));
    nextColumns = contract.output_width+contract.state_width+(1:contract.state_width);
    finalError = terminalPrestate-direct(end,nextColumns);
    finalMetric = struct('max_abs_error',max(abs(finalError)),'rmse',sqrt(mean(finalError.^2)), ...
        'observed_committed_state',terminalPrestate,'expected_committed_state',direct(end,nextColumns), ...
        'terminal_input',inputs(end,:),'terminal_output_assessed',false, ...
        'terminal_next_state_assessed',false,'terminal_event_assessed',false, ...
        'terminal_policy','Repeat the last frozen input; assess only actual incoming committed state. The extra row is outside sample_count and the frozen oracle.');
    writeJson(fullfile(evidenceDir,'equivalence-metrics.json'),metrics);
    writeJson(fullfile(evidenceDir,'final-committed-state.json'),finalMetric);
    writeComparison(fullfile(evidenceDir,'comparison-trace.csv'),times,inputs,direct,simulated,contract);
    for k=1:numel(metrics), assertMetric(metrics(k)); end
    verifyPrestate(simulated,contract,'sfunction');
    assert(finalMetric.max_abs_error<=1e-12 && finalMetric.rmse<=1e-12, ...
        'module_lab:finalState','Last meaningful next_state was not committed before the terminal observation.');
    ledger = recordGate(ledger,activeGate,'PASS', ...
        'Outputs, actual prestate, next-state, events and final committed state agree; maxabs/RMSE <= 1e-12 and events exact.',evidenceDir);

    activeGate = 'MODULE_RUNTIME_DIAGNOSTICS';
    diary off
    required = {'admission.json','source-manifest.json','matlab-diary.txt','products.json', ...
        'compiler-configurations.json','direct-core-compiler.txt','sfunction-compiler.txt', ...
        'sfunction-load.txt','simulink-simulation.txt','direct-trace.csv','direct-replay-trace.csv', ...
        'sfunction-trace.csv','terminal-observation.csv','comparison-trace.csv','equivalence-metrics.json', ...
        'direct-oracle-metrics.json','final-committed-state.json','numeric-evidence.mat', ...
        fullfile('runtime-models','public_module_runtime_harness.slx')};
    for k=1:numel(required)
        info = dir(fullfile(evidenceDir,required{k}));
        assert(~isempty(info) && info.bytes>0,'module_lab:evidence','Missing or empty evidence: %s',required{k});
    end
    ledger = recordGate(ledger,activeGate,'PASS','Required source-bound diagnostics, numeric traces and generated SLX are present.',evidenceDir);
    ledger = recordGate(ledger,'PUBLIC_MODULE_RUNTIME','PASS','Complete public MATLAB runtime subset passed; private acceptance evidence is still required.',evidenceDir);
    receipt.status = 'PASS';
    receipt.completed_at_utc = utcNow;
    receipt.ledger = ledger;
    writeJson(fullfile(evidenceDir,'matlab-receipt.json'),receipt);
    writeText(fullfile(evidenceDir,'matlab-status.txt'),'PASS_PUBLIC_RUNTIME_ONLY');
    writeArtifactIndex(evidenceDir);
catch exception
    report = getReport(exception,'extended','hyperlinks','off');
    fprintf(2,'%s\n',report);
    writeText(fullfile(evidenceDir,'matlab-failure.txt'),report);
    ledger = recordGate(ledger,activeGate,'FAIL',exception.message,evidenceDir);
    ledger = recordGate(ledger,'PUBLIC_MODULE_RUNTIME','FAIL','Public runtime did not complete successfully; see matlab-failure.txt.',evidenceDir);
    receipt.status = 'FAIL';
    receipt.completed_at_utc = utcNow;
    receipt.ledger = ledger;
    receipt.failure_identifier = exception.identifier;
    receipt.failure_message = exception.message;
    writeJson(fullfile(evidenceDir,'matlab-receipt.json'),receipt);
    writeText(fullfile(evidenceDir,'matlab-status.txt'),'FAILED_PUBLIC_RUNTIME');
    diary off
    writeArtifactIndex(evidenceDir);
    rethrow(exception);
end
end

function c = validateContract(c)
assert(isstruct(c) && isscalar(c),'module_lab:contract','Contract must be a JSON object.');
required = {'schema','module_id','version','input_width','state_width','output_width','parameters', ...
    'initial_state','sample_time','sample_count','input_units','state_units','output_units', ...
    'reset_input_index','reset_timing','output_timing','event_flag_bits'};
assert(all(isfield(c,required)),'module_lab:contract','Contract is missing required fields.');
assert(strcmp(c.schema,'cpp-module-v1') && isText(c.module_id) ...
    && ~isempty(regexp(c.module_id,'^[a-z][a-z0-9_-]{0,47}$','once')), ...
    'module_lab:contract','Invalid module schema or lowercase module slug.');
assert(isText(c.version) && ~isempty(regexp(c.version,'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$','once')), ...
    'module_lab:contract','Version must be numeric semver x.y.z.');
validateInteger(c.input_width,1,16,'input_width');
validateInteger(c.state_width,1,16,'state_width');
validateInteger(c.output_width,1,16,'output_width');
validateInteger(c.sample_count,1,2000,'sample_count');
assert(isnumeric(c.sample_time) && isscalar(c.sample_time) && isfinite(c.sample_time) ...
    && c.sample_time>0 && c.sample_time<=1000,'module_lab:contract','Invalid sample_time.');
assert(isnumeric(c.parameters) && isreal(c.parameters) && (isempty(c.parameters) || isvector(c.parameters)) ...
    && numel(c.parameters)<=16 && all(isfinite(c.parameters(:))), 'module_lab:contract','Invalid parameter vector.');
assert(isnumeric(c.initial_state) && isreal(c.initial_state) && isvector(c.initial_state) ...
    && numel(c.initial_state)==c.state_width && all(isfinite(c.initial_state(:))), ...
    'module_lab:contract','Invalid initial_state vector.');
validateUnits(c.input_units,c.input_width); validateUnits(c.state_units,c.state_width); validateUnits(c.output_units,c.output_width);
if isempty(c.reset_input_index)
    assert(strcmp(c.reset_timing,'none'),'module_lab:contract','Null reset_input_index requires reset_timing=none.');
else
    validateInteger(c.reset_input_index,0,c.input_width-1,'reset_input_index');
    assert(strcmp(c.reset_timing,'before_step'),'module_lab:contract','Reset timing must be before_step.');
end
assert(strcmp(c.output_timing,'output_and_next_from_pre_state'),'module_lab:contract','Unsupported output timing.');
assert(isstruct(c.event_flag_bits) && isscalar(c.event_flag_bits),'module_lab:contract','event_flag_bits must be an object.');
meanings = struct2cell(c.event_flag_bits);
for k=1:numel(meanings)
    assert(isText(meanings{k}) && ~isempty(meanings{k}) && numel(meanings{k})<=80, ...
        'module_lab:contract','Invalid event bit meaning.');
end
% Numeric JSON member names are normalized by jsondecode. Their exact decimal
% spelling and 0..31 bounds are checked by the strict upstream Python validator.
c.parameters = double(c.parameters(:)');
c.initial_state = double(c.initial_state(:)');
end

function [inputs,expected] = readFixtures(sourceDir,c)
inputNames = [{'sample'},numberedNames('u',c.input_width)];
expectedNames = [{'sample'},numberedNames('y',c.output_width),numberedNames('x_next',c.state_width),{'event_flags'}];
inputMatrix = readNumericCsv(fullfile(sourceDir,'tests','test_vectors.csv'),inputNames,c.sample_count);
expectedMatrix = readNumericCsv(fullfile(sourceDir,'expected_outputs.csv'),expectedNames,c.sample_count);
samples = (0:c.sample_count-1)';
assert(isequal(inputMatrix(:,1),samples) && isequal(expectedMatrix(:,1),samples), ...
    'module_lab:fixtures','CSV sample indices must be contiguous 0..N-1.');
inputs = inputMatrix(:,2:end); expected = expectedMatrix(:,2:end);
if ~isempty(c.reset_input_index)
    reset = inputs(:,c.reset_input_index+1);
    assert(all(reset==0 | reset==1),'module_lab:fixtures','Reset input values must be 0 or 1.');
end
validateEvents(expected(:,end));
end

function matrix = readNumericCsv(filename,headers,rowCount)
assert(isfile(filename),'module_lab:fixtures','Missing CSV: %s',filename);
fields = parseCsv(fileread(filename),rowCount+1,numel(headers));
assert(isequal(fields(1,:),headers),'module_lab:fixtures','CSV header mismatch in %s.',filename);
matrix = zeros(rowCount,numel(headers));
for r=1:rowCount
    % Python's admitted finite-float syntax also permits numeric underscores.
    values = str2double(strrep(fields(r+1,:),'_',''));
    assert(all(isfinite(values)) && isreal(values),'module_lab:fixtures','CSV values must be finite real numbers.');
    matrix(r,:) = values;
end
end

function fields = parseCsv(text,rowCount,columnCount)
% Quoted CSV is supported, including quoted headers and embedded newlines.
% Upstream strict CSV validation remains authoritative. Allocation is bounded.
text = strrep(strrep(text,sprintf('\r\n'),sprintf('\n')),sprintf('\r'),sprintf('\n'));
fields = cell(rowCount,columnCount);
row=1; column=1; token=''; quoted=false; closed=false; position=1;
while position<=numel(text)
    ch=text(position);
    if quoted
        if ch=='"'
            if position<numel(text) && text(position+1)=='"'
                token(end+1)='"'; position=position+1; %#ok<AGROW>
            else
                quoted=false; closed=true;
            end
        else
            token(end+1)=ch; %#ok<AGROW>
        end
    elseif ch=='"' && isempty(token) && ~closed
        quoted=true;
    elseif ch==',' || ch==sprintf('\n')
        assert(row<=rowCount && column<=columnCount,'module_lab:fixtures','CSV row/column count exceeded.');
        fields{row,column}=token; token=''; closed=false; column=column+1;
        if ch==sprintf('\n')
            assert(column==columnCount+1,'module_lab:fixtures','CSV column count mismatch.');
            row=row+1; column=1;
        end
    else
        assert(~closed,'module_lab:fixtures','Unexpected characters after a quoted CSV field.');
        token(end+1)=ch; %#ok<AGROW>
    end
    position=position+1;
end
assert(~quoted,'module_lab:fixtures','Unterminated quoted CSV field.');
if ~isempty(token) || closed || column>1
    assert(row<=rowCount && column==columnCount,'module_lab:fixtures','CSV final row/column count mismatch.');
    fields{row,column}=token; row=row+1;
end
assert(row==rowCount+1,'module_lab:fixtures','CSV row count mismatch.');
end

function model = makeHarness(inputs,c,modelDir)
model = 'public_module_runtime_harness';
assert(~bdIsLoaded(model),'module_lab:model','Trusted harness name is already loaded.');
new_system(model);
try
    step = sprintf('%.17g',c.sample_time);
    stop = sprintf('%.17g',c.sample_count*c.sample_time);
    set_param(model,'SolverType','Fixed-step','Solver','FixedStepDiscrete','FixedStep',step, ...
        'StartTime','0','StopTime',stop,'ReturnWorkspaceOutputs','on','SimulationMode','normal');
    workspace = get_param(model,'ModelWorkspace');
    time = (0:c.sample_count)'*c.sample_time;
    % Repeat a known input: the contract does not require zero to be valid.
    % Only this extra row's incoming committed state is assessed.
    assignin(workspace,'moduleInput',[time,[inputs;inputs(end,:)]]);
    assignin(workspace,'moduleParameters',c.parameters);
    assignin(workspace,'moduleInitialState',c.initial_state);
    assignin(workspace,'moduleInputWidth',c.input_width);
    assignin(workspace,'moduleStateWidth',c.state_width);
    assignin(workspace,'moduleOutputWidth',c.output_width);
    assignin(workspace,'moduleSampleTime',c.sample_time);
    add_block('simulink/Sources/From Workspace',[model '/Input'],'VariableName','moduleInput', ...
        'Interpolate','off','SampleTime',step,'OutputAfterFinalValue','Holding final value');
    add_block('simulink/User-Defined Functions/S-Function',[model '/Candidate'], ...
        'FunctionName','sfun_module','Parameters', ...
        'moduleInputWidth,moduleStateWidth,moduleOutputWidth,moduleParameters,moduleInitialState,moduleSampleTime');
    add_block('simulink/Sinks/To Workspace',[model '/Trace'],'VariableName','moduleTrace', ...
        'SaveFormat','Timeseries','MaxDataPoints','inf','Decimation','1');
    add_line(model,'Input/1','Candidate/1'); add_line(model,'Candidate/1','Trace/1');
    save_system(model,fullfile(modelDir,[model '.slx']));
catch exception
    closeModel(model);
    rethrow(exception);
end
end

function [times,values,terminalPrestate] = unpackSimulation(result,c)
assert(isa(result,'timeseries'),'module_lab:simulation','Expected a Timeseries from the trusted sink.');
times = result.Time(:);
expectedTimes = (0:c.sample_count)'*c.sample_time;
assert(numel(times)==c.sample_count+1 && all(isfinite(times)), ...
    'module_lab:samples','Expected exactly N+1 finite fixed-step observation times.');
% Floating fixed-step time accumulation can differ by a few ulps.
timeTolerance = min(c.sample_time/8,64*eps(max(abs(expectedTimes))));
assert(all(diff(times)>0) && all(abs(times-expectedTimes)<=timeTolerance),'module_lab:samples','Fixed-step observation times differ.');
width = c.output_width+2*c.state_width+1;
data = result.Data;
assert(isnumeric(data) && isreal(data) && size(data,1)==numel(times) ...
    && numel(data)==numel(times)*width,'module_lab:shape','Unexpected S-function trace shape.');
allRows = reshape(data,numel(times),width);
values = allRows(1:end-1,:);
validateTrace(values,c.sample_count,c);
% Terminal outputs, next-state and event are deliberately unassessed.
terminalPrestate = allRows(end,c.output_width+(1:c.state_width));
assert(all(isfinite(terminalPrestate)),'module_lab:terminalPrestate', ...
    'Terminal incoming committed state must be finite.');
times = times(1:end-1);
end

function validateTrace(trace,count,c)
assert(isa(trace,'double') && isreal(trace) && isequal(size(trace),[count,c.output_width+2*c.state_width+1]) ...
    && all(isfinite(trace),'all'),'module_lab:trace','Trace must have exactly the declared finite numeric shape.');
validateEvents(trace(:,end));
end

function validateEvents(events)
assert(all(events>=0 & events<=double(intmax('uint32')) & events==floor(events)), ...
    'module_lab:events','Event flags must be exact uint32 values.');
end

function verifyPrestate(trace,c,label)
pre = trace(:,c.output_width+(1:c.state_width));
next = trace(:,c.output_width+c.state_width+(1:c.state_width));
expectedPre = [c.initial_state;next(1:end-1,:)];
delta = pre-expectedPre;
assert(all(abs(delta)<=1e-12,'all') && all(sqrt(mean(delta.^2,1))<=1e-12), ...
    'module_lab:prestate','%s trace does not expose actual committed prestate.',label);
end

function metrics = emptyMetrics
metrics = struct('name',{},'samples',{},'max_abs_error',{},'rmse',{},'event_mismatches',{},'tolerance',{});
end

function metric = compareTraces(name,actual,expected,actualEvents,expectedEvents)
assert(isequal(size(actual),size(expected)) && all(isfinite(actual),'all') && all(isfinite(expected),'all'), ...
    'module_lab:metrics','Compared numeric matrices must have the same finite shape.');
delta = actual-expected;
metric = struct('name',name,'samples',size(actual,1),'max_abs_error',max(abs(delta),[],1), ...
    'rmse',sqrt(mean(delta.^2,1)),'event_mismatches',sum(actualEvents~=expectedEvents),'tolerance',1e-12);
end

function assertMetric(metric)
assert(all(metric.max_abs_error<=1e-12) && all(metric.rmse<=1e-12) && metric.event_mismatches==0, ...
    'module_lab:equivalence','%s failed fixed maxabs/RMSE <= 1e-12 or exact-event checks.',metric.name);
end

function writeTrace(filename,trace,c,startSample)
if nargin<4, startSample=0; end
headers = [{'sample'},traceNames(c)];
writeCsv(filename,[(startSample:startSample+size(trace,1)-1)',trace],headers);
end

function writeComparison(filename,times,inputs,direct,simulated,c)
base = traceNames(c);
headers = [{'sample','time'},numberedNames('u',c.input_width),strcat('direct_',base),strcat('sfun_',base),strcat('error_',base)];
writeCsv(filename,[(0:c.sample_count-1)',times,inputs,direct,simulated,simulated-direct],headers);
end

function names = traceNames(c)
names = [numberedNames('y',c.output_width),numberedNames('x_pre',c.state_width), ...
    numberedNames('x_next',c.state_width),{'event_flags'}];
end

function names = numberedNames(prefix,count)
names = arrayfun(@(n) sprintf('%s%d',prefix,n),0:count-1,'UniformOutput',false);
end

function writeCsv(filename,matrix,headers)
fid = fopen(filename,'w'); assert(fid>=0,'module_lab:write','Cannot write %s.',filename);
cleanup = onCleanup(@() fclose(fid)); %#ok<NASGU>
fprintf(fid,'%s\n',strjoin(headers,','));
format = [repmat('%.17g,',1,size(matrix,2)-1),'%.17g\n'];
fprintf(fid,format,matrix');
end

function compileLogged(args,filename)
log = evalc('failure = compileAttempt(args);');
writeText(filename,log);
if ~isempty(failure), rethrow(failure); end
end

function failure = compileAttempt(args)
failure = [];
try
    mex(args{:});
catch exception
    failure = exception;
    fprintf(2,'%s\n',getReport(exception,'extended','hyperlinks','off'));
end
end

function failure = updateAttempt(model)
failure = [];
try
    set_param(model,'SimulationCommand','update');
    fprintf('Model update completed for %s.\n',model);
catch exception
    failure = exception;
    fprintf(2,'%s\n',getReport(exception,'extended','hyperlinks','off'));
end
end

function [simulation,failure] = simulationAttempt(model)
simulation = []; failure = [];
try
    simulation = sim(model);
    fprintf('Normal-mode simulation completed for %s.\n',model);
catch exception
    failure = exception;
    fprintf(2,'%s\n',getReport(exception,'extended','hyperlinks','off'));
end
end

function binary = verifyBinary(buildDir,name)
binary = canonicalPath(fullfile(buildDir,[name '.' mexext]));
info = dir(binary);
assert(~isempty(info) && info.bytes>0 && exist(binary,'file')==3, ...
    'module_lab:binary','Expected compiled MEX is missing: %s',binary);
rehash;
resolved = which(name);
assert(~isempty(resolved) && strcmp(canonicalPath(resolved),binary), ...
    'module_lab:binary','Only the exact newly built binary may resolve for %s.',name);
end

function configureVS2022(evidenceDir)
records = compilerSchema;
languages = {'C','C++'};
for i=1:numel(languages)
    lang = languages{i};
    available = mex.getCompilerConfigurations(lang,'Installed');
    installedInventory = compilerSchema;
    for j=1:numel(available), installedInventory(j)=compilerRecord(lang,available(j)); end %#ok<AGROW>
    writeJson(fullfile(evidenceDir,['installed-compilers-' strrep(lang,'+','p') '.json']),installedInventory);
    expected = ['Microsoft Visual C++ 2022 (' lang ')'];
    matches = find(strcmp({available.Name},expected) | strcmp({available.Name},'Microsoft Visual C++ 2022'));
    assert(numel(matches)==1,'module_lab:compiler','Expected exactly one installed VS2022 %s compiler.',lang);
    chosen = available(matches);
    assert(contains(chosen.Manufacturer,'Microsoft') && isfile(chosen.MexOpt), ...
        'module_lab:compiler','Unexpected VS2022 manufacturer or missing options file.');
    mex(['-setup:' chosen.MexOpt],lang);
    selected = mex.getCompilerConfigurations(lang,'Selected');
    assert(numel(selected)==1 && strcmp(selected.Name,chosen.Name) && strcmp(selected.Location,chosen.Location), ...
        'module_lab:compiler','VS2022 selection did not take effect for %s.',lang);
    records(i) = compilerRecord(lang,selected); %#ok<AGROW>
    writeJson(fullfile(evidenceDir,'compiler-configurations.json'),records);
    fprintf('Selected %s: %s (%s) at %s\n',lang,selected.Name,selected.Version,selected.Location);
end
end

function records = compilerSchema
records = struct('language',{},'name',{},'short_name',{},'manufacturer',{},'version',{},'location',{},'options_file',{});
end

function record = compilerRecord(lang,compiler)
record = struct('language',lang,'name',compiler.Name,'short_name',compiler.ShortName, ...
    'manufacturer',compiler.Manufacturer,'version',compiler.Version,'location',compiler.Location,'options_file',compiler.MexOpt);
end

function ledger = initialLedger
names = {'CLAUDE_GENERATION','MODEL_REQUESTED','MODEL_ACTUAL','CPP_CORE_BUILD','CPP_UNIT_TEST', ...
    'DETERMINISTIC_REPLAY','MATLAB_RELEASE','MEX_COMPILER','SFUNCTION_BUILD','SFUNCTION_LOAD', ...
    'SIMULINK_HARNESS','CPP_SFUNCTION_EQUIVALENCE','PUBLIC_MATLAB_MODULE_ACCEPTANCE','PRIVATE_PROMOTION', ...
    'PRIVATE_PR','PUBLIC_BRANCH_CLEANUP','MAIN_CODEX_INTEGRATION','SYSTEM_VALIDATION','FORMAL_PROJECT_PROMOTION', ...
    'MATLAB_STARTUP','MODULE_RUNTIME_INPUTS','SIMULINK_AVAILABILITY','DIRECT_CORE_MEX_BUILD','DIRECT_CORE_MEX', ...
    'DIRECT_DETERMINISTIC_REPLAY','DIRECT_ORACLE_EQUIVALENCE','MODULE_RUNTIME_DIAGNOSTICS','PUBLIC_MODULE_RUNTIME'};
ledger = repmat(struct('name','','status','NOT_RUN','detail','Not observed by this MATLAB runtime.','observed_at_utc',''),1,numel(names));
for k=1:numel(names), ledger(k).name=names{k}; end
index = find(strcmp({ledger.name},'PUBLIC_MATLAB_MODULE_ACCEPTANCE'));
ledger(index).status = 'WAITING';
ledger(index).detail = 'Needs private evidence; a public runtime PASS is insufficient for acceptance or promotion.';
end

function ledger = recordGate(ledger,name,status,detail,evidenceDir)
index = find(strcmp({ledger.name},name));
assert(isscalar(index),'module_lab:ledger','Unknown or repeated ledger field: %s',name);
ledger(index).status=status; ledger(index).detail=detail; ledger(index).observed_at_utc=utcNow;
writeJson(fullfile(evidenceDir,'matlab-ledger.json'),ledger);
fprintf('%s %s: %s\n',name,status,detail);
end

function receipt = initialReceipt
receipt = struct('schema','public-matlab-module-runtime-receipt-v1','status','RUNNING', ...
    'started_at_utc',utcNow,'completed_at_utc','','module_sha','','infrastructure_sha','', ...
    'admission_json_verbatim','','source_manifest_json_verbatim','','admission',struct(), ...
    'source_manifest',struct(),'source_dir','','trusted_runner','','module_id','','module_version','', ...
    'sample_count',0,'sample_time',0,'candidate_matlab_helpers_executed',false, ...
    'candidate_test_sources_invoked_by_runner',false,'candidate_wrapper_execution','NOT_RUN', ...
    'native_code_warning','Candidate native code requires upstream review/admission and executes in-process. This runner is not a security sandbox.', ...
    'helper_policy','Candidate matlab/build_mex.m and matlab/run_harness.m are unexecuted proposals. Only trusted MATLAB constructs/builds the harness.', ...
    'source_binding_policy','Parent validates the exact 11-file snapshot before and after runtime; admission binds module_sha separately from infrastructure_sha.', ...
    'terminal_observation_policy','Repeat inputs(end,:); assess only actual incoming committed state. Terminal output, next-state and event are unassessed and outside frozen sample_count/oracle.', ...
    'acceptance','WAITING: needs private evidence','failure_identifier','','failure_message','','ledger',initialLedger);
end

function validateInteger(value,minimum,maximum,name)
assert(isnumeric(value) && isreal(value) && isscalar(value) && isfinite(value) ...
    && value==floor(value) && value>=minimum && value<=maximum, ...
    'module_lab:contract','%s must be an integer in [%d,%d].',name,minimum,maximum);
end

function validateUnits(units,width)
if isstring(units), units=cellstr(units); end
assert(iscell(units) && numel(units)==width,'module_lab:contract','Unit metadata width mismatch.');
for k=1:numel(units)
    assert(isText(units{k}) && ~isempty(units{k}) && numel(units{k})<=32, ...
        'module_lab:contract','Units must be nonempty strings at most 32 characters.');
end
end

function yes = isText(value)
yes = ischar(value) && (isrow(value) || isempty(value));
end

function yes = isSha(value)
yes = isText(value) && ~isempty(regexp(value,'^[0-9a-f]{40}$','once'));
end

function yes = isWithin(child,parent)
child=lower(canonicalPath(child)); parent=lower(canonicalPath(parent));
yes=strcmp(child,parent) || startsWith(child,[parent filesep]);
end

function result = canonicalPath(value)
file = javaObject('java.io.File',value);
result = char(file.getCanonicalPath());
end

function text = utcNow
text = char(datetime('now','TimeZone','UTC','Format',"yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"));
end

function writeArtifactIndex(evidenceDir)
files = dir(fullfile(evidenceDir,'**','*')); files = files(~[files.isdir]);
manifest = struct('relative_path',{},'bytes',{});
for k=1:numel(files)
    absolute = fullfile(files(k).folder,files(k).name);
    if strcmp(files(k).name,'matlab-artifact-index.json'), continue; end
    manifest(end+1)=struct('relative_path',strrep(absolute,[evidenceDir filesep],''),'bytes',files(k).bytes); %#ok<AGROW>
end
writeJson(fullfile(evidenceDir,'matlab-artifact-index.json'),manifest);
end

function writeJson(filename,value)
writeText(filename,jsonencode(value,'PrettyPrint',true));
end

function writeText(filename,value)
fid=fopen(filename,'w'); assert(fid>=0,'module_lab:write','Cannot write %s.',filename);
cleanup=onCleanup(@() fclose(fid)); %#ok<NASGU>
fprintf(fid,'%s\n',value);
end

function closeModel(model)
if bdIsLoaded(model), close_system(model,0); end
end

function restoreSession(oldPath,oldDir)
diary off
cd(oldDir); path(oldPath);
end
