# UniXNU-fuxi

Device repository for the Xiaomi 13 (fuxi, SM8550). It holds the board file, kernel defconfigs, device scripts, drivers, package files and the porting record.

This board runs the RAM receiver path: the firmware takes the OS payload into DRAM over USB bulk, and XNU roots from that RAM window. It is not a flash based port. Nothing in this repository writes an Android partition.

## Scope

1. UEFI firmware in [uefi-fuxi](https://github.com/mouse233/uefi-fuxi) opens a USB bulk receiver window and receives the payload into DRAM. The window ends after 180 s without host traffic.
2. The UniXNU menu entry starts DarwinBoot, which calls `ExitBootServices` and hands off to XNU.
3. XNU exposes the received window as a RAM block device, mounts the APFS system volume as its root, and starts `launchd`.

| Item | Value |
| --- | --- |
| SoC | SM8550 |
| Board name | fuxi |
| OS profile | `ipados-17.7.11-21H461` |
| Restore image | `iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw` (iPad7,1, j120ap, `chip_id 0x8011`, `board_id 12`), used unchanged |
| Kernel defconfigs | `fuxi_defconfig`, `fuxi_full_defconfig`, `fuxi_ramroot_defconfig` |
| Payload volume tag | `FUXIPAYL` |
| Firmware | `fuxi` and `feat/ramdisk-test` branches of [uefi-fuxi](https://github.com/mouse233/uefi-fuxi) |
| Structured record | [porting.toml](porting.toml), published at [unixnu.com/devices/fuxi-mouse233](https://unixnu.com/devices/fuxi-mouse233) |

## Status

Verified on device. The iteration log `diagnostics/ram-root/usb-bulk.md` holds the commands, image hashes and the readings transcribed from the device photographs. The photograph files themselves are not committed to this repository, so treat the log text as the record and the photo as the source behind it:

- v36 native SystemOS root authentication (`auth=80`).
- v37 native root switch: `pivot=OK old=md0 newroot=apfs`, `/sbin/launchd` SHA256 verified.
- v41 shared region mapped, `main` passed, and several userland processes running at EL0.
- v43 first natural XNU stop: a `watchdogd` critical exit panic.

Not reached: a complete userland boot, the home screen, display presentation, touch, GPU, Wi-Fi, audio and power transitions. Receiver shutdown is unverified for the XNU handoff attempts summarized here: those attempts end with `STOP status=Time out`. An earlier standalone v23 receiver run did confirm normal controller stop, so this result does not apply to every receiver attempt in the full log.

Treat the completion level of each subsystem, its limits and its next action in [porting.toml](porting.toml) as the current statement for this board.

## Dependencies

- EDK2 builds inside the Docker container `unixnu-fuxi-build-arm64`. BaseTools are Linux ELF binaries, so the firmware build does not run on macOS directly. The workspace is mounted at `/work`, the EDK2 tree at `/work/uefi-fuxi/Mu_Basecore`.
- XNU builds on Linux or macOS. The Linux path uses `scripts/linux/setup-toolchain.py` from the UniXNU checkout and needs a Clang with `__builtin_xnu_type_*` support plus a Mach-O toolchain; distribution Clang is not enough.
- The UniXNU checkout provides the build driver, the component catalog and `scripts/porting-record.py`.
- A device with an unlocked bootloader, reachable through `fastboot`, plus host USB tooling for the log reader.

## Build

Run these from the UniXNU checkout, with this repository as `../UniXNU-fuxi` and the firmware as `../uefi-fuxi`. Each candidate has its own output directory under `out/`, and these steps expect `PYTHONDONTWRITEBYTECODE=1`.

1. Create or update the candidate directory. New system volume candidates start from the preparer, which writes an independent directory instead of reusing a cached tree:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 scripts/prepare-usb-system-mount-candidate.py --parent <parent> --output <candidate>
   ```

2. Build the kernel for the candidate with that candidate's own `build-kernel.py`, then finalize it:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 out/<candidate>/build-kernel.py
   PYTHONDONTWRITEBYTECODE=1 python3 out/<candidate>/finalize.py
   ```

3. Build the firmware image, keeping the USB route and PHY options, and adding the system handoff for system volume payloads:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 scripts/build-fuxi-usb-ram.py --try-usb-route --keep-phy --system-handoff
   ```

4. Package a system payload only through the packager, which writes the `FUXIPAYL` FAT OEM tag. Calling the boot image builder directly leaves no tag and the boot manager reports no boot entry found:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 scripts/package-fuxi-xnu-boot.py --output out/<candidate>
   ```

5. Re-derive the receiver window skew from the delivered `kernel.macho`: the skew must be congruent with the lowest non-empty segment address modulo 2 MiB. A recorded value from another layout is not reusable.

6. Run the host checks and record each real exit code. `scripts/check-usb-system-candidate.py` runs them in order and keeps the true status of every step:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-usb-system-candidate.py --output out/<candidate> --uefi ../uefi-fuxi
   ```

   The individual checks are `check-usb-smmu-audit.py`, `check-usb-smmu-route.py`, `check-usb-ram.py`, `check-fuxi-usb-ram-image.py` and, for system volumes, `check-usb-system-mount.py`, `check-ram-probe-policy.py` and `check-ram-launchd-guards.py --system-entry`.

7. Validate this record after any change to it or to the board configuration:

   ```sh
   python3 scripts/porting-record.py check --device ../UniXNU-fuxi
   ```

**Next step:** boot one receiver window with `scripts/boot-ram-window-once.py` and transfer with `scripts/usb-ram-send.py`, then follow [Diagnostics](#diagnostics).

## Device operation

- `fastboot boot` only. No flashing, no erase, no boot slot change, no other partition write.
- Check that no receiver, sender or boot task is already running before touching the device, and confirm the serial, product and unlocked state before each boot.
- The receiver window and `fastboot` are mutually exclusive: while a window is open, `fastboot devices` lists nothing. An empty list is not evidence that the receiver stopped.
- Do not unplug, reboot or start another boot while a transfer is running. A stop is only accepted from a photograph showing `HALTED`, the restored route and `USBRAM QUIESCE: Success`.
- Host checks passing does not mean the device accepted the image. Record the reached stage from the photograph, and keep hardware values on this board only.

## Diagnostics

- `diagnostics/ram-root/usb-bulk.md` is the iteration log: candidates, on-device conclusions and next steps, appended in order.
- `diagnostics/ram-root/README.md` introduces the topic files, including `uefi-ram-transfer.md`, `apfs-ram-model.md`, `full-os-memory.md`, `system-root-audit.md`, `fatal-fault-forensics.md` and `watchdog-service.md`.
- Each candidate keeps its own `REVIEW.md` and attempt directories. These are working files and are not committed, so the record cites the committed iteration log for on-device results.
- `scripts/build-fuxi-ram-diagnostic.py`, `scripts/build-ram-root-probe.py` and `scripts/audit-ram-full-os.py` produce the read-only diagnostics used by these logs.

## Layered build path

`board.toml` declares `schema_version = 2` so the documentation catalog renders this record. The board does not yet build through the layered driver: it has no SoC source in `feeds.toml`, no component selection and no `dts/` directory, so `./unixnu status` on the format 2 driver stops at the missing `sm8550` source. The board scripts above remain the working entry points. The remaining steps are recorded as limits in the configuration area of [porting.toml](porting.toml).
