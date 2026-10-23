# v26：补齐只读 RAM APFS 挂载的 VFS SPI

日期：2026-10-24。父候选 v25 已实机越过 compressor/KMEM 初始化，但照片
IMG_6951 显示 `RAM_SYSTEM APFS mount failed id=1 errno=22 @RamSystemProbe.c:65`。
照片及结论见 v25/attempt-01 与 `usb-system-mount-v25.md`。

## 修正范围

新增 `patches/xnu/zz-ram-system-mount-spi.patch`，在既有的
`zz-ram-system-disks.patch` 之后应用：

1. `mount_common` 的 VFC_VFSLOCALARGS 内核挂载拒绝条件，增加
   KERNEL_MOUNT_RAM_SYSTEM 的专用例外。原来只允许 by-role/BaseSystemRoot，
   所以 v25 在 VFS_MOUNT/APFS 调用之前就返回 EINVAL。
2. 专用设备路径来自内核字符串，namei 的地址空间改为 UIO_SYSSPACE。
   只跳过设备名指针 copyin 并不够；后续路径查找也必须使用正确的地址空间。
3. RAM 专用入口只接受 `/dev/md1`→`/mnt1`、`/dev/md2`→`/mnt2`；
   在 mount_common 再要求内核调用、apfs、MNT_RDONLY，拒绝 MNT_UPDATE。
   其他普通内核 local-args 挂载仍按原规则拒绝，原 by-role/BaseSystemRoot 行为不变。

保留 v25 的 512 MiB compressor 池和早期 freezer accounting 参数处理；
不改变 USB 协议、对象地址/长度、只读块设备、12 GiB 窗口或用户态前停止点。

## 交付镜像

- 目录：`out/usb-system-mount-v26/`
- 镜像：`Mu-fuxi-usb-ram.img`，203,190,272 字节。
- SHA256：`791840bcbcf95b77d85592e00bd9b148ed7716470306766ed4576f5c06aa9838`。
- prelink：`7c8bfa41197bafce5c7c350c9db40c208d251ea3f98c60f2fb7a0c13d76ad8a0`。
- `FUXIPAYL` 已校验；kernel base `0xfffffff00427c000`，window skew `0x7c000`。

v26 用 `prepare-usb-system-mount-candidate.py` 从 v25 克隆独立缓存后，
执行本目录自己的 build-kernel.py 和 finalize.py。内核在 macOS/Xcode 主机上
构建，固件在 Linux Docker 容器内构建；v25 交付镜像与照片未改动。

## 主机验证（退出码均为 0）

四项必需检查：check-usb-smmu-audit、check-usb-smmu-route、check-usb-ram、
check-fuxi-usb-ram-image。payload/ADT/kernel/trust/data、FUXIPAYL 与 skew 检查通过。

`check-usb-system-mount-spi.py` 从真实 vfs_syscalls.c 抽取 C 入口、拒绝条件、
专用保护与路径地址空间选择，在 ASan/UBSan 下执行。v25 复现 EINVAL 和
UIO_USERSPACE；v26 专用调用通过，并覆盖只读条件、更新挂载拒绝、错误文件系统、
非内核调用、空参数、物理盘名与交叉路径拒绝，保留普通内核挂载与 by-role/
BaseSystemRoot 的原行为。这只验证 VFS glue，不模拟 APFS 或替代实机挂载。
报告：mount-spi-tests.json，绑定本次镜像/内核及真实 VFS 编译记录。

KMEM 七项检查继续通过；最坏余量下界 662,548,480 字节，交付 prelink 的早期
参数解析反汇编核验通过。启动门现在还要求 mount-spi-tests.json 与当前镜像和
内核匹配；八种启动门测试（一个正常、七个缺失/过期/预算不足情况）通过。

```sh
cd /Volumes/External/VSCode/UniXNU/ramdisk-test/UniXNU-fuxi
python3 scripts/check-usb-system-mount-spi.py \
  --output out/usb-system-mount-v26 --base ../unixnu-source \
  --baseline out/usb-system-mount-v25/work/tree/.cache/xnu-work/bsd/vfs/vfs_syscalls.c
```

## 实机验收（待测试）

等待用户将手机重启到 fastboot；不得在接收器窗口重复临时启动。
检查无进行中传输、fastboot 中 serial=fcde91e 后，用新 attempt 目录执行：

```sh
cd /Volumes/External/VSCode/UniXNU/ramdisk-test/UniXNU-fuxi
DYLD_LIBRARY_PATH=/opt/homebrew/lib python3 scripts/test-usb-ram-full.py \
  --image out/usb-system-mount-v26/Mu-fuxi-usb-ram.img \
  --expected-sha256 791840bcbcf95b77d85592e00bd9b148ed7716470306766ed4576f5c06aa9838 \
  --research-image /Volumes/External/VSCode/UniXNU/unixnu-source/out/fuxi/userdata/userdata-root.img \
  --boot-profile usb-system --output out/usb-system-mount-v26/attempt-01
```

双对象 END RAM SHA256 通过后，手动选中 `fuxi RAM diagnostic: system-mount`。
观察 DarwinBoot 与 XNU 最终哈希、/mnt1 与 /mnt2 只读 APFS 挂载，最终应停在
panic `RAM_SYSTEM PASS`。控制器 HALTED、路由恢复、QUIESCE 与最终 XNU 结果均
以照片为准。当前 v26 尚未实机，不能宣称 APFS 挂载或普通系统启动成功。
