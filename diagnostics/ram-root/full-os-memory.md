# Full OS/SystemOS RAM root: P0 audit and P1 memory window

P0 is host-only. P1 only changes the `darwinboot-memory-size` window and the ADT;
it does not touch storage, userdata or any partition.

## P0: image audit

`scripts/audit-ram-full-os.py` reads the local research image
`unixnu-source/out/fuxi/userdata/userdata-root.img` (`SHNGROOT` v1), verifies the
manifest checksum and every window SHA256, then parses the GPT/APFS headers and
mounts each sliced window read-only to collect the volume identity. It never
writes to the image or a device. The recorded result is
[`full-os-audit.json`](full-os-audit.json).

| Window | Bytes | Layout | APFS blocks | Container UUID |
| --- | ---: | --- | --- | --- |
| OS | 6,077,580,800 (5.66 GiB) | GPT, one APFS partition at LBA 34 (512-byte sectors) | 4 KiB, 1,483,776 blocks | `50f8a67f-afc1-4a1b-bcbb-82162674c67f` |
| SystemOS | 3,303,014,400 (3.08 GiB) | bare APFS container | 4 KiB, 806,400 blocks | `e886f82c-ee56-4b52-af5f-e70bf46320c4` |

Mounted volumes:

| Window | Volume name | Volume UUID | Role | Sealed |
| --- | --- | --- | --- | --- |
| OS | `DawnUpdate21H461.J120J121J207J208OS` | `04769D26-2CA0-4825-B443-E2E5F88300F2` | System | No |
| SystemOS | `DawnUpdate21H461.J120J121J207J208SystemCryptex` | `8846FE78-2910-4297-AF97-D59BB6F8FC16` | none | Yes |

Notes for the root handoff:

- The OS window is a complete system volume: `/sbin/launchd` and `/usr/lib/dyld`
  exist, and `/System/Cryptexes/OS` is a symlink to
  `../../private/preboot/Cryptexes/OS`. The image ships an empty
  `/private/preboot`, so the SystemOS cryptex needs a Preboot layout (or a
  direct mount on that path) before it can appear at `/System/Cryptexes/OS`.
- SystemOS is a sealed, `Case-sensitive APFS` cryptex with `System/` and `usr/`
  but no launchd or dyld. It is not a root candidate on its own.
- Trust caches: the OS volume uses the original `StaticTrustCache`
  (`044-76040-038.dmg.trustcache`); SystemOS uses the original
  `Cryptex1,SystemTrustCache` (`044-76034-038.dmg.trustcache`). Both are already
  bundled unchanged as `inputs/original-static-system.bundle` (SHA256
  `7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7`).

## Memory budget

`audit-ram-full-os.py --kernel <prelinked kernel> --data-mib 1024` with 5 %
runtime/page-table overhead:

| Item | Bytes | GiB |
| --- | ---: | ---: |
| OS window | 6,077,580,800 | 5.660 |
| SystemOS window | 3,303,014,400 | 3.076 |
| Prelinked kernel (progress profile) | 51,933,184 | 0.048 |
| RAM Data seed input | 1,073,741,824 | 1.000 |
| Overhead (5 %) | 525,313,510 | 0.489 |
| **Total** | **11,031,583,718** | **10.274** |

The device reports about 23.62 GiB of RAM, but the loader limits are the real
gates:

- `DarwinWindowSizeValid` (`WindowSize.h`) accepts 256 MiB..**8 GiB**, 2 MiB
  aligned. The 10.27 GiB budget does not fit in one current window.
- The current profile passes `0x130000000` (4.75 GiB). The OS window alone is
  5.66 GiB, so the current window cannot even hold the root image.
- The feasibility pass measured about 5 GiB of conventional memory published to
  UEFI, with a candidate high region of ~15.43 GiB above `0xA00000000` that the
  Linux FDT did not reserve. That region has not been qualified for UEFI or XNU.

Two consequences:

1. Before any full-image work, the window itself must grow past 5.66 GiB.
2. Beyond 8 GiB requires either raising the parser bound and rebuilding the
   firmware, or handing over two windows. Splitting keeps each window under the
   current 8 GiB bound (5.66 and 3.08 GiB), but DarwinBoot currently allocates
   exactly one window.

## P1: raise the window

`build-fuxi-ram-diagnostic.py --owned-memory-size 0x180000000` overrides the
ADT `darwinboot-memory-size` (value must stay inside the parser bound and be
2 MiB aligned). `scripts/check-ram-memory-window.py --adt <kernel.macho.adt>
--bytes 0x180000000` re-verifies the value inside the delivered payload.

What the device boot shows:

- Success: the progress screen's `pages`/`free` counters scale with the window.
  The 4.75 GiB profile reports `pages=0x11c297 free=0x1148dc`; a 6 GiB window
  should raise both by roughly 0x5C000 4 KiB pages (exact values depend on what
  the kernel has already allocated). That proves XNU owned and initialized the
  larger window.
- Failure: DarwinBoot's `NO WINDOW <size> BIG RUN <bytes> @ <address>` line plus
  up to eight `FREE <start> + <bytes>` lines. That is the UEFI memory map's
  actual conventional runs and is the input for the next firmware change.

Candidate built with the current progress profile:

- Image: `out/ram-progress/Mu-fuxi-xnu-ram-mem6g.img`, 202,768,384 bytes
- SHA256: `53dcea1a30b5eec253c22f8b3e78f00e13f243b88143e8ec6c946f7f96334ebe`
- ADT verified at `0x180000000` (6.000 GiB); the delivered FAT was re-read and
  matched the kernel, trust cache, RAM disk and ADT (see
  `out/ram-progress/mem6g-verification.json`).
- Host guard cases: root 24, main 45, progress 66; renderer matched the payload.

Use `out/ram-progress/boot-mem6g.py`, which checks the image hash, the 6 GiB
verification record and the device identity before one temporary
`fastboot boot`. The result of that run is below.

### Device result (attempt-06)

The 6 GiB window is refused by the UEFI memory map before XNU starts:

```text
NO WINDOW 180000000 BIG RUN 1346E2000 @ 8C000
FREE 8C0000000 + 1346E2000
```

- Largest conventional run: `0x8C0000000 + 0x1346E2000` = 5,174,599,680 bytes
  (4.8192 GiB), ending at `0x9F46E2000`. No other run reaches 4 MiB.
- The current 4.75 GiB window leaves 70.9 MiB of headroom; the largest
  2 MiB-aligned window this firmware can hand over is 4.8184 GiB.
- The 15.43 GiB bank at `0xA00000000..0xDDB900000` is not published. The firmware
  descriptor is `XNU Window` = `0x8C0000000 + 0x140000000` in
  `uefi-fuxi/Platforms/Xiaomi/fuxiPkg/Library/MemoryMapLib/MemoryMapLib.c:106`;
  the comments at lines 100-105 already list the unpublished banks and note that
  "an XNU window larger than 5 GiB has to bring them in".

So the P1 gate confirms the audit: the limit is the firmware memory map, not
XNU. The next changes are:

1. Publish the free part of `0xA00000000..0xDDB900000` as `EfiConventionalMemory`
   after confirming the reservations that start at `0xDDB900000`.
2. Raise `DarwinWindowSizeValid` (`WindowSize.h`, currently 8 GiB) before asking
   for more than 8 GiB in one window.
3. Keep the ~194 MiB gap after `0x9F46E2000` and the firmware tail out of the
   window; DarwinBoot needs one contiguous run.

### Device result (attempt-07): the high bank works

The firmware now publishes `{"XNU High", 0xA00000000, 0x3DB900000, Conv}` after
the existing 5 GiB entry, and the same 6 GiB ADT window boots:

```text
pages=0x16b07b   (1,486,971 pages = 6,090,633,216 bytes = 5.672 GiB)
free =0x1635ab   (1,455,531 pages = 5.552 GiB)
```

- The window is 6.000 GiB and the kernel image, kexts, RAM disk and launchd all
  ran from it, so the 15.43 GiB high bank is real, writable memory, not just an
  FDT description.
- Overhead is 335.5 MiB, the same ~330 MiB the 4.75 GiB profile shows; the page
  count grew by 1.232 GiB.
- The restore environment behaves exactly as before (`main=PASS`, then
  `restored_external` waits for `AppleNANDStatus`), so this change is isolated
  to the memory map.

Two incidental fixes were needed to get here:

- `bootimg.verify()` parsed every image with the v0 header layout; fuxi uses v3
  (1580-byte header, no load addresses, 4 KiB pages). It now branches on the
  header version. The boot image is also built with the stock `os_version`
  (`0x220001a7`) and a `dummy` ramdisk so the existing packager accepts it.
- `unixnu build firmware` did not define `FUXI_PAYLOAD_DIAGNOSTIC`, so
  `FuxiPayloadDxe` was excluded and the boot manager found no `unixnu.toml`
  volume (`no boot entry found`). `board.toml` now sets it to 1.

Still open: a single window is capped at 8 GiB by `DarwinWindowSizeValid`, while
the full-OS budget is 10.27 GiB, and the 8.74 GiB image transport is unresolved.
Splitting the OS (5.66) and SystemOS (3.08) into two windows would fit the
current cap but needs a second-window handoff in DarwinBoot.

This step writes only RAM. No partition, userdata or boot slot is modified.

## Rerun

```sh
python3 scripts/audit-ram-full-os.py \
  --image ../unixnu-source/out/fuxi/userdata/userdata-root.img \
  --slice-dir /path/with/9g/free --mount \
  --kernel out/ram-progress/work/kernel/kernel.macho \
  --report diagnostics/ram-root/full-os-audit.json

python3 scripts/build-fuxi-ram-diagnostic.py ... --stage progress \
  --owned-memory-size 0x180000000 --output out/ram-progress
python3 scripts/check-ram-memory-window.py --adt kernel.macho.adt --bytes 0x180000000
```
