# fuxi RAM-root bring-up

2026-10-23：已审计 Command Code 会话并构建、临时启动 12 GiB 抽样内存诊断候选，
真机画面待核验。路线结论、修正项和复现入口见 [commandcode-audit.md](commandcode-audit.md)。

## Isolated RAM experiment branch

Use `feat/ramdisk-test` in all three repositories (`unixnu-source`,
`UniXNU-fuxi`, `uefi-fuxi`) under the separate `ramdisk-test/` workspace.
The original working directories remain on their existing branches, with their
uncommitted work preserved. The experiment's module dependencies have independent
local checkouts at the parent repositories' pinned revisions.

The builder supports three stages:

| Stage | Stop point | Hardware evidence |
| --- | --- | --- |
| `mount` | md0/APFS read-only mount and complete image/launchd hashes | Earlier source version passed |
| `user-entry` | Original PID 1's first EL0 SVC, before dispatch | Earlier source version passed |
| `main-entry` | Hardware breakpoint at original launchd LC_MAIN + 4 | Experimental; not phone-tested |

`main-entry` uses a thread-local EL0 hardware breakpoint, without modifying user
program bytes. Process creation, exec, mount/unmount, reboot and ptrace are blocked
at the decoded Unix syscall boundary, including indirect calls. A 30-second
diagnostic deadline and a 2,048-SVC limit bound the observation. The new mode does
not claim that launchd main or full iPadOS already works.

Use this branch's base checkout and device checkout together. Example from the
device repository:

```sh
python3 scripts/build-fuxi-ram-diagnostic.py \
  --base ../unixnu-source --inputs ../inputs \
  --ipsw /path/to/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw \
  --uefi ../uefi-fuxi --unique-chip-id YOUR_HEX_SEED \
  --stage main-entry --plan-only
```

Each stage owns its cache under `out/ram-<stage>/cache/`, plus its own config,
logs and products. `build-layout.json` records these paths. The builder never
flashes a partition or boots a phone. Remove `--plan-only` only when resuming
the full build; the previous full build was stopped at the user's request.

The branch preparation checks passed: 20 user-entry C guard cases, 35 main-entry
C guard cases, and all three profile plans. Original Apple image and trust inputs
were re-extracted and hash-verified locally. No full kernel rebuild or phone
test was performed while creating this branch.

The UEFI branch captures the 192 MiB payload bounds and boot-image packager.
Its firmware dependency submodules and the original Mu_Basecore working edits
were not copied or rebuilt. Existing tested firmware artifacts remain local
inputs; rebuilding firmware requires preparing those dependencies separately.

Two bounded diagnostics passed on a Xiaomi 13 (fuxi) on 2026-10-23. They use the
unchanged 176 MiB RestoreRamDisk from the iPad7,1 iPadOS 17.7.11 (21H461) IPSW.
Android userdata is not the root device. No partition was flashed or erased and
the Android boot slot was not changed.

The historical `codex/fuxi-ramroot-bringup` branches preserve the exact source
checkouts used for those tests (base UniXNU commit `97de9e7` plus diagnostics).
The fork `main` branches merge that work with the forks' newer upstream commits.
Use both forks together. The integration preserves the newer staged-SystemOS
patch ordering, and skips Stage A cross-archive stub injection when UFS or the
bounded RAM-root profiles explicitly force-load the stub archive once.

Post-merge checks on `main` passed: all 57 XNU/NativePrelink patches apply to a
pristine source tree, the three bootstrap regression tests and 20 diagnostic C
guard cases pass, and the strict stub build leaves the other 11 archives
untouched. The historical phone observations below remain tied to their tested
commits; a post-merge full kernel build and phone run have not been repeated.

## Observed results

Mount-only diagnostic (`ram-root-probe=1`):

```text
RAM_ROOT PASS
root=md0 filesystem=apfs readonly=1
media=176MiB SHA256=OK
launchd=561856 bytes SHA256=OK
userland not started
```

The entire RAM disk and `/sbin/launchd` were hashed by the kernel, then the
diagnostic intentionally stopped before userland.

User-entry diagnostic (`ram-root-probe=2`, `XNU_RAM_USER_ENTRY=1`):

```text
RAM_USER_ENTRY PASS
root=md0 apfs readonly=1
original launchd exec=OK
PID1 executed at EL0; SVC trapped
pc=100f60fb4 cpsr=0 esr=56000080
x16=74 instruction=d4001001
stopped BEFORE first syscall dispatch
recovery programs not started
```

The original launchd completed the kernel exec path and PID 1 executed EL0
instructions. The first SVC was mprotect(74), intercepted before dispatch. The
original dyld contains a unique `mov x16,74; svc #0x80` sequence at offset
`0x3afac`, consistent with this observation. The checkpoint does not establish
that dyld initialization finished, launchd main executed, services started, or
the iPadOS UI works.

System-object probes still use md0 as the root; `/mnt1/sbin/launchd` is an
independent executable source. A system mount flag alone does not establish
that exec selected that file. The v32/v33 `kern_exec.o` omitted
`XNU_RAM_ROOT_PROBE`, compiling out the intended init-path override. Therefore
v32's system-source claim is withdrawn (its PID1/EL0/SVC observation remains
valid), and v33's final photo shows an argv rejection before EL0/main. See the
append-only correction in `usb-bulk.md` and v33 attempt-02's object audit.
The v34 fix defines both RAM macros for `kern_exec`, verifies the selected init
path before releasing its replacement thread, and checks the compiled object,
archive, stage policy and delivered image. Its PASS screens report `root=md0`
and `exec=/mnt1/sbin/launchd` separately. It is still a bounded main checkpoint,
not a system-root switch or full iPadOS startup.

The optional v35 launch observer records only existing `vfs_flags` and
`VNOP_IOCTL` returns during the original AMFI callback, scoped to the current
PID1 thread and the exact target vnode/mount. It issues no extra authorization
ioctl. `XNU_RAM_LAUNCH_OBSERVE`, the archive option, pins and boot selector must
all opt in; otherwise it stays disabled. The original policy error, reason and
SIGKILL remain intact. Its appended `RAM_LC_OBS` reports missing observations
as `unseen`. `check-ram-launch-observer.py` checks actual production C and XNU
wrappers in both modes, including unchanged return values and call counts.

Earlier, a separate UFS probe read the GPT and the first userdata block. Its
purpose was to establish real storage reads, not to authorize replacing userdata.
The normal board defaults remain a read-only UFS diagnostic; RAM diagnostics
use isolated effective configs and do not rewrite `board.toml` or the usual
`.config`.

## Inputs and trust

`pins.json` specifies the exact original disk and launchd hashes. The checked
RestoreRamDisk member is `044-75545-038.dmg`, an IM4P containing raw APFS.

- Raw RAM disk: 184,549,376 bytes, SHA256
  `aadb59ed21a8f0218a348f070913ab5a7679805c58408534d811170871f1fc05`.
- launchd: 561,856 bytes, SHA256
  `8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25`.
- dyld: 937,056 bytes, SHA256
  `f8df3c58fae6e409db740df22c98aa699aee110b73523541a63f1bd5e23cf705`.

Both executables' CDHashes are already present in the unchanged original
StaticTrustCache. User-entry mode loads only the original StaticTrustCache and
Cryptex1,SystemTrustCache modules, bundled with SHA256
`7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7`.
No custom code hashes are added and no AMFI decision is replaced.

The source-only input preparation command verifies each IM4P against its
BuildManifest digest, unwraps it and checks the pinned payload. It does not
mount images or execute any recovered program:

```sh
python3 scripts/prepare-fuxi-ram-inputs.py \
  --ipsw /path/to/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw \
  --output out/ram-inputs
```

The IPSW and extracted Apple binaries are local inputs and are not distributed
in these repositories.

## Building

Use the normal macOS/Xcode and Homebrew prerequisites documented by UniXNU.
Run from this device repository with the matching base checkout and fuxi UEFI
checkout. The device's platform seed is an explicit local input:

```sh
python3 scripts/build-fuxi-ram-diagnostic.py \
  --base ../UniXNU --inputs out/ram-inputs \
  --ipsw /path/to/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw \
  --uefi ../uefi-fuxi --unique-chip-id YOUR_HEX_SEED --stage mount
```

Use `--stage user-entry` for the second diagnostic or experimental `--stage
main-entry` for the hardware main checkpoint. `--plan-only` writes the effective
board, pins, config and build layout without compiling. Each profile has its own
build tree, final products and logs under `out/ram-<stage>/`.

The payload is a 192 MiB FAT volume. DarwinBoot expands DBRDISK1 into the original
176 MiB bytes and populates `/chosen/memory-map/RAMDisk`, rather than handing off
a `PackedRAMDisk`. XNU uses its builtin md0 driver. NativeUfs and NativeRamBlock
are disabled in both RAM profiles.

The tested firmware also needs the larger payload bounds. The source delta
relative to `mouse233/uefi-fuxi` commit `6cc1ec64` is included as
`uefi-payload-192m.patch`. It is a zero-context patch; apply with
`git apply --unidiff-zero` in that firmware checkout, then rebuild firmware using
its existing instructions. Other firmware dependencies retain that checkout's
normal patches. The two new forks do not contain firmware binaries.

The source-only packager preserves the original boot v3 shim/firmware/DTB and
header fields, replaces a dummy ramdisk with the FAT payload, stamps FUXIPAYL,
and emits a hash manifest:

```sh
python3 scripts/package-fuxi-xnu-boot.py \
  --boot /path/to/dummy-ramdisk-firmware-boot.img \
  --payload out/ram-user-entry/work/payload/payload.img \
  --size-mib 192 --output out/fuxi-ram-user.img
```

The diagnostics are intended for temporary `fastboot boot` on the identified,
unlocked fuxi device. The scripts in this repository do not flash a phone.
The displayed panic is the expected bounded stop, not a request to let the
restore services run.

## Validation

- Kernel and prelink builds completed; all 30 original Apple component
  executable hashes stayed unchanged.
- Diagnostic code compiled as ordinary ARM64 using actual XNU compile records
  with `-Wall -Wextra -Werror`.
- Actual DarwinBoot C decoding reconstructed the exact 176 MiB image hash.
- Final boot FAT contents were read back and matched the kernel, ADT, trust
  cache, RAM disk and boot-argument manifests.
- Disassembly confirmed the real exec-result callback and the non-returning
  SVC checkpoint before `handle_svc`.
- Kernel bootstrap regression tests passed (3 cases).
- Actual diagnostic C passed 20 host stop/refusal cases with real SHA256.

To rerun the guard tests, provide the original launchd copied from a read-only
mount of the pinned RAM disk; the tests never execute it:

```sh
python3 scripts/check-ram-root-guards.py \
  --ramdisk out/ram-inputs/restore.dmg --launchd /path/to/original/launchd
```

Historical final boot-image hashes (the ADT contains a fresh random seed, so
rebuilds need not be byte-identical):

- Mount: `3643e73de53a5cb29acfb07e705ab67623866c310a2e3fb7e1d85a49f1f824c2`.
- User entry: `c96b5449ebab9dbef67223f52dffabf4e5fc117cee591eb94246f10572b1bbd7`.


## Continuous progress profile (experimental)

`--stage progress` combines the root, PID1 EL0 and original launchd main
observations in one temporary boot. Unlike `main-entry`, its hardware breakpoint
is removed after LC_MAIN+4 and PID1 continues. Other PID1 threads are allowed during dyld and launchd initialization; only the original main thread owns the breakpoint.
The Apple executable bytes and original trust modules remain unchanged.

The profile records up to eight recent decoded UNIX calls, their first argument,
raw return word and errno, the most recent open/exec/spawn path, the most recent
stat64 path and the most recent write payload (printable, truncated), plus the
last eight exec/spawn entries with each child's return value. Results are matched
by thread and process. `err=-9999` means dispatch has not returned. A best-effort
1024-byte RAM snapshot is printed before an ordinary panic, including panics
outside the probe. A contending recorder skips an event rather than blocking;
`dropped` reports this. No trace is written to physical storage.

A verified main checkpoint is a recorded milestone, not a stop. After main the
probe follows process creation: launchd and the children it spawns keep running,
so their syscalls, exec/spawn results and paths are recorded. A `mount` whose
filesystem type is exactly `tmpfs` is allowed because it is memory-backed; stops
remain on invalid context/hash/entry bytes, kernel faults, 90 seconds or 131072
SVCs, and before every other mount, unmount/`__mac_mount`/fmount, ptrace or
reboot dispatch.
Indirect `syscall(0, ...)` is decoded by XNU before the guard and its argument
register offset is accounted for. No physical storage driver is linked, so
followed children cannot write to a partition.

The final panic screen can be tested with the actual candidate and actual C
probe snapshot using `check-xnu-panic-screen.py --candidate KERNEL --ram-progress-file
out/ram-progress-guard-tests/trace-fixture.txt`. This executes the renderer in
QEMU synthetic memory; it does not establish a successful XNU or phone boot.
`check-ram-launchd-guards.py --progress` also checks continuation, clearing the
breakpoint, per-thread results, path truncation, bounded logs and guards after
main. The old mount, user-entry and main-entry profiles remain available.

The isolated build additionally needs the local AMFI/PMGR audit candidate and
its JSON metadata under `work/tree/build/native-prelink/`, and the corresponding
original decompressed kernelcache under `.cache/firmware/kernelcache.macho/`.
These are ABI reference inputs only. `out/ram-progress/audit-inputs.json` records
the supplied local files. The final kernel is rebuilt from the RAM branch.


### Exec replacement-thread correction

The first progress phone attempt stopped in the diagnostic before dyld because
it inspected the retiring exec caller's registers. In this pinned XNU,
`__mac_execve` creates a new thread and task with `cloneproc(CLONEPROC_EXEC)`.
The old caller after `execve()` is not the thread that enters dyld.

The ready hook now receives `imgp->ip_new_thread` while `TCRW_CLEAR_FINAL_WAIT`
still holds it. It reads that thread's saved registers, switches to the target
task map for bounded stack/code reads, restores the old map, and arms the target
thread before its final wait is released. The later exec-result callback checks
errors and ready status only. Both argv layouts (`launchd` and `launchd -s`)
are checked, including argv[0], the optional `-s`, and argv termination.

Regression fixtures keep the old thread's SP invalid and its map inaccessible,
while only the new thread/map can satisfy the checks. They verify map restoration
on both success and failure. `check-ram-exec-handoff.py --exec-source PATH` also
compiles the actual patched XNU release block, testing five successful/failed
exec and PID scenarios with the feature both enabled and disabled. Source tests
and final disassembly verify the ready hook precedes the final wakeup.

The v2 phone test reached 170 SVCs and 152 UNIX calls. Its stage remained 8:
dyld aborted because `/usr/lib/libSystem.B.dylib` was missing from the two supplied
system trust modules. The photo, original library and structured observation are
preserved under `out/ram-progress/attempt-02/`. First-attempt diagnostic evidence
remains under `attempt-01/`.

### Original restore trust module (v3)

`prepare-fuxi-ram-inputs.py` now also extracts the exact original
`RestoreTrustCache` from the pinned BuildManifest. Its original version-1 bytes
are 5898 bytes, contain 267 entries and have SHA256
`54e80085f2de19d76f870aa63366ef520a905913dc0208381010864f7d1fbb09`.
The libSystem CDHash shown by the failed boot belongs to this module; it is absent
from both version-2 system modules.

DarwinBoot's external bundle validator accepts version 2 only. The progress
profile therefore embeds the unchanged version-1 module in a read-only native
kernel segment. `RamRestoreTrust.c` checks its pinned SHA256 and passes the whole
module to the original AMFI `TrustCache.loadModule` API as the first static cache,
using XNU's existing `trust_cache_static0` wrapper. The two existing version-2
modules remain unchanged and load afterwards using XNU's existing extra-module
policy. No module is converted, no hashes are synthesized, no signing check is
disabled and no AMFI failure is converted to success. A rejected load stops the
test with the actual returned error. All 267 entries must be queryable through
AMFI before md0 is mounted.

`--stage progress` enables `CONFIG_XNU_RAM_RESTORE_TRUST` and defaults to
`INPUTS/original-restore.trustcache`; other stages keep their existing behavior.
The final archive, source hashes, kernel compile flag, first-static hook and
unchanged embedded module are verified in `out/ram-progress/verification.json`.

Host validation includes 13 production loader cases and the actual patched
trust-cache blocks with the feature enabled and disabled. A read-only audit of
the original RAM disk checked 268 Mach-O files and 52,344 code pages; all were
valid and covered by the original restore module. These checks do not establish
that the original ARM64 AMFI loader accepts the module on the phone; v3 still
requires a temporary boot. The pending image is
`out/ram-progress/Mu-fuxi-xnu-ram-progress-v3.img`.

USB Bulk 接收器、构建/发送命令及当前硬件限制见 [usb-bulk.md](usb-bulk.md)。

### Native system-root authentication preflight

`--system-root-preflight` is an independent mount-only archive profile. It
requires `--user-entry --system-objects`, excludes system user entry, main,
progress and launch observation, and requires pins `system_root_preflight=true`
and `system_userland_allowed=false`. The existing md0 probe needs its user-entry
compile mode to reach the system mount checkpoint; the new system preflight is
`noreturn` and stops before init can be armed. Boot arguments are
`ram-root-probe=2 ram-system-probe=1 ram-system-root-preflight=1`.

The probe verifies the md0 root and both distinct read-only APFS volume roots,
then issues exactly one native `FSIOC_KERNEL_ROOTAUTH` per target, with the same
NULL data, zero fflag and kernel context as iOS `bsd_init`. This actively requests
authentication; it is different from the passive v35 AMFI query observation.
Actual authentication errors, before/after flags, root identity and candidate
pivot-directory lookup errors are printed without turning failures into success.
No root switch, mount-flag write, trust-cache change or userland entry is performed.
An authentication result on a secondary mount alone does not establish that a
later native root mount or full system boot will succeed.

`check-ram-system-root-preflight.py` executes the real C with ASan/UBSan, exercises
mode/identity rejection before any ioctl, verifies one original request per
target and actual error preservation, and checks guard/result mutations fail.
It also binds pins/config/boot args, the compiled call and three-member archive,
init object, source hashes and delivered image. It runs in the candidate check
suite whenever the explicit preflight argument is present. Hardware output still
requires a photo in the candidate's independent attempt directory.

### Native root switch via vfs_switch_root

`--system-root-switch` is the mount-only archive profile that pivots the md0
root to the OS volume with the original `vfs_switch_root("/mnt1", "Developer",
0)`. The probe validates the outgoing md0 root, both RAM APFS volume roots, the
outgoing placement directory (`Developer`) and the preserved `dev` directory,
plus the pinned `/mnt1/sbin/launchd` content, before releasing every vnode and
letting the native pivot run. Afterwards it verifies the switched rootvnode
mount, `MNT_ROOTFS` moved to the OS volume (and cleared from md0), the outgoing
mount at `/Developer`, preserved devfs at `/dev`, and `/sbin/launchd` on the
new root with the same bytes. The original `FSIOC_KERNEL_ROOTAUTH` result on
the switched root is recorded but never gates; userland stays prohibited and
the probe stops before PID1.

`check-ram-system-root-switch.py` compiles the real probe C with a model of
the pivot (including switch failures, a forgotten incoming `MNT_ROOTFS`, a
retained outgoing flag, wrong placement, missing devfs and a changed
post-switch launchd) under ASan/UBSan with three mutation rejections, and
binds pins/config/boot args, the archive, the `vfs_switch_root`/`MNT_ROOTFS`
source contract, original executable hashes and the delivered image. Hardware
output still requires a photo in the candidate's attempt directory.

### Native-root progress and RAM cryptex cache

For native-root progress use `--system-entry --native-root --progress` in the
production-C guard checker. The candidate suite derives these flags from the
archive and requires policy/guards for selector 4 as well as selector 3.
Each check run preserves its own logs under `check-runs/<UTC>/`.

`--system-cache` verifies and uses the existing RAM cryptex at
`/Developer/mnt2` after pivot. It requires the original native cache-root auth
result to be zero, pins the actual OS dyld bytes, supplies its cache-directory
environment and sets the original no-ignition startup bit. It changes XNU
diagnostic hooks, never original dyld/AMFI bytes or trust decisions.
`check-ram-system-cache.py` executes production preparation C and original dyld
CacheFinder instructions; `check-ram-cache-files.py` checks cache mapping
alignment and CodeDirectory block hashes from a read-only host mount.

Spawn snapshots distinguish raw `rv` from `child` read from the PID output
pointer. Successful `posix_spawn` has `e=0 rv=0`; `child>0 pe=0` is the observed
PID output, not proof that this child reached EL0. Missing/dropped observations
stay unknown. See `svc-dispatch-preflight.md` and the v40 REVIEW for exact
evidence and remaining runtime boundaries.
