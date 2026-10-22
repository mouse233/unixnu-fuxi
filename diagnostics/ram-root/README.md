# fuxi RAM-root bring-up

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

Use `--stage user-entry` for the second diagnostic. `--plan-only` writes the
effective board, pins and config without compiling. The builder reuses the
base checkout's build tree but isolates final products and logs in
`out/ram-mount/` or `out/ram-user-entry/`.

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
