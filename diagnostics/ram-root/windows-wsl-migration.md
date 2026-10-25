# Windows / WSL2 migration, 2026-10-25

The active workspace is `/home/mouse/work/UniXNU` in Ubuntu WSL2. The Windows
directory `D:\Projects\UniXNU` retains the original handoff archive and report
entry point. `/Volumes/External/VSCode/UniXNU` is a compatibility symlink inside
WSL; existing historical candidate scripts and reports retain their original bytes.

The archive SHA256 is
`e4e516292566456ec7f38c76e845337f4b019c2264ac219105107197699ba6fe`.
Restoration recovered six dirty repository snapshots (five nonempty patches),
ten untracked files, and 58 output record directories. All five patches passed
pre-application and reverse checks. Initial byte comparison covered 3,062 files
(2,573,743,015 bytes); v47's 30 UEFI/DarwinBoot source bindings matched.
The original snapshot and memory files remain available in the root-level
`handoff-archive/`; historical authentication and venv files were not activated.

The device checkout was first restored at 449f06f, then fast-forwarded to a0fa052
to include the two later M1 log commits. The independent TWRP checkout retains
the exported M0 commit e64deffc. Its remote M1 source was not substituted for
the historical checkout. The older `/home/mouse/work/uefi-fuxi` was preserved.

## Linux host check adaptation

`check-usb-ram.py` originally used `-dynamiclib` and CommonCrypto unconditionally.
On Linux, clang rejected that invocation before the real receiver could run.
The fixture now uses an ELF shared library (`-shared -fPIC`) and OpenSSL SHA256
on Linux, while keeping CommonCrypto and `-dynamiclib` on macOS. Receiver
production C and the Python sender are unchanged. No checks were bypassed.
The original Linux failure and the successful protocol run are both preserved
under the workspace's `migration/20261025/`, outside historical candidate output.

Current checks: SMMU audit, route and USB receiver/sender pass; the actual watchdog
C++ model passes 33 cases and rejects four mutants. The independent upstream
UDIF suite passes all six cases after building its pinned LZFSE tool. These are
host checks; no phone boot, USB transfer or partition operation was performed.
macOS execution of the retained fixture branch was not available on this host.

## Upstream Linux XNU support

UniXNU upstream 16e592abee8a550dec8154ed1f38b5ebc4f16213 implements Linux builds in
`scripts/linux/setup-toolchain.py`, `scripts/linux/prepare-xnu.py` and
`scripts/unixnu/kernel.py:linux_env`. It builds patched Clang with the XNU type
builtins, Mach-O cctools, mig/iig and fakeroot SDK support. Linux userdata decoding
also exists; skip-setup and Data seed resizing still require macOS image tools.

The restored fuxi experimental source predates that support. Its RAM-root,
user-entry and main-entry build extensions are absent from the corresponding
upstream build script, although the upstream config reader still supports this
device's schema 1. A future Linux port must preserve those patches, compile
macros and compiled-object guards. Replacing the experimental script wholesale
would not be a qualified migration.

The upstream `--check` command passes the installed host dependency compile/link
probe; it does not install or validate the complete cross toolchain. A direct
`linux_env()` check still reports the missing `/opt/unixnu-linux/llvm/bin/clang`.
No full XNU rebuild was attempted in this migration.

## Delivery boundary

The archive excludes candidate images, work/cache trees, the full IPSW and the
boot-tests archive. The exported v47 scripts also lack required local inputs,
including config/restore.dbr and original launchd/dyld evidence. v47 remains
untested on hardware, while v46's photograph records a watchdog preparation
failure before PID1 exec. Historical reports are retained as historical evidence.

Docker Engine setup was explicitly authorized by the user after automatic
approval rejected a persistent daemon/group permission change. Docker Desktop's
existing socket startup failure was not handled by a reset. Container build and
mount results are recorded separately in the root migration reports. The legacy
container name is retained for scripts, but its new host/tool architecture is
linux/amd64 and the firmware target is AARCH64; archived ARM64 venvs are not used.
