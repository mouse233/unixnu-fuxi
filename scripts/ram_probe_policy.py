"""Stage contracts and compiled-init evidence for isolated RAM probes."""
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_stage_policy(pins, user_entry, main_entry, progress):
    require(not main_entry or user_entry, 'main entry requires user entry')
    require(not progress or main_entry, 'progress requires main entry')
    require(pins.get('physical_storage_allowed') is False, 'physical storage must remain disabled')
    require(pins.get('start_userland_allowed') is user_entry, 'userland policy disagrees with archive mode')
    require(pins.get('stop_before_first_user_svc', False) is (user_entry and not main_entry),
            'first-SVC stop policy disagrees with archive mode')
    require(pins.get('stop_at_launchd_main', False) is (main_entry and not progress),
            'main stop policy disagrees with archive mode')
    require(pins.get('continue_after_main', False) is progress, 'main continuation policy mismatch')
    require(pins.get('follow_process_creation', False) is progress, 'process follow policy mismatch')
    require(pins.get('block_process_creation', True) is (not progress), 'process creation policy mismatch')
    return 'progress' if progress else 'main-entry' if main_entry else 'user-entry' if user_entry else 'mount'


def llvm_tool(name):
    found = shutil.which(name)
    fallback = Path('/opt/homebrew/opt/llvm/bin') / name
    require(found or fallback.is_file(), 'missing ' + name)
    return found or str(fallback)


def inspect_compiled_init(out):
    directory = out / 'work/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    obj = directory / 'bsd/DEVELOPMENT/kern_exec.o'
    record = json.loads(obj.with_suffix('.o.json').read_text().strip().rstrip(','))
    arguments = record['arguments']
    for name in ('XNU_RAM_ROOT_PROBE', 'XNU_RAM_USER_ENTRY'):
        require(arguments.count(name + '=1') + arguments.count('-D' + name + '=1') == 1,
                'compiled kern_exec lacks ' + name + '=1; source patch alone is insufficient')
    symbols = subprocess.check_output([llvm_tool('llvm-nm'), str(obj)], text=True)
    for name in ('_fuxi_ram_system_user_init_path', '_fuxi_ram_user_exec_begin', '_fuxi_ram_user_exec_ready'):
        require(name in symbols, 'compiled kern_exec lacks reference to ' + name)
    disassembly = subprocess.check_output([llvm_tool('llvm-objdump'), '--macho', '--disassemble',
        '--no-show-raw-insn', '--symbolize-operands', '--disassemble-symbols=_load_init_program', str(obj)], text=True)
    require('_fuxi_ram_system_user_init_path' in disassembly,
            'load_init_program does not reference the system-init override')
    return {'kern_exec_object_sha256': sha(obj), 'kern_exec_compile_record_sha256': sha(obj.with_suffix('.o.json'))}


def inspect_system_probe(out):
    """Check metadata against the actual compiled archive and init object."""
    pins = json.loads((out / 'pins.json').read_text())
    board = json.loads((out / 'board-effective.json').read_text())
    entries = [shlex.split(x) for x in board['kernel']['archives']
               if shlex.split(x)[0] == 'build-ram-root-probe.py']
    require(len(entries) == 1, 'need exactly one RAM probe archive')
    entry = entries[0]
    user, main, progress = (flag in entry for flag in ('--user-entry', '--main-entry', '--progress'))
    require(user and '--system-objects' in entry and '--system-user-entry' in entry and not progress,
            'system entry must use its bounded archive profile')
    require(Path(entry[entry.index('--pins') + 1]).resolve() == out / 'pins.json', 'probe pins belong to another candidate')
    stage = validate_stage_policy(pins, user, main, progress)
    config = (out / 'config').read_text().splitlines()
    for name, enabled in [('XNU_RAM_ROOT_PROBE', True), ('XNU_RAM_USER_ENTRY', user),
                          ('XNU_RAM_MAIN_PROBE', main), ('XNU_RAM_PROGRESS', progress)]:
        definitions = [x for x in config if x.startswith('CONFIG_' + name + '=')]
        require(definitions == ['CONFIG_' + name + '=' + ('y' if enabled else 'n')], 'config mismatch: ' + name)
    args = board['payload']['kernel_args']
    require([x for x in args if x.startswith('ram-root-probe=')] == ['ram-root-probe=' + ('3' if main else '2')],
            'root probe selector disagrees with archive')
    require(args.count('rd=md0') == 1, 'system entry must report md0 as its root')
    build = out / 'work/tree/build/ram-root-probe'
    report = json.loads((build / 'build-report.json').read_text())
    require(report['pins'] == pins, 'compiled probe pins differ from candidate pins')
    for key, expected in [('user_entry', user), ('main_entry', main), ('progress', progress),
                          ('system_objects', True), ('system_user_entry', True)]:
        require(report.get(key) is expected, 'compiled probe mode mismatch: ' + key)
    require(report['archive_sha256'] == sha(build / 'libRamRootProbe.a'), 'probe archive digest mismatch')
    device = Path(__file__).resolve().parents[1]
    for key, name in [('source_sha256', 'RamRootProbe.c'), ('system_source_sha256', 'RamSystemProbe.c'),
                      ('main_source_sha256', 'RamLaunchdProbe.c')]:
        if key == 'main_source_sha256' and not main:
            continue
        require(sha(device / 'package' / name) == report[key] == sha(out / 'work/tree/kits/sheng' / name),
                'probe source binding mismatch: ' + name)
    stamp = (out / 'work/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/.native-aes-link-input').read_text()
    require('1:' + report['archive_sha256'] in stamp, 'kernel link did not consume the probe archive')
    evidence = inspect_compiled_init(out)
    kernel_symbols = subprocess.check_output([llvm_tool('llvm-nm'), str(out / 'work/kernel/kernel.macho')], text=True)
    for name in ('_fuxi_ram_system_user_init_path', '_fuxi_ram_user_exec_begin'):
        require(any(line.split()[-1:] == [name] and ' U ' not in line for line in kernel_symbols.splitlines()),
                'delivered kernel lacks defined ' + name)
    return dict(evidence, stage='system-' + stage, root='md0', exec_path='/mnt1/sbin/launchd',
                probe_archive_sha256=report['archive_sha256'], contract_verified=True)
