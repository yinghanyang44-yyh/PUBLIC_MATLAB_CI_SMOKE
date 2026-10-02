"""Bounded public module contract, exact source inventory and immutable oracle validation.

These checks verify shape and source identity. They do not sandbox native code;
owner review of the exact published source remains required.
"""
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import stat

MAX_BYTES = 1024 * 1024
SOURCE_MAX_BYTES = 256 * 1024

PROFILE = 'cpp-module-v1'

FILES = frozenset({'contract/contract.json','include/module.h','src/module.cpp',
    'sfunction/sfun_module.cpp','tests/test_module.cpp','tests/test_vectors.csv',
    'expected_outputs.csv','matlab/build_mex.m','matlab/run_harness.m','README.md','HANDOFF.md'})

ORACLE_FILES = frozenset({'contract/contract.json','tests/test_vectors.csv','expected_outputs.csv'})

def digest(data):
    return hashlib.sha256(data).hexdigest()

def exact_keys(value, keys):
    if not isinstance(value,dict) or set(value)!=set(keys):
        raise ValueError('Unexpected/missing C++ profile keys')

HEADER = '''#ifndef CPP_MODULE_V1_H
#define CPP_MODULE_V1_H
#include <cstdint>
extern "C" void module_step_v1(const double* input, const double* state,
    const double* parameters, double* output, double* next_state,
    std::uint32_t* event_flags) noexcept;
#endif
'''

CONTRACT_KEYS = {'schema','module_id','version','input_width','state_width','output_width',
    'parameters','initial_state','sample_time','sample_count','input_units','state_units','output_units',
    'reset_input_index','reset_timing','output_timing','event_flag_bits'}

def finite(value):
    return type(value) in (int,float) and math.isfinite(value)

def validate_contract(c):
    exact_keys(c,CONTRACT_KEYS)
    if c['schema']!=PROFILE or not isinstance(c['module_id'],str) or not re.fullmatch('[a-z][a-z0-9_-]{0,47}',c['module_id']):
        raise ValueError('Invalid C++ module identity/schema')
    if not isinstance(c['version'],str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',c['version']):
        raise ValueError('Numeric semantic version required')
    for kind in ('input','state','output'):
        width=c[kind+'_width'];units=c[kind+'_units']
        if type(width) is not int or not 1<=width<=16:raise ValueError('C++ width outside1..16')
        if not isinstance(units,list) or len(units)!=width or not all(isinstance(x,str) and 0<len(x)<=32 and all(ord(ch)>=32 for ch in x) for x in units):
            raise ValueError('Explicit matching units required')
    if not isinstance(c['parameters'],list) or len(c['parameters'])>16 or not all(finite(x) for x in c['parameters']):
        raise ValueError('Finite parameter vector required')
    if not isinstance(c['initial_state'],list) or len(c['initial_state'])!=c['state_width'] or not all(finite(x) for x in c['initial_state']):
        raise ValueError('Explicit finite initial state required')
    if not finite(c['sample_time']) or not 0<c['sample_time']<=1000:raise ValueError('Invalid explicit sample time')
    if type(c['sample_count']) is not int or not 1<=c['sample_count']<=2000:raise ValueError('Sample count outside1..2000')
    reset=c['reset_input_index']
    if reset is not None and (type(reset) is not int or not 0<=reset<c['input_width']):raise ValueError('Reset input index invalid')
    if c['reset_timing']!=('none' if reset is None else 'before_step'):raise ValueError('Reset timing must be explicit')
    if c['output_timing']!='output_and_next_from_pre_state':raise ValueError('Unsupported output/state timing')
    bits=c['event_flag_bits']
    if not isinstance(bits,dict) or not all(isinstance(k,str) and re.fullmatch(r'(0|[1-9]|[12][0-9]|3[01])',k)
        and isinstance(v,str) and 0<len(v)<=80 and all(ord(ch)>=32 for ch in v) for k,v in bits.items()):
        raise ValueError('Explicit event bit meanings required')
    return c

def parse_csv(data, expected_header, count, event_mask=None):
    reader=csv.reader(io.StringIO(data.decode('utf-8')),strict=True)
    rows=list(reader)
    if not rows or rows[0]!=expected_header or len(rows)!=count+1:raise ValueError('CSV header/count mismatch')
    result=[]
    for index,row in enumerate(rows[1:]):
        if len(row)!=len(expected_header) or row[0]!=str(index):raise ValueError('CSV row/sample identity mismatch')
        values=[]
        for position,value in enumerate(row[1:],1):
            if event_mask is not None and position==len(row)-1:
                if not re.fullmatch(r'(0|[1-9][0-9]*)',value):raise ValueError('Event flags must be unsigned decimal')
                event=int(value)
                if event>4294967295 or event & ~event_mask:raise ValueError('Undeclared/out-of-range event bit')
                values.append(event)
            else:
                if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',value):
                    raise ValueError('CSV numbers must use ASCII decimal/scientific notation without whitespace or underscores')
                number=float(value)
                if not math.isfinite(number):raise ValueError('Nonfinite vector/oracle value')
                values.append(number)
        result.append(values)
    return result

def vector_header(c):return ['sample']+['u'+str(i) for i in range(c['input_width'])]

def expected_header(c):return ['sample']+['y'+str(i) for i in range(c['output_width'])]+['x_next'+str(i) for i in range(c['state_width'])]+['event_flags']

def event_mask(c):return sum(1<<int(k) for k in c['event_flag_bits'])

def strict_json(data):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    def constant(value):
        raise ValueError('Nonfinite JSON number')
    return json.loads(data,object_pairs_hook=pairs,parse_constant=constant)


def bounded_text(data, relative):
    limit = MAX_BYTES if relative.endswith('.csv') else SOURCE_MAX_BYTES
    if relative=='contract/contract.json': limit=16384
    if not 0<len(data)<=limit or b'\0' in data: raise ValueError('Empty, oversized or binary source file')
    text=data.decode('utf-8')
    if relative.endswith(('.cpp','.h')):
        # Reject extra/transitive local source and exotic macro include targets.
        allowed={'module.h','simstruc.h','simulink.c','mex.h','cstdint','cstddef','cmath',
                 'algorithm','array','limits','type_traits','utility','cassert','iostream',
                 'string','vector','iomanip','cstdlib','cstdio','cstring'}
        includes=re.findall(r'^\s*#\s*include\s+([^\r\n]+)',text,re.M)
        for include in includes:
            match=re.match(r'[<"]([^>"]+)[>"]\s*(?://.*)?$',include)
            if not match or match.group(1) not in allowed:
                raise ValueError('Unapproved transitive or macro include')
    return text


def validate_package_bytes(files, frozen_inputs):
    exact_keys(files,FILES);exact_keys(frozen_inputs,ORACLE_FILES)
    if sum(len(data) for data in files.values())>MAX_BYTES: raise ValueError('Package exceeds one MiB')
    for relative,data in files.items():bounded_text(data,relative)
    if files['include/module.h'].decode()!=HEADER: raise ValueError('Canonical ABI header mismatch')
    for relative in ORACLE_FILES:
        expected=frozen_inputs[relative]
        if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected): raise ValueError('Invalid frozen input SHA256')
        if digest(files[relative])!=expected: raise ValueError('Candidate changed a supervisor-frozen input')
    core=files['src/module.cpp'].decode()
    if re.search(r'(?i)(simstruc\.h|mex\.h|matrix\.h|\bmex[A-Za-z_]|\bSimStruct\b)',core):
        raise ValueError('Core is not independent of MATLAB/Simulink')
    if 'module_step_v1' not in core: raise ValueError('Required module ABI missing')
    wrapper=files['sfunction/sfun_module.cpp'].decode()
    if not re.search(r'^\s*#\s*define\s+S_FUNCTION_NAME\s+sfun_module\s*$',wrapper,re.M):
        raise ValueError('Candidate S-function name must be sfun_module')
    if not re.search(r'^\s*#\s*define\s+S_FUNCTION_LEVEL\s+2\s*$',wrapper,re.M):
        raise ValueError('Candidate wrapper must be a Level-2 S-function')
    if 'module_step_v1' not in wrapper: raise ValueError('Candidate wrapper does not call the fixed module ABI')
    contract=validate_contract(strict_json(files['contract/contract.json']))
    vectors=parse_csv(files['tests/test_vectors.csv'],vector_header(contract),contract['sample_count'])
    parse_csv(files['expected_outputs.csv'],expected_header(contract),contract['sample_count'],event_mask(contract))
    index=contract['reset_input_index']
    if index is not None and any(row[index] not in (0,1) for row in vectors): raise ValueError('Reset channel must be zero or one')
    return contract


def read_snapshot(root):
    root=Path(root)
    if not root.is_dir() or root.is_symlink(): raise ValueError('Snapshot must be a real directory')
    files={}
    for path in root.rglob('*'):
        relative=path.relative_to(root).as_posix();info=path.lstat()
        if stat.S_ISDIR(info.st_mode):
            if not any(name.startswith(relative+'/') for name in FILES): raise ValueError('Unexpected source directory')
            continue
        if relative not in FILES or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o111:
            raise ValueError('Unexpected, linked, executable or nonregular source')
        files[relative]=path.read_bytes()
    exact_keys(files,FILES)
    return files
