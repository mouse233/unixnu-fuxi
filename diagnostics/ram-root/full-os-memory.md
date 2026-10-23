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

The device boot is still pending. Use `out/ram-progress/boot-mem6g.py`, which
checks the image hash, the 6 GiB verification record and the device identity
before one temporary `fastboot boot`.

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
