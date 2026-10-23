# v25：修复 USB 双镜像交接后的 compressor VA 预留失败

日期：2026-10-24。父候选 v24 attempt-05 的实机照片 IMG_6950 已确认 XNU
执行到 `kmem_range_init`，随后 panic：

```
kmem_range_init: vm_map_locate_space failing for claim compressor
@vm_kern.c:3731
```

## 原因与修正

本机非 ARM_LARGE_MEMORY、4 KiB 页的布局中，DarwinBoot 把统一 12 GiB 窗口
交给 XNU，`topOfKernelData` 为窗口基址 + `UR_RETAIN_END`（0x24f400000，
9.23828125 GiB）。XNU 的物理映射从这个保留区之后开始，再加完整 12 GiB 窗口、
小于 1 GiB 的随机偏移与 PTOV 对齐余量。最坏情况下，动态内核 VA 的连续空洞
仅 27,573,350,400 字节（约 25.68 GiB）。

iOS zone 固定预留 24 GiB VA。compressor 仍按 `max_mem=12 GiB` 计算压缩池，
并为五个 128 MiB freezer swap 文件预留缓冲空间；实际原版函数算出的 compressor
claim 为 13,556,281,344 字节（约 12.625 GiB）。两者之和已经超出连续空洞，
`kmem_add_extra_claims` 的剩余空间无符号减法也会下溢，后续随机 claim 无法放入。
这不是等待哈希，也不是 APFS 错误。

修正分两部分：

- `native-compressor-early-freezer-mode.patch` 把现有
  `-disable_freezer_cseg_acct` 参数的处理从 `vm_compressor_init` 移到
  `vm_compressor_set_size`，使首次 KMEM 预留时就生效。未指定参数时行为不变。
- 此挂载诊断的 payload 增加 `vm_compression_limit=131072`
  （4 KiB 页，512 MiB 压缩池）和 `-disable_freezer_cseg_acct`。
  压缩器仍运行；未修改 zone VA、不跳过 allocator panic、不缩小 12 GiB
  物理窗口，也不释放或覆盖两个系统对象。

只限制压缩池而保留旧的参数处理时序时，原版函数仍申请 1,207,963,648 字节。
两项修正一起使用时，claim 为 537,018,368 字节（约 512.14 MiB，含元数据）。

## 交付与主机验证

镜像：`out/usb-system-mount-v25/Mu-fuxi-usb-ram.img`。
大小：203,190,272 字节。
SHA256：`163f05519cb001c97c15f710c7024e437be6f8eb95ba0aae582e1405e7fe5879`。
prelink 内核：`4c56668bb0432bec880927de9fb3a09383c2c0bcf122c355ef70ba0699d2a7ba`。
窗口 skew 仍为 `0x7c000`，与交付内核基址 `0xfffffff00427c000` 同余。
FAT OEM 标记为 `FUXIPAYL`。

以下检查退出码均为 0，报告位于候选目录：

- `check-usb-smmu-audit.py`：14 个生产 C 用例。
- `check-usb-smmu-route.py`：20 个生产 C 用例。
- `check-usb-ram.py`：21 个生产协议/发送器用例。
- `check-fuxi-usb-ram-image.py`：交付 FD/DTB/模块/源码及既有端点停机测试绑定。
- `check-usb-system-mount.py`：payload、内核/ADT/trust/data 身份、skew、挂载参数。
- `check-usb-system-kmem.py`：从固定 XNU 源码抽出真实的容量计算函数，运行新旧
  函数与参数组合；ASan/UBSan。检查未加参数时行为不变、首次计算与再次计算稳定、
  旧配置超预算、新配置有余量，并反汇编确认交付内核在 swap sizing 前解析参数。
- 启动门测试：缺失 KMEM 报告、旧镜像报告、旧内核报告、预算不足时，均在任何
  设备查询之前拒绝；正常报告到达受 mock 拦截的设备查询（共五种情况）。
- 完整传输运行器的六种主机回归场景通过。

预算模型预留 24 GiB zone VA、512 MiB zone 元数据/bitmap/tag 上界和
64 MiB IPC/io/fuzz 等开销后，最坏仍剩 662,548,480 字节（约 632 MiB）
供后续 KMEM 范围使用。这个静态预算是当前固定 4 KiB 内核/设备布局的主机检查，
不等于所有 VM 初始化或 APFS 挂载已实机通过，也不能推广到其他设备/配置。

启动脚本的 usb-system profile 现在要求 KMEM 报告绑定当前镜像与内核，且
剩余 VA 下界至少 512 MiB；fastboot 固定使用 `/opt/homebrew/bin/fastboot`。

## 构建与复现

`prepare-usb-system-mount-candidate.py` 用 APFS clone 建立独立候选，更新 pins
路径与 compressor 参数，清除继承的实机/镜像验收报告。不会 hardlink 修改父候选。
内核仍由候选自己的 `build-kernel.py` 在 macOS 主机构建，固件由该目录的
`finalize.py` 在 `unixnu-fuxi-build-arm64` 内构建并调用 FUXIPAYL 打包器。

```sh
cd /Volumes/External/VSCode/UniXNU/ramdisk-test/UniXNU-fuxi
python3 scripts/prepare-usb-system-mount-candidate.py \
  --parent out/usb-system-mount-v24 --output out/<新的候选目录>
DEVELOPER_DIR=/Volumes/External/Applications/Xcode.app/Contents/Developer \
PATH=/opt/homebrew/opt/llvm/bin:$PATH \
python3 out/<新的候选目录>/build-kernel.py
python3 out/<新的候选目录>/finalize.py
```

重建后按上列验证顺序运行，先生成 `payload-verification.json`，再运行
`check-usb-system-kmem.py --output out/<新的候选目录> --base ../unixnu-source`。
不同重建的 SHA256 以实际清单为准。

## 实机验证

v25 attempt-01 已由用户确认进入 fastboot 后启动，等待双对象传输结果和屏幕证据。
本轮仅 `fastboot boot`，未写分区。传输结束后需手动选择
`fuxi RAM diagnostic: system-mount`。

```sh
cd /Volumes/External/VSCode/UniXNU/ramdisk-test/UniXNU-fuxi
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 scripts/test-usb-ram-full.py \
  --image out/usb-system-mount-v25/Mu-fuxi-usb-ram.img \
  --expected-sha256 163f05519cb001c97c15f710c7024e437be6f8eb95ba0aae582e1405e7fe5879 \
  --research-image /Volumes/External/VSCode/UniXNU/unixnu-source/out/fuxi/userdata/userdata-root.img \
  --boot-profile usb-system --output out/usb-system-mount-v25/<新的attempt目录>
```

目标：DarwinBoot 双对象最终哈希通过 → XNU 越过 KMEM 初始化 →
RAM_SYSTEM 双对象哈希通过 → /mnt1、/mnt2 只读 APFS 挂载 → panic `RAM_SYSTEM PASS`。
正常停机的 HALTED/路由恢复/QUIESCE 三标记、APFS 成功或任何失败点仍需照片确认。
当前不能宣称 v25 已启动成功或已修复实机 panic；主机验证通过是本轮已确认的边界。
