% Generic public MATLAB/Simulink smoke; no private vehicle/controller data.
assert(strcmp(version('-release'), '2025a'), 'Expected MATLAB R2025a');
assert(~isempty(ver('simulink')), 'Simulink must be installed');

x = [0.25 0.50 0.75 1.00];
assert(abs(sum(x) - 2.5) < 1e-12, 'MATLAB arithmetic smoke failed');

model = 'upc_public_circleci_gain_smoke';
if bdIsLoaded(model)
    close_system(model, 0);
end
new_system(model);
cleanupModel = onCleanup(@() close_system(model, 0));
add_block('simulink/Sources/Constant', [model '/Constant'], 'Value', '3.5');
add_block('simulink/Math Operations/Gain', [model '/Gain'], 'Gain', '2');
add_block('simulink/Sinks/To Workspace', [model '/Output'], ...
    'VariableName', 'ci_public_signal', 'SaveFormat', 'Array');
add_line(model, 'Constant/1', 'Gain/1');
add_line(model, 'Gain/1', 'Output/1');
simOutput = sim(model, 'StopTime', '0.02', 'ReturnWorkspaceOutputs', 'on');
signalValues = simOutput.get('ci_public_signal');
assert(~isempty(signalValues), 'Simulink returned no values');
assert(abs(double(signalValues(end)) - 7.0) < 1e-9, ...
    'Simulink gain differs from expected value');

folder = fullfile('evidence', 'circleci');
if ~exist(folder, 'dir')
    mkdir(folder);
end
report = struct('status', 'PASS', ...
    'suite', 'generic_public_matlab_simulink_smoke', ...
    'release', version('-release'), ...
    'output_value', double(signalValues(end)), ...
    'claim', 'software_smoke_only_not_research_validation');
fid = fopen(fullfile(folder, 'matlab-smoke.json'), 'w');
assert(fid > 0, 'Unable to create evidence');
cleanupFile = onCleanup(@() fclose(fid));
fprintf(fid, '%s\n', jsonencode(report));
clear cleanupFile;
clear cleanupModel;
disp('PUBLIC_MATLAB_SIMULINK_SMOKE_PASS');
