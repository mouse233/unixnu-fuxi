# UEFI 阶段从电脑向 RAM 传输 OS（2026-10-23）

可行，但当前 fuxi 固件尚无可用的接收通道。要实现的是一个在
ExitBootServices 之前运行的 RAM 接收器；已有的 Android ABL fastboot
不能作为新 UEFI 的 USB 服务继续调用。

## 本次照片先说明了什么

12 GiB 首次候选 `1c1885ec…` 的 attempt-08 原图和设备记录保存在
`out/ram-mem12g/attempt-08/`。

- 绿条由 DarwinBoot 的 `DarwinEnter` 在 ExitBootServices 成功后画出。
- 蓝条由 XNU 入口补丁画出，说明已经跳入 XNU。
- 没有紫条：尚未到达启用 MMU 前的检查点。
- `LockCapsuleInterface` 是 ExitBootServices 事件回调的日志，不是安全锁阻止启动。
- 按已交付 loader 的控制流，12 GiB 分配和抽样检查已经越过；仍没有证明
  XNU 能在 12 GiB 窗口下完成初始化。

找到的确定容量问题：`native-4k-bootstrap-capacity.patch` 只留了 16 个
4 KiB 页用于两个 L1 root 与两套 L2 映射。对应本轮预期 PA/KVA，12 GiB
需要 27 页，旧代码会进入 `Lbootstrap_tables_exhausted`，恰在两个色条之间。
照片没有异常 PC，因此不能排除另有故障；容量不足本身已经能复现。

源码现已扩大到 36 页，以容纳最多 16 GiB、两套映射均跨额外 1 GiB 区间
的最坏对齐。主机直接执行从 `start.s` 提取的真实 ARM64 汇编宏：旧池在
12 GiB 拒绝、新池在 12/16 GiB 通过，最坏对齐与越界 canary 共六个场景通过。
测试为 `unixnu-source/scripts/check-bootstrap-window-capacity.py`。
**这项修复尚未重建进新内核；当前手机运行的候选仍是旧 16 页版本。**

## 当前 USB 条件

本地确认：

- `fuxi.dsc` 的 `FUXI_VENDOR_HW=0`，`Binaries/fuxi` 也没有已提取的驱动。
- QcomPkg 有 `EFIUsbfnIo.h`、`EFIUsbDevice.h` 等接口定义；接口声明不等于
  有实现或运行时发布了协议。
- 当前 fuxi 的 DXE 列表/FV 只有 UsbBus、UsbKb、UsbMassStorage 等上层模块，
  没有看到 USB Function 控制器实现。它们也不能替代接电脑所需的设备模式。
- 仓库有 Samsung 的 UsbfnDwc3Dxe，可研究共享的 DWC3 控制器逻辑；它依赖
  Samsung 平台初始化，不能直接当作 SM8550 PHY/时钟/复位/Type-C 的驱动使用。

UEFI USB Function 接口就是让固件作为 USB 设备与主机通信的抽象，参见
[UEFI USB Function 规范](https://uefi.org/specs/UEFI/2.11/17_Protocols_USB_Support.html)。
Fastboot 自身也使用两个 USB Bulk 端点，且有 download-to-memory 数据阶段，参见
[AOSP 协议说明](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fastboot/README.md)。

## 建议实现的路径

电脑 → USB Bulk → UEFI RAM 接收器 → 已分配并预留的 OS/SystemOS 范围 → XNU RAM 块设备。

1. 先只做设备枚举与 1 MiB 测试传输，完成收发、哈希核验和超时退出。
   不一开始引入全套 vendor 硬件驱动；先限定 USB 所需的依赖。
2. 接收端限定 OS/SystemOS 两个对象，使用 64 位总长度和对象内偏移。
   电脑分块发送，例如 1–16 MiB；接收器检查边界、连续偏移和每块/整对象 SHA256。
   不提供任意物理地址写入接口，也不提供 flash/erase 命令。
3. 先为 XNU/镜像规划唯一布局，再分配接收区域。USB DMA 使用控制器支持的
   小缓冲区，必要时把数据复制到高位 RAM；不要假设控制器可直接 DMA 到
   0xA00000000，也不要同时保留两份 8.74 GiB 的密集镜像。
4. 将镜像区及接收清单交给 DarwinBoot，保证 ExitBootServices 后不会被
   XNU 分配器覆盖。镜像应处在 XNU 实际能映射的物理范围内，并通过合适的
   boot-args/ADT 保留布局交接。仅把 EFI allocation 标为 LoaderData 不足以
   让当前 DarwinBoot/XNU 自动保护任意接收区域。
5. XNU 的 RAM 块设备消费这些物理范围，发布只读 OS/SystemOS；再处理
   普通根卷选择、cryptex 挂载和易失 Data。UEFI RamDisk/BlockIo 协议本身
   不能跨 ExitBootServices 替代 XNU 块设备驱动。

这样 768 MiB 只是启动小型 UEFI 镜像时遇到的 ABL 限制，不再是后续传输
整个对象的限制。标准 fastboot 的单次 DATA 长度仍是 8 位十六进制字段，
若借用协议也必须显式实现分块累计，不能简单把一次 download 扩到 8.74 GiB。

## 其它路径的取舍

| 路径 | 本机需要补齐的内容 | 判断 |
| --- | --- | --- |
| USB 设备模式 Bulk | SM8550 USB Function 驱动、PHY/时钟/复位、接收器与电脑端工具 | 最符合“一根线从电脑送到 RAM”的目标，优先研究 |
| USB 网络后 HTTP/PXE | 同样需要 USB 底层，再增加网络设备协议和网络栈 | 更复杂，不解决缺 USB 控制器驱动的根问题 |
| OTG U 盘/外部存储 | Host 控制器与 PHY/供电/角色切换，文件读取后 RAM 交接 | 可作独立备选；当前上层 UsbMassStorage 模块不足以证明能用 |

下一轮内核先纳入 36 页修复，确认紫条/MMU 和 launchd；传输侧的首个硬件
验收点是 USB Function 枚举及小数据收发，而不是直接上传完整 OS。
