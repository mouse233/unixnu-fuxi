# fuxi UEFI USB Bulk RAM receiver

2026-10-23：**v22 首次通过完整配置枚举和实机 1 MiB Bulk→RAM SHA256 校验。**
已确认 1209:0001、480 Mb/s、配置 1、ff/58/01 接口；主机报告与用户照片相符。
随后完整 OS 6,077,580,800 字节与 SystemOS 3,303,014,400 字节在同一会话
分别通过设备 END 实际 RAM SHA256 校验，合计 8.736 GiB。
独立 v23 已通过 1 MiB 回归和正常控制器停机；IMG_6937 确认 HALTED、
events=0、route release result=0 restored=1、USBRAM QUIESCE: Success。
**当前镜像只停留在 UEFI，不启动 XNU。**

## 实现范围

`uefi-fuxi/Platforms/Xiaomi/fuxiPkg/Drivers/FuxiUsbRamDxe` 实现接收器；
`Library/FuxiUsbfnLib` 连接现有 Samsung 通用 DWC3 控制器驱动与 fuxi。
通过 `FUXI_USB_RAM_RECEIVER=1` 显式启用，默认固件不包含它。
当前使用 ABL 留下的 PHY/时钟状态，控制器基址 `0x0a600000`。
这不是完整的 SM8550 PHY、时钟、Type-C 冷启动驱动。

USB2 High Speed，实验 VID:PID `1209:0001`，产品名 `fuxi RAM Bulk`，
接口 `ff/58/01`，Bulk OUT `01`、IN `81`。VID/PID 仅用于本机诊断。
主机同时核对产品名。DMA 暂存缓冲区分配在 4 GiB 以下并设为 UC；
验证每块数据后复制到接收对象。USB DMA 不直接指向高位 OS 对象。

协议没有 flash、erase、分区、任意物理地址写入或执行入口。
对象内存由 EFI 分配器管理；180 秒无通信后停止，出错也停留在 UEFI。
这不能阻止底层硬件异常导致重启。镜像不含可启动 XNU 的 payload。

## 构建及核验

在设备仓库目录，使用已有的 arm64 Linux 构建环境：

```sh
python3 scripts/build-fuxi-usb-ram.py \
  --base ../unixnu-source --uefi ../uefi-fuxi \
  --edk2 /work/uefi-fuxi/Mu_Basecore --output out/usb-ram
```

主机检查使用 macOS CommonCrypto，编译并执行实际 `RamBulk.c`，
通过实际 Python `Client` 测试，不需要 USB 设备：

```sh
python3 scripts/check-usb-ram.py --uefi ../uefi-fuxi \
  --report out/usb-ram/host-tests.json
python3 scripts/check-usb-smmu-audit.py --uefi ../uefi-fuxi \
  --report out/usb-ram/smmu-tests.json
python3 scripts/check-fuxi-usb-ram-image.py --uefi ../uefi-fuxi
```

后者解压实际交付 boot image/FV，核对控制器及接收器 EFI 二进制、
源码哈希、stock OS metadata 与主机测试输入。`verification.json`
不代表硬件验收。临时启动工具支持 `--profile usb-ram`，要求精确镜像哈希
及上述核验记录，并检查 serial/product/unlocked；仅执行 `fastboot boot`。

## 主机发送

需要 libusb（macOS 默认加载 `/opt/homebrew/lib/libusb-1.0.dylib`）。
先完成枚举和自测，再考虑发送大对象：

```sh
python3 scripts/usb-ram-send.py --self-test --report out/usb-ram/device-transfer.json
python3 scripts/usb-ram-send.py --research-image ../unixnu-source/out/fuxi/userdata/userdata-root.img \
  --kind both --report out/usb-ram/full-transfer.json
```

自测发送 1 MiB 确定性数据，END 要求设备对实际 RAM 内容计算 SHA256 并回传。
发送工具先核对 SHNGROOT 源镜像清单和对象 SHA，再从原文件按偏移流式发送。
`--kind both` 保持一个连接，依次保留 OS 和 SystemOS；两次单独执行工具会
重新设置 USB configuration，清空上一轮对象。也可 `--file PATH --kind test|os|systemos`。
接收成功只证明 END 时 RAM 中对象完整，尚无 DarwinBoot/XNU 交接功能。

完整双对象实机入口先核验两个源窗口，再临时启动精确哈希的接收器：

```sh
python3 scripts/test-usb-ram-full.py \
  --image out/usb-ram-v22/Mu-fuxi-usb-ram.img \
  --expected-sha256 1ea39613b1ac09ba8350124c4b943e841cc852e0a2c160bb9c2435de0c957141 \
  --research-image ../../unixnu-source/out/fuxi/userdata/userdata-root.img \
  --output out/usb-ram-full-v22/new-attempt
```

实际源镜像位于原始工作区 unixnu-source/out/fuxi/userdata/，不在实验
ramdisk-test/unixnu-source/out/。每次使用新的输出目录保存失败与成功记录。
仅发现/打开设备阶段可重试；开始协议传输后不自动重传不确定的 DATA/END。
通过 --attach-boot-record 可关联一次已成功启动、尚未开始 DATA 的打开重试。

主机测试、双对象实机报告和停机候选是不同证据层次。完整结果见
out/usb-ram-full-v22/REVIEW.md；下一步内存交接的代码缺口见
[usb-ram-handoff.md](usb-ram-handoff.md)。

## 协议 v1

所有整数 little endian。请求与 ACK 都有 64 字节头：

| 偏移 | 长度 | 字段 |
| --- | --- | --- |
| 0 | 8 | `UNXRAM01` |
| 8 | 2 | version=1 |
| 10 | 2 | opcode；ACK 为 opcode OR 0x8000 |
| 12 | 4 | object ID |
| 16 | 8 | 请求对象内偏移；ACK 已接收长度，HELLO 则为块上限 |
| 24 | 4 | 请求 payload 长度；ACK=0 |
| 28 | 4 | 请求必须为 0；ACK 为状态码 |
| 32 | 32 | 请求 payload SHA256；END ACK 为实际 RAM SHA256 |

每次请求先写头、再单独写 payload，然后读取 ACK。payload 上限 65536 字节。
HELLO=1；BEGIN=2（payload 为 u64 总长度 + 32 字节最终 SHA256）；
DATA=3（必须连续偏移）；END=4（精确完成并核对实际 RAM）；ABORT=5（释放对象）。
空 payload 的请求头 digest 不使用。状态码定义见 `RamBulk.h`。
ID 0 测试对象最多 1 MiB，ID 1 OS 最多 8 GiB，ID 2 SystemOS 最多 4 GiB；
对象合计最多 10 GiB。分配失败返回错误。END 失败后对象必须 ABORT 才能重试。
短传输、畸形帧导致会话停止；主机不自动重传不确定的 DATA。
USB reset/detach、SET_CONFIGURATION 或 SET_INTERFACE 会清空对象。

## 已有证据及下一验收点

21 个主机用例通过，包括 1 MiB 内容核验、损坏检测、重复/乱序、边界、
分配失败、64 位长度、跨 4 GiB 偏移、两对象共存和合计容量。
大偏移用例使用稀疏映射与测试夹具预置进度，并非真实传输了 4 GiB。

第一版镜像 SHA256：
`aa373feddccfa4ba1b7640ad19973dda7757b64b9ed33bc96f756c519779d3d7`。
两次临时启动均进入接收器；第一次用户观察到两行后重启 Android，
主机未发现接收器，未发送数据。第二次照片确认最后可见日志为
`FUXI USB: inherited SNPS=33313130 USB2PHY=...`。
这证明可以读取 DWC3 标识，不能证明 PHY/时钟可用于复位或数据传输。
日志在 `UsbfnPlatformPhyInit` 中，尚无控制器停止或核心初始化完成证据。
记录和原图留在本地 `out/usb-ram/attempt-01`、`attempt-02`。

分步日志版给 DCTL stop、PHY reset、core reset、事件环设置增加 S0–S12 标记。
先定位最后一个标记及寄存器，再处理实际故障；不能把未枚举直接归因为 PHY。
后续硬件门槛为枚举 → 1 MiB END 校验 → 传输整对象，随后才实现预留布局与
DarwinBoot/XNU 交接。内核 36 页启动表修复也必须重建后才能继续 12 GiB 测试。

分步日志版镜像 SHA256：
`6ed2d53f4595cadef06575d4b2340e97c2d3650519a487997feefdaabb2af4f9`。
首次启动尝试发送成功，但 Booting 报 `usb_read failed with status e00002ed`；
尚不能证明镜像执行到接收器。主机未发现接收器，ADB 也未连接。
屏幕结果待补充；摘要见 [usb-bulk-evidence.json](usb-bulk-evidence.json)。


第二次临时启动分步版：Sending/Booting 都 OKAY，用户报告到达
`S12 core initialization complete` 后黑屏、重启 Android。
ADB 可见，`ro.boot.bootreason=bootloader`，仍无法说明具体重启机制。
因此可以把最后已确认的执行点推进到核心初始化完成；端点配置、EP0 首次
DMA、RUN_STOP=1 和事件处理均不能视为通过。

下一版增加 S13（StartController 返回）、S14（端点配置入口）、
S15（EP0 配置）、S16（Bulk 配置）、S17（首次 EP0 DMA 及地址）、
S18（启用连接）、S19（端点配置返回）和限额端点命令日志。
同时让首次 EP0 SETUP 提交失败时向上返回错误，避免忽略错误后继续连接。
这项错误传播修复不等于已找到重启根因；后续正常事件里的 EP0 重挂错误处理
仍沿用通用驱动原实现。USB 测试不通过前，不发送完整 OS。

S13–S19 候选已完成构建、21 项主机测试和交付模块核验，SHA256：
`43a0dff0c7bc9d56cb197177edca60547481a95504d013ee21cc4556ed95476b`；尚未启动到手机。


## 首次 DMA 定位与只读 SMMU 审计

`IMG_6903.HEIC` 显示 S13 成功、端点资源及 EP0/Bulk 配置全部返回状态 0。
最后可见行是 S17 后 `ep=0 cmd=6 params=00000000/C67BF000/00000000`；
没有下一行 command written。事件缓冲为 `0xC685A000`、TRB 为 `0xC67BF000`、
EP0 buffer 为 `0xC6850000`。因此最后确认的区域是首次 STARTTRANSFER 提交。
仅凭照片不能区分具体 MMIO 写入、同步屏障或异步硬件故障。

本机 2026-10-22 保存的 live DT（SHA 见 evidence JSON）独立确认：
`/soc/ssusb@a600000/dwc3@a600000` 的 iommus 为 phandle 0x41 / SID 0x40 / mask 0；
phandle 指向 `/soc/apps-smmu@15000000`，寄存器 aperture 1 MiB。
当前 Android shell 读取 DT 被拒，使用的是先前保存的设备原始捕获。
这与 [上游 SM8550 DTS](https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/qcom/sm8550.dtsi) 一致。

只读审计版 SHA256 `9ff2d106a583cb8065243e8842debaf677c968c4ad8d650a932484d2089a5a34`
已临时启动；`IMG_6905.HEIC` 显示预期 AUDIT STOP，没有发出 STARTTRANSFER：
ID0=4C017E69、ID1=60000046、CR0=00290406、GFSR=0；
105 组扫描完成，无 SID 0x40 匹配；寄存器页 4 KiB，context base 0x80000，70 个 CB。
CR0.CLIENTPD=0、USFCFG=1，即未匹配 stream 按架构配置进入故障处理。
这支持 DMA 路由缺失的假设；尚无 DMA 故障寄存器或异常 PC 来确认本次重启的因果。
`Not Ready` 是审计完成后主动停住的状态，不是枚举失败的重试结果。

审计不修改 SMMU 寄存器，最多读前 128 组，只解引用匹配且位于 aperture 内的 CB。
`check-usb-smmu-audit.py` 直接编译实际捕获引擎，14 项 ASan/UBSan 场景通过。
控制器通常需要有效的 DMA 映射；把缓冲限制在 4 GiB 以下并不建立这种映射。
后续路由修改还要考虑 [Qualcomm S2CR BYPASS 读回异常](https://github.com/torvalds/linux/blob/master/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c)。
需要精确匹配、读回校验和失败回滚，不应直接关闭整个 apps SMMU。


## 精确 USB 路由试验（默认关闭）

`FUXI_USB_TRY_SID_ROUTE=1` / 构建脚本 `--try-usb-route` 显式启用试验库。
不加该参数仍是只读审计镜像，预期停在 AUDIT STOP，不能运行发送测试。

路由引擎重新扫描完整的非扩展 SMR 表，要求 apps SMMU client 已启用、
未匹配策略为 fault、无既有 USB 匹配、扫描完整且存在无效条目；否则停住。
先在该无效条目设置 S2CR BYPASS（仅 USB），读回核对，再启用
`SMR=0x80000040`（SID=0x40、mask=0）。激活后再次核对 S2CR。
读回异常按保存原值回滚；回滚也验证，失败单独报告并阻止 DMA。
只写所选条目的 SMR/S2CR，不修改全局 CR0、其他有效条目或 context bank。
成功读回只是允许继续试验，不是对物理 DMA 或设备安全策略的硬件验收。

12 项实际路由引擎 ASan/UBSan 用例验证配置顺序、精确/重叠匹配、缺少空闲槽、
状态变化、Qualcomm BYPASS→FAULT、回滚失败及保持所有其他寄存器不变。
停止控制器后才能移除路由；移除前检查条目仍属于该会话，然后先撤销有效匹配，
再恢复 S2CR。控制器停止失败时保留路由和 DMA 缓冲。

试验构建还需执行并保存：

```sh
python3 scripts/check-usb-smmu-route.py --uefi ../uefi-fuxi \
  --report out/usb-ram-route/route-tests.json
```

`--output`、三个测试报告和镜像核验都要指向同一目录。
交付核验同时检查实际 EFI 模块中的 route-trial 标记与三组测试源码哈希。
发送器的 `--wait-seconds 15` 只等待设备发现，不自动重试任何协议数据传输。


精确路由版 SHA256 `201c2ba38ead647d229c47485d3fedc0232dc734e4e0947dd2c5669f59d1728a`
已完成 47 项主机测试及交付核验，第二次临时启动 Sending/Booting 成功。
`IMG_6908.HEIC` 实机确认：slot 0，BYPASS 请求 `S2CR=0x000100ff`
读回 `0x000200ff`（FAULT），result=4；原 `SMR=0x60/S2CR=0x20000`
完整恢复，active=0/restored=1，未发出 STARTTRANSFER。
这确认了本机对该 BYPASS 编码的异常处理，不能直接靠这个编码打通 DMA。
下一步检查不被其他有效 stream 引用、SCTLR.M=0、CBAR.TYPE=S1_TRANS_S2_BYPASS
的既有 context bank，尝试以 TRANS 指向它。仍需读回核验和实际 DMA 验收。


## 既有禁用 context 的 TRANS 路径

当前 `--try-usb-route` 候选改用 `FurInstallViaContext`；原 BYPASS 事务保留用于
回归测试和解释上一轮证据。默认构建仍只读审计。
新路径将所有 S2CR 中的 TRANS 引用都视为 context 占用（含无效 SMR 的引用），
在 1 MiB aperture 内从高位 context 向下查找，保留 bank 0。
只接受 `CBAR.TYPE=1`（S1 translation/S2 bypass）且 `SCTLR.M=0` 的既有状态；
不写任何 CB 寄存器、不更改已有页表。找不到合适 context 时返回 result=7 并停住。

在空闲 SMR 下先写 `S2CR=TRANS|CBNDX`，要求读回含 CBNDX 在内完全一致；
激活精确 SID 后再次核对路由及 context 状态。context 改变也回滚，保留外部改变。
复用既有禁用上下文的方案与 Linux 的 Qualcomm bypass-context 处理思路一致，
但本实验不创建或重配 context。尚未证明该路径在本机实际可用。

生产事务测试现有 20 项，新增 context 占用、禁用条件、S2 模式、配置变化、
精确 CBNDX 和 64 KiB 寄存器页场景；与 14 项审计及 21 项协议测试合计 55 项。
交付清单显式记录 `usb_sid_route_strategy=existing-disabled-context`，
并检查实际 EFI 中的策略日志标记。只满足主机检查不能宣称手机 USB 已通。

既有 context 候选已构建核验，SHA256：
`e2b27346542b70fad2f8a26274d937439d747c7c1c8d81df2860f5eea23b2147`。尚未实机启动，等待 fastboot 就绪。

## 既有 context 路由的首次实机结果

镜像 `e2b27346542b70fad2f8a26274d937439d747c7c1c8d81df2860f5eea23b2147`
已临时启动（Sending/Booting OKAY）。屏幕依次显示
`USB CONTEXT existing bank=5 checked=1 CBAR=000100FF SCTLR=000000E0 desiredS2CR=00000005`、
`USB ROUTE result=0 slot=0 SMR=80000040 S2CR=00000005 active=1 restored=0`、
读回 `SMR=80000040 S2CR=00000005`、保存值 `00000060/00020000`，
最后是 `USB ROUTE VERIFIED; first DMA permitted`。

按本树 `Silicon/Qualcomm/QcomPkg/Library/ArmSmmuDetachLib/SmmuV2.h` 的定义
（`S2CR.TYPE`：0=translation、2=fault；`CBAR.TYPE`：1=S1 translation/S2 bypass、
VMID 字段 0xFF），这些值与预期一致：SID 0x40 指向已禁用的 bank 5，
context 寄存器未被改写。寄存器写入与读回一致只说明路由已生效，
不能说明 DMA 已经通过。

随后 EP0 的初始 STARTTRANSFER 在重试后以控制器状态 1 失败，
控制器被停止（最后一行 `S1 read DCTL for run/stop`）。
主机侧 `ioreg` 与 `system_profiler` 中没有 `1209:0001`，
fastboot 与 adb 也为空。失败点已从"首次 DMA 挂住"变成
"首次 DMA 命令被拒绝"，但原因未定：可能 SMMU 仍在 abort 该 stream，
也可能是控制器在尚未收到 SETUP 时提前 armed 被拒绝。

## 下一次读数（v7）

v7 保持同一路由策略，只在失败路径增加只读快照：`GFSR` 与 `GFSYNR0..2`
（指出出错的 stream）、路由槽 `SMR/S2CR`、路由 context 的
`SCTLR/CBAR/FSR/FAR`，以及 DWC3 的 `DSTS/GSTS/DCTL/GEVNTCOUNT`
和事件环里尚未取走的 dword。快照不写寄存器、不清故障、不取走事件，
失败行为与不带快照时相同。

判读方法：`GFSR=0` 且无 pending event 说明 DMA 路径已通，
问题在 EP0 时序；`GFSR!=0` 且 `GFSYNR1=0x40` 说明该 stream 仍在 abort。

## v7 实机结果：EP0 已 armed，链路仍未建立

v7（`f178629e…c08ca`）启动后，路由读回与 v6 完全相同
（bank 5、`S2CR=00000005`、`CBAR=000100FF`、`SCTLR=000000E0`），
新增的 `USB ROUTE baseline GFSR=00000000` 表明安装本身没有触发故障。

与 v6 不同，这次初始 EP0 OUT STARTTRANSFER（`cmd=6`）成功，
日志继续到 `FUXI DWC3: S18 EP0 armed; enable connection`，
随后 `Dwc3RunStop(TRUE)`、`USBRAM S13 StartController returned Success`，
接收器进入 `USBRAM WAIT 1209:0001 ... idle timeout 180s`。
v6 的 status 1 拒绝没有复现；v7 与 v6 的差别只有失败路径上新增的只读
读数和日志（会放慢执行），因此那更像时序相关，而不是稳定故障。
这条还不能算结论，需要单独复测才能确认。

主机侧在整个窗口内仍没有 `1209:0001`：`ioreg`、`system_profiler`
都没有新设备，内核日志也没有 USB attach 事件；fastboot 与 adb 均为空。
所以下一个障碍不是路由，而是 USB 链路本身没有起来：
控制器报告 RUN_STOP 成功，主机却看不到连接。

## v8：链路采样

v8 在 `Dwc3RunStop(TRUE)` 成功后用 2 秒采样 `DSTS`：链路状态、连接速率、
SOF 号、`DCTL`（确认 bit31）、`GUSB2PHYCFG(0)` 和 `GEVNTCOUNT`，
只在链路状态、速率或事件计数变化时打印。判断方式：
链路始终停在 disconnected/ON 且无 SOF 前进，说明 PHY 没把设备拉到总线上；
速率与 SOF 正常而主机仍无枚举，说明问题在端点/描述符阶段。

## v8 实机结果：控制器在跑，链路始终 disconnected

v8（`600ab33f…ea12`）启动后唯一一行链路采样是：

`FUXI LINK t=0ms DSTS=… link=4 speed=1 sofn=0 DCTL=… PHYCFG=00102400 GEVNTCOUNT=0`

2 秒内没有第二行，说明链路状态、速率和事件计数都没有变化：
`link=4` 是 HS_DISCONNECTED，`sofn=0` 表示没有 SOF，`GEVNTCOUNT=0`
表示没有 connect/reset 事件。控制器侧没有报告停止，主机侧也没有枚举。

按本树 `Dwc3.h` 解码 `PHYCFG=00102400`：PHYSOFTRST=0（已释放）、
SUSPHY=0（未挂起）、ENBLSLPM=0、ULPI_UTMI=0（UTMI 模式）、
PHYIF=0（8 位）、USBTRDTIM=9（8 位 UTMI 的取值）。
也就是说软件看到的 PHY 配置是自洽的，但设备就是没有把 D+ 拉起来。
因此下一步不再是 SMMU 或端点，而是 PHY/术语层：
要么继承的 PHY 未被正确重新使能，要么我们在初始化时把 ABL 的状态改坏了。

## v9：与 ABL 状态做只读对比

v9 在 `UsbfnPlatformPhyInit`（控制器仍处于 ABL 状态时）抓一份
`GSNPSID/GCTL/PHYCFG/PIPECTL/DCFG/DCTL/DSTS/DEVTEN/EVTADR/EVTSIZ`
快照，并在 S17 审计清屏后与初始化完成后的同名快照一起打印，
标签为 `FUXI SNAP ABL` 和 `FUXI SNAP POST`。
ABL 那份是唯一被证明能连上主机的状态（fastboot 就工作在这个状态），
两份的差异直接指出我们改坏或漏配了哪个寄存器。
快照全部只读，不改变启动行为。

## v10 实机结果：ABL 与 POST 快照的差异

v10 重新把两条快照打印在链路使能附近，照片可以读到（部分数字受屏幕字号限制）：

- `FUXI SNAP ABL … GCTL=00102001 PHYCFG=00102400 PIPECTL=0B014102
  DCFG=00480E00 DCTL=10F00000 … DEVTEN=00000127 …`
- `FUXI SNAP POST … GCTL=00102000 PHYCFG=00102400 PIPECTL=0B014102
  DCFG=00480E00 … DCTL=90F00000（RunStop 之后）…`
- `FUXI LINK t=0ms DSTS=… link=4 speed=1 sofn=0 DCTL=90F00000
  PHYCFG=00102400 GEVNTCOUNT=0`

结论有三点。第一，`DCFG`、`PHYCFG`、`PIPECTL` 在 ABL 与 POST 之间完全一致，
所以 UTMI 位宽、速度配置和速率不是被我们改坏的。第二，唯一确定的差异是
`GCTL` 的 bit0（DSBLCLKGTNG）：ABL 置 1，我们的初始化把它清掉，
这是该驱动的常规行为。第三，ABL 快照里 `DCTL` 的 RUN_STOP 已经是 0，
说明交接时控制器已经被停住，因此这份快照不是"链路可用"的参照，
不能作为逐位对齐的目标。

因此链路起不来更可能发生在 PHY 本身：ABL 初始化好的外部 USB2 PHY
在 `Dwc3SoftReset` 里被 `GUSB2PHYCFG.PHYSOFTRST` 脉冲复位，
而本固件没有重新初始化该 PHY 的能力。

## v11：跳过 PHY 软复位

v11 增加 `FUXI_USB_KEEP_PHY`（构建脚本 `--keep-phy`，默认关闭），
只跳过 `GUSB2PHYCFG.PHYSOFTRST` 的置位/释放；核心软复位、时钟门控、
端点与 SMMU 路由都不变。镜像里保留一条
`FUXI USB: skip USB2 PHY soft reset` 标记，核验脚本在 `usb_keep_phy`
为真时强制要求它存在。

判读方式：若链路仍停在 link=4，说明问题不在这次脉冲，
而在 PHY 需要显式初始化（或 Type-C/供电通路）；若链路起来并出现
`GEVNTCOUNT != 0` 或主机枚举，则说明 ABL 的 PHY 状态必须保留。

## 根因方向：这台机器用的是 eUSB2 PHY

v11（跳过 `PHYSOFTRST` 脉冲）的链路仍是 `link=4`、`sofn=0`、`GEVNTCOUNT=0`，
主机没有任何 attach。也就是说，那次脉冲不是原因，而 PHY 需要显式初始化。

保存的 live DT 给出了决定性信息（`live-fdt.dtb`，SHA256 `3ac98f56…674e`）：

```
ssusb@a600000: qcom,use-eusb2-phy;
hsphy@88e3000: compatible = "qcom,usb-snps-eusb2-phy";
               reg = <0x88e3000 0x154 0xc276000 0x04>;
               usb-repeater = <&eusb2-repeater>;
eusb2-repeater@fd00 (PMIC): compatible = "qcom,pmic-eusb2-repeater";
```

即 DWC3 的 UTMI 口接的是一颗 **Synopsys eUSB2 PHY**，它的模拟前端是 PMIC
里的 eUSB2 repeater。之前的固件完全没有初始化这颗 PHY，
所以 DWC3 虽然 RUN_STOP=1，也拿不到可用的 UTMI 链路。

## v12：按上游序列初始化 eUSB2 PHY

v12 在 `UsbfnPlatformPhyInit` 里、DWC3 核心初始化之前，按上游
`phy-qcom-snps-eusb2.c`（19.2 MHz 参考时钟分支）的寄存器序列初始化
`0x88e3000`：CMN override、POR、PHY_ENABLE/RETENABLEN、APB logic reset、
FSEL、PLL 分频与偏置、TX 参数、VBUS 外部检测、SUSPEND_N，最后清 POR。
它不触碰 PMIC repeater、vdd/vdda12 和参考时钟——这些仍需 ABL 留下，
本固件没有对应驱动。

因为 `Dwc3SoftReset` 的 `PHYSOFTRST` 脉冲会把刚写好的 PHY 再复位，
v12 必须同时使用 `--keep-phy`。初始化前后各打印一条只读快照
（`FUXI PHY PRE` / `FUXI PHY POST`），核验脚本要求镜像里存在
`FUXI PHY eUSB2 init sequence applied` 标记。

判读方式：`PHY POST` 与上游写入值一致且主机出现 `1209:0001`，
说明这条路径成立；若 PHY 寄存器写不进去或链路仍为 4，
则问题在 PMIC repeater/供电/参考时钟，而不是 SoC 侧 PHY 序列。

## v12 实机结果：PHY 写入生效，但暴露两处缺失

v12 的 PHY 快照（照片可辨认部分）显示写入确实落到寄存器上：

- `FUXI PHY PRE … COMMON0=4B4B4B4B CTRL2=17171717 CFG0=00000000 …`
- `FUXI PHY POST … COMMON0=0B0B0B0B CTRL2=17171717 CFG0=02020202 …`

读回值按字节重复（该寄存器窗口按 8 位宽度呈现），因此
`COMMON0` 由 `0x4B` 变为 `0x0B`、`CFG0` 由 `0x00` 变为 `0x02`，
与序列的预期终值一致。但这也暴露了两个问题：

1. `PRE COMMON0=0x4B` 的 `FSEL` 字段是 4，即 ABL 用的是 **38.4 MHz**
   参考时钟，而 v12 却按 19.2 MHz 写了分频，PLL 会落在错误频率上。
2. 链路依旧 `link=4`。上游 `dwc3-qcom.c` 在设备模式启动前必须做
   VBUS/会话的软件覆盖：`QSCRATCH_HS_PHY_CTRL(0x10) |=
   UTMI_OTG_VBUS_VALID|SW_SESSVLD_SEL`、`QSCRATCH_SS_PHY_CTRL(0x30) |=
   LANE0_PWR_PRESENT`。缺少这一步时 DWC3 认为 VBUS/会话无效，
   永远不会向主机呈现连接——与"控制器在跑、链路却始终断开"完全吻合。

## v13：38.4 MHz 分支 + QSCRATCH 覆盖

v13 做两处修正：按继承的 `FSEL` 选择分频（4 → 38.4 MHz 用
`0xC8/0x0`，否则 19.2 MHz 用 `0x90/0x1`），并在 PHY 初始化后设置
`0xa600010`/`0xa600030` 的 VBUS/会话覆盖位，打印读回值。
其余（路由、跳过 PHY 软复位、只读快照）不变。

## v13 实机结果：PHY 序列落地，VBUS 可见，链路仍未建立

`Dwc3SoftReset` 的 PHY 脉冲被跳过、PHY 按继承的 38.4 MHz 分支初始化后，
快照显示 `COMMON0` 回到 `0x4B`（PHY_ENABLE|SIDDQ_SEL|RETENABLEN|FSEL=4），
`CFG0=0x02`（CMN override 仍为开），`HS_PHY_CTRL2(0x64)=0x17`，
其中 bit0 `VBUSVLDEXT0=1`、bit2 `USB2_SUSPEND_N=1`、bit4 `VBUS_DET_EXT_SEL=1`。

也就是说 PHY 看到了 VBUS，PLL 分频与 38.4 MHz 参考时钟一致，
但 `FUXI LINK` 仍是 `link=4 speed=1 sofn=0`，主机无枚举。
在 eUSB2 架构里，D+/D- 的上拉与驱动位于 **PMIC 的 eUSB2 repeater**，
SoC 侧 PHY 只是单端接口；本树没有 SPMI/PMIC 驱动，
所以 repeater 的使能/参数是否仍保持在设备模式无法由固件确认。

## v14：对齐更新后的上游序列并全量回读

对比更新后的上游 `snps-eusb2` 驱动，发现两处差异：`PHY_CFG_PLL_CPBIAS`
应取 0，且初始化结束必须清掉 `CMN_CTRL_OVERRIDE_EN`（我们之前一直留在开）。
v14 同步这两点，并把回读扩展到 16 个 PHY 寄存器两个批次打印，
同时打印 wrapper 的 `QSCRATCH GEN/HS/SS`（`0xa600008/0x10/0x30`），
以便确认 VBUS/会话覆盖位到底有没有写进去、ABL 原本是什么值。

判读方式：若 `CFG0` 变为 0 而链路仍为 4，则 SoC 侧序列已与上游一致，
问题基本落在 PMIC repeater 或供电；若 `QSCRATCH HS` 的 bit20/bit28
写不进去，则说明这个 wrapper 的布局与上游假设不同。

## v14 实机结果：PHY 回读完整，wrapper 仍不可见

v14 启动后链路仍为 `link=4`，主机未枚举。180 秒窗口结束后接收器正常收尾：
`USBRAM STOP status=time out; buffers retained; hardware result needs review`，
随后停止控制器并把路由恢复为 `SMR=00000060`（`release result=0 restored=1`），
说明新增的失败/退出路径没有破坏回滚。

PHY 回读显示 SoC 侧序列与上游一致（`COMMON0=4B` 保留 38.4 MHz、
`CTL1=00`、`CTL8=18`、`CTL9=48`、`CFG0=00` 即已清掉 common-mode override），
但 `QSCRATCH` 三分量在 `0xa600010/0x30` 上读回为 0，
与"VBUS 覆盖应该置位"的预期不符，说明这个基址很可能不是真正的 wrapper。

## v15：把 QSCRATCH 定位到 core + 0xF8800

Qualcomm 把 USB wrapper 放在 `core_base + 0xF8800`（DWC3 核心寄存器从 +0xC100 开始，
两者不重叠）。v15 把 VBUS/会话覆盖写到 `0xa6f8800 + 0x10/0x30`，
并在 PRE/POST 各回读一次新基址和旧的 `0xa600000+0x10/0x30` 作为对照。
判读方式：新的 `HS` 出现 bit20|bit28、`SS` 出现 bit24，且链路转为 0 或主机枚举，
说明 wrapper 基址就是它；若新基址读回也全 0，则 wrapper 位置还要再定位。

## v15 实机结果：链路首次真正连通

v15 把 QSCRATCH 覆盖写到 `core + 0xF8800` 之后，行为发生了质变：
控制器开始持续收到 USB 事件，屏幕反复出现

```
USBRAM: bus reset/detach; session cleared
USBRAM: speed=3            (偶发 speed=2)
```

并且 180 秒窗口结束、停止控制器时读到的 `DSTS=00002732`：
`USBLNKST=0`（U0/在线），`SOFFN=0x4E6` 且在推进，说明当时主机正在发 SOF、
链路是稳定在线的。这是本项目第一次让 DWC3 真正连上主机，
也确认了 wrapper 基址就是 `0xa6f8800`。

同时回滚路径仍然完整：停止后 `write offset=800 value=00000060`、
`write offset=c00 value=00020000`，`route release result=0 restored=1`。

主机侧仍看不到设备，说明卡在枚举阶段。这里的 `speed` 是
`EFI_USB_BUS_SPEED`，不是 DSTS 原始编码：2=Full、3=High、4=Super。
先前把 speed=3 解释为 SS 是误读；它本身不是链路速率异常的证据。

## v16：枚举跟踪

v16 不改行为，只加三处只读跟踪，用来判断枚举停在哪一步：

- 把原来的 `bus reset/detach` 拆成 `bus reset` 与 `bus detach`；
- 每个 `EfiUsbMsgSetupPacket` 打印 `rt/req/val/idx/len`；
- `Setup()` 返回失败时打印 `USBRAM: setup reply failed %r`。

判读方式：完全没有 setup 行说明主机 reset 后没有发出控制传输，
问题在 EP0 arm/链路速率；有 setup 行但紧接着 reply failed，
问题在 EP0 数据/状态阶段；有 GET_DESCRIPTOR 却反复 reset，
则要检查返回的描述符（尤其 512 字节 bulk 端点与最高速率的一致性）。

## v16 实机结果：主机 reset 后没有发出 SETUP

v16 的跟踪把链路事件拆开后看清了循环：

```
USBRAM: bus reset; session cleared
USBRAM: detach; session cleared
USBRAM: speed=3
```

整个 180 秒里**没有任何 `USBRAM: setup` 行**，也没有 `setup reply failed`。
说明主机 reset 之后从未把 SETUP 送到设备端代码，枚举停在
"reset 完成、控制传输未开始"。

对照驱动逻辑：`Dwc3Ep0ResetState()` 只结束了 EP0 IN，没有清 EP0 OUT 的
`TransferStarted`；随后 `CONNECT_DONE` 里的 `Ep0Rearm = !TransferStarted`
因此为假，`Dwc3Ep0OutStart()` 被跳过。USB reset 会作废已挂的 setup TRB，
所以 EP0 OUT 实际上再也没有被武装。上游 `dwc3_gadget_reset_interrupt()`
正是在每次 reset 后调用 `dwc3_ep0_out_start()`。

## v17：reset 后重新武装 EP0 OUT

v17 在 USB reset 分支里、`Dwc3Ep0ResetState()` 之后调用
`Dwc3Ep0OutStart()`，并在 `Dwc3Ep0OutStart()` 与 `Dwc3HandleEp0Event()`
增加不受 trace 预算限制的只读日志（`FUXI EP0: arm` / `FUXI EP0: evt=…`）。

判读方式：出现 `FUXI EP0: arm` 且随后出现 `FUXI EP0: evt=1 …`（XFERCOMPLETE
在 Setup 状态）说明 SETUP 已被接收；再配合 `USBRAM: setup rt=…` 即可看出
枚举推进到哪个请求。若仍无 SETUP，则问题回到链路速率/描述符协商。

## v17 实机结果：控制传输首次跑通到数据阶段

v17 修复了 reset 后不 arm EP0 的问题，屏幕第一次出现 SETUP：

```
USBRAM: speed=3
FUXI EP0: evt=1 status=12 ep=0 state=0
USBRAM: setup rt=80 req=06 val=0100 idx=0000 len=18
FUXI EP: submit ep=1 cmd=5 ...
```

`rt=80 req=06 val=0100 len=18` 就是主机的 GET_DESCRIPTOR(DEVICE)，
它证明 SETUP 已送达。先前把 `evt=3 ... state=1` 当成数据阶段完成不正确：
事件 1=COMPLETE、2=INPROGRESS、3=NOTREADY、7=EPCMDCMPLT。
主机记录读到了 VID/PID、64 字节 EP0 和 480 Mb/s，但没有成功配置接口或 Bulk
传输的证据，不能称为完整枚举验收通过。

但每次都在**状态阶段**失败：

```
DWC3: STARTTRANSFER failed on Physical EP 0, TrbType 0x40 Len 0 RscIdx 0 Ep0State 2
```

`status 1` 对应 `DEPEVT_TRANSFER_NO_RESOURCE`，已对照上游
`dwc3_send_gadget_ep_cmd()` 的命令状态处理和本树寄存器定义核实。
它表示命令没有可用传输资源，不能单凭这一值推出“bus reset 丢弃资源分配”。
此前基于后一假设制作了 v18，实机结果是回归；根因尚需实验验证。

## v18：reset 后完整重建 EP0

v18 在 reset 分支里先 `Dwc3EnableEndpoint(EP0_OUT/EP0_IN, CONTROL, 64, FALSE)`，
让驱动重跑 `Dwc3StartConfig()` 重新分配各端点资源，再 `Dwc3Ep0OutStart()`。
CONNECT_DONE 之后仍会用实际速率对应的 max packet 做 MODIFY。

判读方式：状态阶段不再报 `Status 1`，`USBRAM: setup` 序列继续推进到
`req=05`（SET_ADDRESS）、`req=09`（SET_CONFIGURATION）与
`USBRAM: configuration 1`，主机侧出现 `1209:0001` 并可用 libusb 打开。

注意：SET_ADDRESS 由底层驱动内部处理，不会出现在接收器的 `USBRAM: setup`
日志里。不能以缺少 `req=05` 判断失败；应查看 configuration、接口和实际自测。

## v18 实机结果与路线复核

已读取 Command Code 会话 `b8eb4f27-4573-453e-8e79-f6fa1ba407c7` 的文字与
操作历史，并查看本地 `out/usb-ram-v18/screen-evidence.jpeg`。原照片留在本机。
v18 能偶发收到 GET_DESCRIPTOR，但反复 reset，CONTROL_SETUP 的
STARTTRANSFER 也返回状态 1；主机无 1209:0001，发送器未打开设备。
这足以否定 v18 是修复的结论，不能证明所有 reset 资源生命周期假说均已被排除。

总体方向（USB 接收对象到 RAM、枚举后先做 1 MiB 校验）合理。此前把资源不足、
PHY/repeater 和 SMMU 回读的推测过早称作根因或端到端验证，证据强度不足。
这次继续保留已有链路配置，只改有直接代码证据的 EP0 生命周期。

## v19：完成时清除 EP0 资源索引

撤销 v18 在 bus reset 分支执行的两次 EP0 INIT，恢复 v17 的重新挂 SETUP。
在 EP0 XFERCOMPLETE 进入状态处理前清零 ResourceIndex，与通用端点及上游
Linux `dwc3_ep0_xfer_complete()` 一致。这样后续数据/状态阶段不会对已经完成的
资源发送 ENDTRANSFER。保留现有 reset 中止活动传输的逻辑。

日志增加 EP0 事件进入时的 `rsc`，ENDTRANSFER 失败同时记录端点、旧索引与
EFI_STATUS；没有修改其错误恢复策略。PHY、路由、Bulk 和描述符保持原实现。
H2（过期索引导致后续命令失败）是本轮实机待验证假说，不把主机模拟当成硬件结果。

上游对照（2026-10-23）：

- [EP0 完成路径](https://github.com/torvalds/linux/blob/master/drivers/usb/dwc3/ep0.c)：
  COMPLETE 清除活动标志与资源索引；正常控制阶段用 STARTTRANSFER。
- [资源配置和命令状态](https://github.com/torvalds/linux/blob/master/drivers/usb/dwc3/gadget.c)：
  命令状态 1 按无资源处理；DEPSTARTCFG 的全端点重置用于上电/软复位初始化，
  不能由状态 1 推导出 bus reset 时应重置全端点分配。

构建目录 `out/usb-ram-v19`；镜像 SHA256：
`0f76bd4b8c0316422ab5815d08b4d39f368a4104b541a3a98d04617dfa35cf1c`，
1,859,584 字节。14 项审计、20 项路由、21 项协议用例与交付镜像解包核验通过。
另有 5 项直接编译实际 Dwc3Endpoint.c 的模拟 MMIO 用例（ASan/UBSan）：
IN/OUT 三阶段、两阶段 SET_ADDRESS、活动 SETUP reset、活动 IN 数据 reset。
它们覆盖资源退休、避免过期 ENDTRANSFER 和 reset 不重建分配；不模拟真实 PHY、
DMA 或 USB 主机。v18 原代码和在 v19 恢复 v18 reset 改动的负向对照都被拒绝。

```sh
python3 scripts/check-dwc3-ep0.py --uefi ../uefi-fuxi \
  --edk2 /Volumes/External/VSCode/UniXNU/uefi-fuxi/Mu_Basecore \
  --report out/usb-ram-v19/ep0-tests.json
```

v19 构建完成时尚未启动。下一验收点仍是成功配置并打开
`ff/58/01` 的 Bulk 接口，然后在窗口内完成 1 MiB END 实际 RAM SHA256 校验。
若仍失败，应按 `evt/status/ep/state/rsc` 序列区分陈旧事件、状态阶段方向与资源
问题。不要在一轮同时改 PHY、SMMU 和描述符，也不先发送完整对象。

## v19 首次实机（2026-10-23 22:12，UTC+8）

核对 fcde91e / fuxi / unlocked=yes 后临时启动一次：Sending 与 Booting 均 OKAY，
总耗时 1.482 秒。紧接着自动启动自测，20 秒内始终未成功打开接收器。
主机 ioreg 捕获到 VID/PID=1209:0001、EP0=64、UsbLinkSpeed=480000000、
bNumConfigurations=1。随后 libusb 只重查一次发现/打开（未重试协议数据）：
它也读到同一设备描述符，但 active config=0，USBDeviceOpen 返回 0xe00002d8，
映射 libusb -99；本机 IOKit 头文件将 0xe00002d8 定义为 kIOReturnNotReady。
因此设备描述符可见的进度已恢复，完整配置枚举仍未通过；
没有发送 HELLO、BEGIN 或测试 payload，不能宣称 1 MiB 自测通过。

屏幕结果待用户照片，不能从主机打开错误独立判断 EP0 状态阶段故障是否已解决。
本地证据：device-test.json、host-usb.plist/json、self-test.log、
libusb-diagnostic.log、device-observation.json（均在 out/usb-ram-v19）。

## v19 照片复核：有效的零资源编号与提前切换 STATUS

IMG_6930.JPG 已保存为 `out/usb-ram-v19/screen-evidence.JPG`，SHA256：
`635483b7c272d3e5d9877640b8a69350e20b7f50360984c4c318ab1d1a20de1b`。
关键转录和解释见同目录 photo-analysis.json。

照片显示 EP0 OUT STARTTRANSFER 成功返回 `reg=00000006`，即资源索引 0；
EP0 IN 返回 `reg=00010006`，即资源索引 1。`Dwc3EndTransfer()` 原来以索引为
0 为由直接返回，导致仍活动的 EP0 OUT 被当成不存在，reset 重挂 SETUP 可能与
原传输冲突。上游 EP0 代码也明确允许结束资源编号为零的 OUT 传输。

另一组顺序是 `evt=3 status=2 ep=1 state=1 rsc=1`，紧接着发送
`cmd=10908`（resource=1 的 ENDTRANSFER），随后在 EP1 启动 STATUS。
此时仍在 DATA 状态，驱动不应因为一个 STATUS NOTREADY 就中止尚未完成的 DATA。
后面的 `evt=1 ... state=2` 只能证明完成事件到来时软件已切到 STATUS，
不能证明真正的状态事务已完成；它也可能是被中止 DATA 的迟到事件。
之后 SETUP（TRB 0x20）和描述符 DATA（TRB 0x50）均有资源不足错误。

v19 的模拟模型把 OUT/IN 索引设为 1/2，漏掉了硬件的 0/1 情况；原来 5 项通过
不能排除此缺陷。v20 模型按实际编号修正，并加入照片中的提前 STATUS 顺序。

## v20：用活动标志结束传输，等待 DATA 完成再启动 STATUS

在 v19 基础上，仅改 EP0 生命周期相关逻辑：

- ENDTRANSFER 根据 TransferStarted 判断活动状态，允许对索引为 0 的活动 OUT
  发出命令，仍不对已完成传输重复 END。
- DATA COMPLETE 后才切换到等待 STATUS；STATUS NOTREADY 在 DATA 阶段不再取消
  数据，也不启动状态 TRB。校验状态方向：三阶段与数据相反，无数据请求为 IN。
- 正常状态启动不再先发 ENDTRANSFER，启动成功后才标记 Ep0StatusStarted。
- EP0 事件日志加入 `run`（软件活动标志），避免把 rsc=0 直接解释为无活动。

对照 [Linux EP0 状态处理](https://github.com/torvalds/linux/blob/master/drivers/usb/dwc3/ep0.c)：
状态 NOTREADY 有下一预期事件门槛；完成 DATA 后才等待 STATUS，控制 OUT 的零资源
索引可用于 ENDTRANSFER。新增方向门槛也按控制传输方向约束验证。

9 项实际端点驱动模拟 MMIO 用例已通过 ASan/UBSan：原有 IN/OUT 控制事务、
SET_ADDRESS、reset，加上活动零索引、重复 END、照片事件顺序、无数据请求状态方向。
v19 基线、恢复零索引哨兵的 v20、恢复 v19 状态处理的 v20 三个负向对照均被拒绝。
这仍是模拟模型，不证明真实控制器的 DMA、异步 END 完成时序或主机 USB 行为。
PHY、SMMU、Bulk 和描述符未改。实机构建与交付结果见下一候选记录。

## v20 实机与两张照片

镜像 SHA256 `b9ecd4b042fbecbd2174ae888f0f16fff024393e5717e98f8f6a44e25e2384c7`，
1,859,584 字节；64 项主机用例与实际交付模块核验通过。
2026-10-23 22:21（UTC+8）临时启动，Sending/Booting 均 OKAY（1.535 秒），
立即执行自测。主机读到 1209:0001、480 Mb/s，libusb 仍报 config=0，
USBDeviceOpen 0xe00002d8 / -99，20 秒内未打开，没有发送协议数据。

IMG_6931 和 IMG_6932 的原始 HEIC 及用于阅读的 JPEG 均保存在
`out/usb-ram-v20`，哈希和关键转录见 photo-analysis.json。
前一修正确有可见变化：`evt=3 status=2 ep=1 state=1 run=1` 后，
出现 `evt=1 ... ep=1 state=1 run=1`，即 DATA COMPLETE 先到，再切到 STATUS。
但紧接着出现 `evt=1 ... ep=0 state=2 run=0`，软件把未活动 OUT 的完成事件当作
状态完成，重新挂 SETUP并遇到资源不足；后面 `evt=1 ... ep=1 state=0 run=0`
又被当成 SETUP，使用旧缓冲重复处理 GET_DESCRIPTOR。

这些行直接证明当前处理器未核对完成事件归属。事件是否来自旧 END/reset、属于
哪一代传输，尚不能只凭照片确定。新增照片还显示退出时
`Stop timed out! DSTS=0x00137FB1`，最终 buffers retained；这轮不能声称
控制器正常停止或 SMMU 路由已恢复。

## v21：验证完成事件属于当前活动控制阶段

只针对完成事件误判继续修改：在清除资源/切换阶段前检查 TransferStarted、
当前阶段预期物理端点、共享 EP0 TRB 的 HWO 已清除，以及 STATUS 是否成功提交。
未通过检查时不修改端点记账、不解析 SETUP、不报告数据成功、不重新武装。
额外的 ignore COMPLETE 原因日志最多 12 条；DATA NOTREADY 也仅在 DATA 阶段处理。

主机夹具现在与实际分配路径一致，让 EP0 IN/OUT 共享 TRB pool。
12 项 ASan/UBSan 场景覆盖原有事务及 v20 照片顺序、已取消传输的重复完成、
新 TRB 仍归硬件所有时的迟到事件、方向不符的完成。v20 基线、去掉 HWO 检查、
去掉方向检查的变体均被拒绝。模拟通过仍不证明真实异步 END、DMA 或主机交互通过。
如果 v21 仍失败，下一步应看 ignore 日志中的 want/armed/TRB 和实际 COMPLETE
先后关系，再决定是否需要等待 END 命令完成事件后才能重挂；本轮没有同时重做这部分。

v21 镜像已构建，SHA256：
`abcea15a6e9805c5caeb688f9927d5dc582f480f5d81b3beed8296ccc80508fe`，
1,859,584 字节。14/20/21/12 共 67 项主机用例及实际交付模块核验通过。
尚未实机启动，当前 fastboot 探测无设备；报告和下一轮命令见
`out/usb-ram-v21/REVIEW.md`。

v21 随后首次实机临时启动：Sending/Booting 均 OKAY，1.833 秒。
立即自测在 20 秒内未发现可打开的 1209:0001，libusb 调试日志未缓存到该设备
描述符，主机 ioreg 捕获同样无匹配项；没有发送协议数据。
与 v20 相比，此次主机观测没有恢复描述符可见，原因待屏幕照片确认。
重点应看 ignore COMPLETE 中的 want/armed/TRB，避免在没有事件证据时直接
认定 HWO、方向或活动标志哪个检查错误。实机证据见同目录 device-observation.json。

## v21 照片与路线修正

IMG_6934/6935 原始 HEIC、阅读用 JPEG、哈希和关键转录保存在
`out/usb-ram-v21` 的 photo-analysis.json。日志显示
`evt=1 status=12 ep=0 state=0 rsc=0 run=1` 被 HWO 检查拒绝，
共享 TRB 为 `00000C23/00000008`。随后 EP0 END 命令超时，程序仍继续提交
STARTTRANSFER、DEPSTARTCFG/SETEPCONFIG，最终 Stop 也超时，DSTS=0x00110991。

这确认 HWO 检查阻止了状态推进，但不能仅据照片断言硬件收到新 SETUP 后一定不
清 HWO，也不能排除这其实是旧 END 产生的完成事件遇上已重用的 TRB。
v21 的模拟模型默认 END 在命令寄存器返回后立即结束，又把 HWO 清零当成必需条件，
无法验证真实异步事件边界；那一条“去掉 HWO 的变体被拒绝”不能当成硬件正确性证明。

上游代码将 END 的待完成标志保留到 EPCMDCMPLT；Linux 端点文档也指出强制 END
不会立即完成，而且不会更新 TRB 进度或清 HWO。控制 EP0 完成路径不以 HWO 位
作为单独的完成事件否决条件。参考：
[Linux EP0](https://github.com/torvalds/linux/blob/master/drivers/usb/dwc3/ep0.c)、
[Linux gadget](https://github.com/torvalds/linux/blob/master/drivers/usb/dwc3/gadget.c)、
[Linux DWC3 文档](https://www.kernel.org/doc/html/v6.19-rc3/driver-api/usb/dwc3.html)。

## v22：异步取消与延迟重挂 EP0

EP0 END 命令提交后保留 EndTransferPending、活动标志、资源索引和共享 TRB，
不再靠固定 100 微秒后清空它们。重挂 SETUP 先排队，只有两个方向都收到对应的、
成功的 END EPCMDCMPLT 后才复用 TRB。等待期间的旧 COMPLETE 不解析为新阶段，
其他命令的 EPCMDCMPLT 也不能释放 END 待完成标志。

END 拒绝、超时或完成事件报告错误时锁住 EP0 的继续提交，保留未确认结束的所有权；
后续 reset/connect、SETUP、状态提交和接收器的直接 STALL 都不会继续向 EP0 发命令。
CONNECT_DONE 若在 END 待完成期间到来，先延迟，确认取消结束后才按协商速率 MODIFY；
已启用 EP0 不再因为暂时没有传输而走完整 INIT/资源重分配。

保留活动、方向及已提交 STATUS 的完成事件检查；撤销单独的 HWO 否决条件。
这不代表 HWO 总能忽略，Bulk 处理保持原实现。事件 7 日志额外显示 END done，
忽略 COMPLETE 的日志含两个方向的 end 标志。停止控制器的其他流程未改变，
v20/v21 的 Stop 超时仍是待实机复核问题。

主机模型改为显式延迟 END ack。18 项 ASan/UBSan 场景覆盖双向 END 交错、
共享 TRB 在 ack 前保持原样、CONNECT_DONE 延迟、无关/重复 ack、END 拒绝和超时、
失败事件、停机后的迟到 ack、不以 HWO 单独拒绝合法的 EP0 COMPLETE。
连同 14 审计、20 路由、21 协议用例，共 73 项通过。v21 基线、恢复 HWO 过滤、
去掉 SETUP 等待、去掉 CONNECT_DONE 延迟的四种负向对照均被拒绝。
这些结果只验证模型中的生命周期约束，不能证明真实取消事件顺序、DMA、枚举或正常停机。

## v22 实机通过：完整配置与 1 MiB RAM 校验

镜像 SHA256 `1ea39613b1ac09ba8350124c4b943e841cc852e0a2c160bb9c2435de0c957141`，
1,863,680 字节。最终镜像与当前 C/头文件哈希匹配，交付模块核验通过。
检测到 fcde91e 已在 fastboot 后临时启动一次，Sending/Booting 均 OKAY（2.035 秒）。

首次自测 20 秒发现窗口未打开设备，没有发送任何协议帧。稍后 ioreg 能读到产品名，
同一接收窗口内仅再次发现/打开一次，即时 1 MiB 自测成功（没有重传协议 DATA）。
成功报告为 `out/usb-ram-v22/self-test-recheck.json`：

```
object_id=0 bytes=1048576 ram_readback_verified=true
sha256=fbbab289f7f94b25736c58be46a994c441fd02552cc6022352e3d86d2fab7c83
partition_writes=false kernel_handoff=false
```

主机 USB 属性独立确认 configuration=1、interface=0、class/subclass/protocol=
ff/58/01、两个端点、链路 480 Mb/s。IMG_6936 照片确认
`USBRAM: configuration 1` 和
`USBRAM VERIFIED id=0 PA=C6428000 bytes=1048576 (RAM only)`。
照片和主机协议校验共同支持这次硬件门槛通过。控制传输数据/状态阶段可正常循环，
不再仅依据 VID/PID 可见判定通过。

完整证据在同目录 REVIEW.md、device-observation.json、host-usb-after-pass.json、
photo-analysis.json 和原始照片/日志。下一阶段为完整对象传输与停机验证；
不能把 1 MiB 成功扩大成大对象、跨 4 GiB 实传或正常回滚已经通过。
后续发现等待可放宽到 60 秒，仍只等待设备发现，不自动重试不确定的协议数据。


## 完整对象实机通过与 v23 停机候选

2026-10-23，v22 在同一次 USB 会话中完成两个完整对象的 END 实际 RAM SHA256：
OS 6,077,580,800 字节，SystemOS 3,303,014,400 字节，共 9,380,595,200 字节。
哈希分别为 e5634d04a8ac35f024845f583394e0a35deb26a04cb0c00286a8674e25a9a4f2、
b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a18b1600c96a3fd70414f。
原始报告在 out/usb-ram-full-v22/attempt-02-reopen/full-transfer.json；
源检查、发现、传输和 END 扫描合计 531.853 秒。

独立 v23 候选 SHA256 为
5fc9f064551b737edf0dc1154aaca85a2f0430f28ac99e7a8b672731fb3a5670，
临时启动成功，1 MiB 实机回归已通过。83 个驱动/协议主机用例及 6 个运行器用例
通过，实际 C 端点/停机测试启用 ASan/UBSan。交付模块及源码哈希已核验。

停机实现独立于一般事件处理：先 END 活动资源，再清 RUN_STOP，等待 halted 时
确认事件消费；这期间不重挂 SETUP 或 Bulk。只有 DSTS.DEVCTRLHLT 确认后才清理
端点和事件环并恢复 SMMU 路由。失败时保留 DMA 内存与路由，不继续新传输。
依据与反例测试见 out/usb-ram-v23/REVIEW.md、stop-negative-controls.json。

停机硬件门槛是 DWC3 STOP: HALTED、route release result=0 restored=1 和
USBRAM QUIESCE: Success。接收器 180 秒无通信后的 receive status=Timeout
是主动结束会话的原因，与独立的 QUIESCE 停机返回值不同。
此阶段不交接内核；USB 消失不替代屏幕停机证据。

v23 自然超时后主机已看不到接收器。IMG_6937 进一步确认
DWC3 STOP: HALTED DSTS=00C25CB0 events=0、route release result=0 restored=1、
USBRAM QUIESCE: Success，故正常停机与路由恢复的实机验收通过。
末行 receive status=Time out 是 180 秒无通信后退出接收的原因。
完整双对象和正常停机分别来自 v22、v23 两次会话；尚未交接 XNU。

## v24：双镜像到 DarwinBoot/XNU 的只读挂载候选

统一 12 GiB 窗口（基址 2 MiB 对齐、低 14 位 0x4000）：前 512 MiB 为内核与
启动数据，OS 6,077,580,800 字节自窗口+512 MiB 起（APFS 容器在字节 17,408），
SystemOS 3,303,014,400 字节在其后 2 MiB 对齐边界。两个对象的源 SHA256 固化在
UniXNURamObjects.h；接收器 END、DarwinBoot 采纳前、XNU 探针各做一次全量
RAM SHA256。

接收器在双对象 END 通过且停机（HALTED + 路由恢复）后发布 UR_HANDOFF 协议并
返回 DXE；失败仍 Park。DarwinBoot 要求 UrValid、窗口恰为 12 GiB、整窗被
EfiLoaderData 实际覆盖，采纳后不复制镜像，把 live 描述写入 ADT 的
RamSystemObjects（136 字节占位），TopOfKernelData 按 UR_RETAIN_END 计算。
XNU 新增两个只读 RAM 块设备（64 位偏移、字节偏移、拒绝写入/越界、>4 GiB
容量修正）与 RamSystemProbe：把 OS/SystemOS 只读挂载到 /mnt1、/mnt2，
停在第一个用户态服务之前（panic RAM_SYSTEM PASS）。

候选 SHA256 cccb58571e61fb4dd96cff95c7534f826a82734d972d5742f951cf09c745cc4e
（203,190,272 字节 = FV + 192 MiB payload FAT）。接收/停机机制源码与 v22/v23
实机通过版本逐字节一致；主机验证包含既有镜像核验（verify=0）与新增的
payload/ADT/loader 检查（check-usb-system-mount.py）。内核因 bootstrap pool
36 页修复重新构建（prelink 5c9c2348…051c）；固件含 DarwinBoot 的
CopyMem 类型与内存图 NumberOfPages 溢出防护两处修复，在 Linux 容器内重建。

实机验收点与步骤见 out/usb-system-mount-v24/REVIEW.md。
此时尚未交接实机：不宣称普通系统启动，不写分区，不进入用户态。

## v24 实机结果与打包修正（2026-10-24）

上一版交付镜像 `cccb5857…45cc4e` 已实机三次（00:08–00:33）：

- `attempt-02`：双对象完整传输通过——OS 6,077,580,800 与 SystemOS
  3,303,014,400 字节的 END 实际 RAM SHA256 均匹配，586.309 秒；随后接收器
  从主机消失。
- 但设备**没有**停在 panic，而是回到 UniXNU 菜单显示 `built-in configuration`
  + 红字 `no boot entry found`。交接链一步都没走到：DarwinBoot 从未被调用，
  12 GiB 窗口从未被采纳。因此上文 v24 条目里"尚未交接实机"之后的结果是
  传输通过、交接未到达。
- `attempt-01` 被运行器的 profile 门拒绝（未开始传输）；`attempt-03` 为无传输
  启动，屏幕只剩接收器初始化与 EP0 枚举跟踪，更早输出已被刷出屏外。

根因是**打包**，不是固件逻辑：固件用 `FuxiPayloadHeader()`
（`uefi-fuxi/.../FuxiPayloadDxe/PayloadBounds.h`）扫描 initrd，要求 FAT 引导
扇区的 **OEM 名字段（bytes 3..10）恰好是 `FUXIPAYL`**，其余 BPB 字段按结构
校验。上一版镜像的 ramdisk 直接由 `unixnu.bootimg.build` 装入，OEM 名仍是
mtools 默认的 `MTOO4049`，于是 `FuxiFindPayload()` 不匹配、`FuxiPayloadDxe`
静默返回、没有 SimpleFileSystem 卷发布，菜单回落到内置配置。逐条对照，该
ramdisk 满足除 OEM 标记外的全部条件。

`unixnu.bootimg.build` 只把 FD、DTB 和 ramdisk 装成 v3 boot image，**不打任何
标记**；唯一打标记的是 `scripts/package-fuxi-xnu-boot.py`，
`diagnostics/ram-root/README.md` 也写明它 "stamps FUXIPAYL"。历史实机通过的
`ram-progress-v5`、`ram-mem6g-high`、`ram-mem12g` 的 ramdisk 都是 `FUXIPAYL`，
正是由它打包。另注意 `check-usb-system-mount.py` 原先把 ramdisk 的 SHA 绑定到
未打标记的 `work/payload/payload.img`，等于把缺陷固化进验收并给出 verify=0。

本次修正：

- 交付镜像改由 `scripts/package-fuxi-xnu-boot.py --size-mib 192` 打包；
  `out/usb-system-mount-v24/finalize.py` 负责固件重建 + 打包 + 绑定 v23 主机
  测试记录。
- `scripts/build-fuxi-usb-ram.py` 的占位 ramdisk 改为打包器唯一接受的
  `b'dummy'`（与 `build-ram-window-candidate.py` 一致），固件构建产物留存为
  `base.img` 供打包器消费。
- `check-usb-system-mount.py` 改为校验 `FUXIPAYL` 标记、打包器 manifest 身份与
  BPB 结构，并分别记录标记前后的两个 payload SHA；`boot-ram-window-once.py` 的
  `usb-system` profile 增加 `payload_tag == FUXIPAYL` 要求，标记缺失时拒绝启动。
- 撤回三处 `CpuDeadLoop` 诊断驻留（`FuxiPayloadDxe` 的 10 个失败分支、
  `DarwinBoot` 拒绝路径、`UniXNULib` 配置未找到分支），已确认从交付 FD 中消失。

新候选 SHA256 `5a0290652cb05651084773ae2e734027fae388f1ee200c1af995b3c955f98e6c`
（203,190,272 字节；payload 标记后 SHA `1b783405…e3e3`、标记前 `3bb98638…ab7f`；
FD `3fc2b14c…`，prelink 内核 `5c9c2348…051c`），打包器 manifest 记录
`payload_tag=FUXIPAYL`。主机验证 `check-fuxi-usb-ram-image.py` 与
`check-usb-system-mount.py` 均为 0。**尚未实机。**

下一验收点仍是本条开头那座门槛，新增一项观察：本轮是第一次有接收器版本在
会话结束后进入菜单（此前都 Park），所以 UniXNU 菜单真正加载 `unixnu.toml`
时是否自动进入 `xnu` 条目在本机尚未验证过；若停在菜单等待按键，那是路径
走通而非失败。

## v24 接手实机：菜单已通过，交接几何被拒（2026-10-24）

打包修正后重新临时启动（`5a029065…`，attempt-04）：双对象传输与 END 实际 RAM
SHA256 均通过（OS 6,077,580,800 + SystemOS 3,301,014,400 字节，566.08 秒），
接收器从主机消失。**菜单第一次显示了我们的条目** `fuxi RAM diagnostic:
system-mount`——`FUXIPAYL` 修正实机成立，FAT 卷已发布、`unixnu.toml` 已读到。
同时确认：**菜单不会自动进入条目，需要手动选择**（此前所有接收器版本都以 Park
结束，从没进过菜单，这一点本次才第一次验证）。

选中条目后屏幕一闪而过：

```
DARWINBOOT: boot refused: Compromised Data; firmware retained
```

根因是交接几何，不是内核/内存/APFS：固件与加载器对窗口基址的对齐要求不一致。

- `FuxiUsbRamDxe` 把窗口放在 `ALIGN_VALUE(Raw, 2 MiB) + 0x4000`，`UrValid()` 也硬性
  要求 `Window & 0x1fffff == 0x4000`。
- `DarwinBoot` 采纳前要求 `(Objects.Window & 2 MiB-1) == (Kernel.Base & 2 MiB-1)`：
  bootstrap 用 2 MiB block 映射，窗口物理基址必须与内核链基址模 2 MiB 同余。
  `Kernel.Base` 是 kernel.macho 所有非空段 vmaddr 的最小值。
- 交付内核的 `__PRELINK_TEXT` 在 `0xfffffff00427c000`，低 21 位为 `0x7c000`。
  `0x4000 ≠ 0x7c000` → 第 637 行的 `||` 判 `EFI_COMPROMISED_DATA`。

这不是内核重建造成的漂移：**所有**现存内核构建（含实机成功启动 XNU 的
`ram-progress`）低 21 位都是 `0x7c000`。非交接路径没有这个问题，因为 DarwinBoot
自己按 `Kernel.Base` 选窗口，天然同余；只有交接路径把这个值写死成了 `0x4000`。

修正：`UniXNURamObjects.h` 新增 `UR_WINDOW_SKEW`/`UR_SKEW_MASK`，接收器与
`UrValid()` 都改用它；`check-usb-system-mount.py` 从交付 payload 的 kernel.macho
重新推导该值并与头文件常量比对，内核重建导致 prelink 段移动时在主机侧就被拦住。
另外把 DarwinBoot 交接分支的拒绝路径改为逐条打印具体原因（UR_HANDOFF 非法 / 窗口
大小 / skew 不同余 / 无 LoaderData 覆盖 / 最终 RAM SHA256 不符）并在拒绝时 Park：
菜单零点几秒内重绘，否则拒绝原因只剩一闪。该 Park 只在这一条分支触发，交接通过后
应重新评估是否保留。

新候选 SHA256 `e5b9593f81287e91971b206f5d2ed240ccd3f3aa713dda97f5bd82d1771f8cfe`
（203,190,272 字节；payload 打标后 `1b783405…e3e3`、标记前 `3bb98638…ab7f`；
prelink 内核不变 `5c9c2348…051c`）。主机验证 `check-fuxi-usb-ram-image.py` 与
`check-usb-system-mount.py` 均为 0，`window_skew=0x7c000` 与内核基址低 21 位一致。
**尚未实机。**

下一验收点：接收器枚举与双对象校验 → `USBRAM SYSTEM: window=… skew=7c000` →
停机 → 手动选中菜单条目 → `DARWINBOOT: adopted USB window` →
`final RAM SHA256 id=1/2 PASS` → XNU `RAM_SYSTEM /mnt1 /mnt2 mounted readonly`
→ panic `RAM_SYSTEM PASS`。仍未验证：12 GiB 窗口的 LoaderData 覆盖、`UR_FRONT`
的 512 MiB 预算、XNU 对 `UR_RETAIN_END` 之外页面的处理、34 扇区偏移的 APFS 挂载。


## v24 attempt-05：XNU 已进入，早期 VM 初始化 panic（2026-10-24）

镜像 `e5b9593f81287e91971b206f5d2ed240ccd3f3aa713dda97f5bd82d1771f8cfe`。
`out/usb-system-mount-v24/attempt-05/full-transfer.json` 确认 OS 6,077,580,800
字节、SystemOS 3,303,014,400 字节，两对象 END 实际 RAM SHA256 均通过，
565.909 秒，未写分区。上条 attempt-04 的 SystemOS 大小误记为 3,301,014,400；
当前报告与协议常量使用的是 3,303,014,400。

用户提供 IMG_6950.HEIC，已归档到同一 attempt 目录。照片明确显示 XNU panic：

```
kmem_range_init: vm_map_locate_space failing for claim compressor
@vm_kern.c:3731
```

因此这次已越过此前的 DarwinBoot 拒绝点并进入 XNU 早期 VM 初始化，
不是全量哈希等待，继续等待不会自行推进。未到 RAM_SYSTEM 只读挂载探针，
不能宣称 /mnt1、/mnt2 挂载通过或普通系统启动成功。照片未覆盖停机三标记和
DarwinBoot 最终哈希日志；主机 USB 消失不替代这些屏幕证据。
`full-transfer.json` 的 `kernel_handoff=true` 是 profile 配置位，
`normal_controller_stop_verified=false` 仍保持原值。

下一步定位 compressor 虚拟地址范围预留失败，检查 12 GiB 物理窗口与
XNU 虚拟地址规划的关系；尚未确定根因，未构建或临时启动下一候选。

## v25：修正 compressor 的早期 VA 预算（2026-10-24）

v24 attempt-05 的 `claim compressor` panic 已由真实 XNU 容量计算函数复现其
超预算条件：最坏连续 heap VA 约 25.68 GiB，固定 zone VA 24 GiB，原版
compressor claim 约 12.625 GiB（包括 freezer swap reserve），总和无法放入。
`kmem_add_extra_claims` 中的无符号剩余空间减法还会下溢。

新增 `native-compressor-early-freezer-mode.patch`，把现有
`-disable_freezer_cseg_acct` 处理提前到首次 `vm_compressor_set_size`；
挂载诊断 payload 同时加 `vm_compression_limit=131072`（4 KiB 页的 512 MiB 池）
与该 flag。未指定参数的默认行为保持一致，不跳过 panic，不改物理保留对象。
修正 claim 为 537,018,368 字节。按最坏 slide/对齐并保守扣除其他固定开销后，
余量下界 662,548,480 字节；该模型只适用于这份固定内核和布局。

新候选 `out/usb-system-mount-v25/`，SHA256
`163f05519cb001c97c15f710c7024e437be6f8eb95ba0aae582e1405e7fe5879`，
203,190,272 字节；prelink `4c56668b…7ba`，skew 仍为 0x7c000。
四项必需主机检查、payload 检查、真实 C sizing 的七项回归（ASan/UBSan）、
反汇编检查与启动门五种情况均通过。完整结果、命令和边界见候选 REVIEW.md
及 `diagnostics/ram-root/usb-system-mount-v25.md`。

用户已进入 fastboot，v25 attempt-01 已开始。尚待双对象传输与照片结果；
此处不宣称新候选已越过实机 panic 或已挂载 APFS。

## v25 attempt-01：双对象 END RAM 校验通过，待 XNU 照片（2026-10-24）

镜像 `163f0551…5879`，用户确认 fastboot 后，经 serial/product/unlocked
检查完成一次临时启动。OS 6,077,580,800 字节、SystemOS 3,303,014,400 字节
的 END 实际 RAM SHA256 都匹配，合计 550.602 秒。
记录：`out/usb-system-mount-v25/attempt-01/full-transfer.json`、
`host-observation.json`。接收器已从主机消失；正常停机三标记仍待屏幕证据，
保留 `normal_controller_stop_verified=false`。

已提示用户手动选择 `fuxi RAM diagnostic: system-mount`。
当前尚不能确认 XNU 越过 kmem_range_init、两个 APFS 卷挂载成功或
`RAM_SYSTEM PASS`。后续必须把最终照片结论追加回本日志与 REVIEW.md。

## v25 实机结论：KMEM 已通过，挂载 glue 返回 EINVAL（2026-10-24）

IMG_6951.heic 已归档于 out/usb-system-mount-v25/attempt-01，显示
`RAM_SYSTEM APFS mount failed id=1 errno=22 @RamSystemProbe.c:65`。
v25 的 compressor 修复在实机成立：XNU 已越过 KMEM 初始化并运行挂载探针。
按探针源码调用顺序，进入该失败点意味着两个对象的 XNU RAM SHA256 和
只读块设备注册已通过（源码推论；照片未直接拍到相应日志）。
/mnt1 失败，/mnt2 未尝试，未达到 RAM_SYSTEM PASS。

下一缺口已由源码锁定：mount_common 对 VFC_VFSLOCALARGS 的 kernel mount
只例外允许 by-role/BaseSystemRoot，尚未例外允许新增 RAM_SYSTEM 标记，
故在 VFS_MOUNT 之前就返回 EINVAL。设备路径 namei 也仍用 UIO_USERSPACE，
需要专门为 RAM_SYSTEM 内核字符串改成 UIO_SYSSPACE。下一候选 v26
将同时修正并严格限定 RAM 设备/路径配对与只读 apfs，实机验收继续停在用户态前。

## v26：只读 RAM APFS 挂载 SPI 修正候选（2026-10-24）

新增 zz-ram-system-mount-spi.patch：为 mount_common 的 local-args 内核挂载
拒绝条件加入 RAM_SYSTEM 专用例外，并把其设备路径的 namei 改为 UIO_SYSSPACE。
入口严格配对 /dev/md1→/mnt1、/dev/md2→/mnt2；保护要求内核、apfs、只读，
拒绝更新挂载，保留其他内核挂载原规则。v25 的 compressor 修复不变。

镜像 out/usb-system-mount-v26/Mu-fuxi-usb-ram.img，203,190,272 字节，
SHA256 `791840bcbcf95b77d85592e00bd9b148ed7716470306766ed4576f5c06aa9838`，
prelink `7c8bfa41…ad8a0`，skew 0x7c000。四项必需主机检查、payload/ADT
检查、KMEM 七项回归、真实 C 挂载入口/拒绝/路径回归（ASan/UBSan），以及
八种启动门情况均通过；启动门要求当前镜像/内核的挂载 SPI 报告。

详细步骤和限制见候选 REVIEW.md 及 usb-system-mount-v26.md。
当前等待 fastboot，尚未实机，不能宣称 APFS 挂载通过。

## v26 attempt-01：双对象 END RAM 校验通过（2026-10-24）

镜像 `791840bcbcf95b77d85592e00bd9b148ed7716470306766ed4576f5c06aa9838`。
确认无进行中脚本、fastboot 序列号 fcde91e、镜像与四份验证报告绑定后，
运行器完成 product/unlocked 检查和一次临时启动。OS 6,077,580,800 字节、
SystemOS 3,303,014,400 字节的 END 实际 RAM SHA256 均通过，551.584 秒。
报告位于 out/usb-system-mount-v26/attempt-01/full-transfer.json、host-observation.json。
未写分区，normal_controller_stop_verified 仍为 false；USB 消失不替代停机屏幕证据。

已提示用户手动选择 fuxi RAM diagnostic: system-mount。尚待 DarwinBoot/XNU
最终屏幕照片，不能宣称 /mnt1、/mnt2 挂载通过或 RAM_SYSTEM PASS。

## v26 实机照片：首次挂载 EBUSY，来源待定位（2026-10-24）

IMG_6957.HEIC 归档于 out/usb-system-mount-v26/attempt-01，显示
`RAM_SYSTEM APFS mount failed id=1 errno=16 @RamSystemProbe.c:65`。
错误从 v25 的 EINVAL(22) 变成 EBUSY(16)，但 /mnt1 仍失败，/mnt2 未尝试，
未达到 RAM_SYSTEM PASS。不能仅凭该 errno 宣称 APFS 已被调用或锁定某个原因。

已查源码：挂载点竞争、设备 mounted 标记、打开计数检查、文件系统回调及
挂载提交均有可能返回 EBUSY。当前没有分阶段证据，不跳过占用检查。
主机只读检查三输入的 NXSB，RestoreRamDisk/OS/SystemOS 的容器 UUID
互不相同，排除输入容器 UUID 直接重复；数值与分析见该候选 REVIEW.md
及 attempt-01/photo-observation.json。

下一步在 RAM 挂载专用路径记录失败阶段、设备号/打开计数、VFS_MOUNT
返回值，区分 VFS 前置检查和 APFS 拒绝。本轮未修改 v26 或制作下一候选。

## v27：RAM 挂载失败阶段诊断候选（2026-10-24）

针对 v26 首次挂载 EBUSY，新增 zz-ram-system-mount-trace.patch，仅在 XNU
RAM 专用路径记录阶段、设备号、vcount、设备/挂载点 flags 和 APFS 返回值。
原 kernel_mount 清理完毕后显示诊断 panic；不保留 vnode 指针，不跳过检查。
UEFI 接收流程、双对象布局与只读限制未改。尚未认定 EBUSY 根因或修复挂载。

候选 out/usb-system-mount-v27/Mu-fuxi-usb-ram.img，203,190,272 字节，
SHA256 `d48f32848b9f1da0b469b001dbbe2365430061ad27409ed7f30d1ef362d6328d`，
prelink `cd451c0c…103b1`。四项必需检查、payload、KMEM、挂载 SPI、启动门均通过。
新诊断检查在 ASan/UBSan 下覆盖 13 个故障注入、成功与普通挂载隔离，验证清理后
仍保留准确阶段，并核对清理代码与 v26 一致、编译来源和交付 prelink 的诊断字符串。

详细说明、命令和阶段解释见 usb-system-mount-v27.md 及候选 REVIEW.md。
当前仅主机验证通过；将核对 fastboot 后执行 attempt-01，最终结果须回填照片。

## v27 attempt-01：双对象 END RAM 校验通过，等待阶段照片（2026-10-24）

镜像 d48f3284…6328d。上机前确认无传输任务、fcde91e 在 fastboot，运行器
serial/product/unlocked 门通过，一次临时启动成功。OS 6,077,580,800 字节与
SystemOS 3,303,014,400 字节的 END 实际 RAM SHA256 都匹配，552.387 秒，退出码 0。
未写分区；记录在 out/usb-system-mount-v27/attempt-01/full-transfer.json、
boot-record.json、host-observation.json。normal_controller_stop_verified 保持 false。

已提示手动选择 fuxi RAM diagnostic: system-mount 并提供完整阶段照片。
尚未取得 XNU 结果，不能宣称 EBUSY 根因已确定、挂载已修复或 RAM_SYSTEM PASS。
后续读 at/dev/opens/spec/apfs/covered 定位，照片结论必须追加回填本日志与 REVIEW。

## v27 实机照片：EBUSY 来自 APFS 回调（2026-10-24）

IMG_6958.HEIC 已归档到 attempt-01，显示：
`RAM_SYSTEM mount failed errno=16`；`at=apfs-mount path=/mnt1`；
`dev=3000001 opens=0 spec=4`；`apfs=16 covered=84800 @vfs_syscalls.c:394`。
因此 APFS 的 VFS_MOUNT 确实返回 EBUSY；VFS 挂载点/设备占用前置检查已通过。
opens=0 是打开设备前原 vcount 的值，spec=4 是 SI_MOUNTING，不是 SI_MOUNTEDON。
/mnt1 仍失败，/mnt2 未尝试，未达到 RAM_SYSTEM PASS；继续等待不会推进。

下一步核对 RAM 专用挂载给 APFS 的参数与设备接口，定位 APFS 回调内部的拒绝。
当前尚未确定内部根因。控制器停机三标记不在照片中，原 false 结论保持。

## v28：补齐 APFS 附加卷参数候选（2026-10-24）

v27 照片已确认 APFS 回调 EBUSY。源码发现 RAM glue 将 fsmountargs 置空；
原始 21H461 APFS 的默认 mode 0 选择启动根卷路径，而非 /mnt1、/mnt2 所需的
附加卷挂载。对固定哈希 APFS 的 copyin 长度、mode 读取和真实跳转表审计确认
上述参数错误；尚未取得实机 APFS 内部拒绝断点，不能宣称所有 EBUSY 已被解释。

新增 zz-ram-system-mount-volume-args.patch（XNU）：构造 fspec 后 324 字节参数，
只读、mode=1、uid/gid=99，其余清零；解析 fspec 后保留参数；入口限定 kernel_task。
保留原 copyin 的 map 验证、APFS/VFS 检查、只读盘和 v27 阶段诊断。
APFS 原始可执行区、UEFI、DarwinBoot、USB 和对象布局未改。

候选 out/usb-system-mount-v28/Mu-fuxi-usb-ram.img，203,190,272 字节，SHA256
`836bd4bb609dafdda7a0347ea46078459143de6c5b0c1fe7506393c5354b16b3`，
prelink `9c1a1f44…957ac`。四项必需检查、payload、KMEM、SPI、trace、启动门全部通过。
新增参数检查执行真实抽取 C 与 ARM64 copyin 路径（ASan/UBSan），父 v27 解析块
作为置空参数负对照，同时核验原始 APFS 模式分支和未修改的可执行区。

详见 usb-system-mount-v28.md 及候选 REVIEW.md。当前未实机；fastboot 列表为空，
没有进行中传输，等待用户重启进入 fastboot 后运行 attempt-01。不得宣称挂载已通过。

## v28 attempt-01：双对象 END RAM 校验通过，待挂载照片（2026-10-24）

用户确认进入 fastboot 后，再次核对无传输任务、fcde91e 与 v28 镜像及六份
交付验证报告绑定。运行器 serial/product/unlocked 门通过，一次 fastboot boot
成功。镜像 SHA256 为 836bd4bb…4b16b3；OS 6,077,580,800 字节与
SystemOS 3,303,014,400 字节的 END 实际 RAM SHA256 均匹配，581.145 秒，退出码 0。
未写分区。记录在 out/usb-system-mount-v28/attempt-01/full-transfer.json、
boot-record.json、host-observation.json。normal_controller_stop_verified 保持 false。

已提示手动选择 fuxi RAM diagnostic: system-mount 并拍摄最终完整屏幕。
尚未取得 v28 XNU 照片，不能确认已越过 APFS EBUSY、两个卷挂载通过或
RAM_SYSTEM PASS。若仍失败，读取 at/dev/opens/spec/apfs/covered 后继续定位。
照片结论须追加回填本日志与候选 REVIEW.md。

## v28 实机照片：APFS 返回 ENOENT（2026-10-24）

IMG_6959.HEIC 已归档于 v28/attempt-01，明确显示 errno=2、at=apfs-mount、
path=/mnt1、dev=3000001、opens=0、spec=4、apfs=2、covered=84800，
位置 @vfs_syscalls.c:424。v28 已改变 APFS 回调结果（v27 为 EBUSY 16），
但 /mnt1 仍失败，/mnt2 未尝试，未达到 RAM_SYSTEM PASS。当前为 panic，不会继续推进。

仅凭 ENOENT 尚不能确认缺少哪个对象；不能把它直接等同于 /mnt1 或 /dev/md1
不存在，因为前置 VFS namei/占用检查已经通过。下一步检查 APFS 的设备名处理、
容器/卷查找与 IOMedia 发布，并补充回调内部日志和文件打开路径证据，避免猜测。
控制器停机三标记未出现在照片中，normal_controller_stop_verified 保持 false。

## v29：APFS 日志和实际打开路径诊断候选（2026-10-24）

v28 照片确认 APFS 回调 ENOENT，尚未确认具体缺失对象。只读检查显示普通卷
路径还会处理设备名、再次 vnode_open 并查找容器/卷/IOMedia，不能直接猜测修补。
新增 zz-ram-system-mount-witness.patch（XNU）：在 RAM 专用 VFS_MOUNT 前后用
原 bsd_log 锁保存最多 640 字节 legacy 日志尾部，原子 owner 限定挂载线程的
vnode_open 结果；失败屏幕增加 open 路径/错误/次数及 LOG tail。
不改原打印/日志路径、vnode_open 语义、APFS、只读条件或挂载错误清理。

候选 out/usb-system-mount-v29/Mu-fuxi-usb-ram.img，203,190,272 字节，SHA256
`f9068392e3a1539f1506a6b777dd64c3e9bef6f8f60ee81abd403060445257ef`，
prelink `ca42c99f…fa7b2`。四项必需检查、payload、KMEM、SPI、trace、参数与启动门均通过。
新 witness 主机检查在 ASan/UBSan 下覆盖范围/线程隔离、回绕、截断、控制字符、
缓冲区变化及无效情况，并核对实际编译来源、交付诊断字符串和原打开行为保持。
详细步骤与诊断限制见 usb-system-mount-v29.md 和候选 REVIEW.md。

当前 fastboot 列表为空，无运行中传输；等待用户重启进入 fastboot 后执行 attempt-01。
v29 尚未实机，未宣称解决 ENOENT、两卷挂载通过或 RAM_SYSTEM PASS。

## v29 后续：校准 APFS 离线模型，撤销伪偏移/块号结论（2026-10-24）

本轮只改主机分析工具，未操作手机，未构建 v30。镜像 SHA256 重算仍为
f9068392e3a1539f1506a6b777dd64c3e9bef6f8f60ee81abd403060445257ef。
将 analyze-apfs-ram-mount.py 默认额外偏移恢复为 0；NXSB 前的 32 字节
是标准 obj_phys_t，并非镜像私有头。加 0x20 的负对照明确返回 EFTYPE(79)。

修正 ioctl 命令误认：GETBLOCKSIZE 曾被当作 ISVIRTUAL 返回 1，
GETBLOCKCOUNT 曾返回字节容量，真实 ISVIRTUAL 的 4 字节输出又被写入
12 字节。现与 v29 实际 mdevioctl 对齐，buf_getblk 也改按当前设备扇区大小
寻址。真实 C（ASan/UBSan）与实际 Python hook 的 68 组接口/输出 canary
对照全部通过；另检查寻址、两盘对象头和 Darwin va_list 日志解码。

校准后 md1 第二次读取正确位于 0x3dd0000，原来的超大地址是模型错误。
md1/md1s1 假设别名/md2 分别读取 58/58/38 次元数据后，均在未建模的
reflock wait 边界明确停止；报告 completed=false、return_value=null，退出 2。
这些结果不能证明实机锁等待或 ENOENT 根因，也不能证明增加别名就能修复。
不再继续用无状态锁桩猜测整个挂载结果。

报告归档 out/analysis/codex-takeover，复现与边界详见 apfs-ram-model.md。
下一证据仍为原 v29 实机的 open/count/path 和 LOG tail；未宣称挂载通过。

## v29 attempt-01：双对象 END RAM 校验通过，待 witness 照片（2026-10-24）

用户确认进入 fastboot 后，检查无活动传输、序列号 fcde91e，并重新执行
镜像/固件源码绑定及系统 payload 检查，均退出 0；七份交付报告的镜像、
内核、XNU 源码、补丁和 compressor 编译记录绑定核验通过。
沿用的 SMMU audit/route/receiver 主机报告经当前源码身份核验，未修改交付镜像。

镜像 SHA256 f9068392e3a1539f1506a6b777dd64c3e9bef6f8f60ee81abd403060445257ef。
运行器 product=fuxi、unlocked=yes 门通过，一次 fastboot boot 成功。
OS 6,077,580,800 字节与 SystemOS 3,303,014,400 字节的 END 实际 RAM SHA256
均匹配，551.628 秒，运行器退出 0。未写分区。
preflight.json、boot-record.json、boot.log、full-transfer.json、host-observation.json
保存在 out/usb-system-mount-v29/attempt-01。

已提示等待接收器正常退出，手动选择 fuxi RAM diagnostic: system-mount，
拍摄完整最终屏幕，重点保留新增 open/count/path 与 LOG tail。
normal_controller_stop_verified 保持 false；尚无停机三标记或 XNU 屏幕证据。
kernel_handoff=true 仅是配置位；当前不能宣称解决 ENOENT、挂载通过或
RAM_SYSTEM PASS。照片到位后须同时回填本日志与候选 REVIEW.md。

## v29 实机照片：ENOENT 明确来自无效卷索引 -1（2026-10-24）

IMG_6967.HEIC 已归档到 attempt-01，显示 errno=2、at=apfs-mount、
path=/mnt1、dev=3000001、opens=0、spec=4、apfs=2、covered=84800，
位置 @vfs_syscalls.c:426。新增 witness 为 open=0 count=1 /dev/md1：
APFS 的再次打开已成功，不是设备路径不存在。

原始 APFS 日志显示 block size 512、block count 11870208，读取并选中
checkpoint largest/best xid 418 @3，stable desc 2/data 4；随后明确打印
nx_is_mount_valid:2306: md1 invalid fsindex: -1，再返回 ENOENT。
这将原始解析器 /dev/md1→index -1 与实机拒绝点联系起来，容器几何和
checkpoint 读取均已推进；不能再把此 ENOENT 归因为设备名查找失败。

/mnt1 仍失败，/mnt2 未尝试，未达到 RAM_SYSTEM PASS。panic 不会继续推进。
停机三标记未出现在照片中，normal_controller_stop_verified 保持 false。
下一候选拟仅为只读 RAM 容器发布第一卷设备名，并让专用挂载 SPI 选择该卷；
不修改 APFS、不跳过资格检查。照片、转写和结构化结论见 attempt-01。

## v30：只读 RAM 第一卷名称修复候选（2026-10-24）

根据 v29 照片明确的 invalid fsindex: -1，新增 XNU 补丁
zz-ram-system-volume-nodes.patch：在设置只读与精确边界后，为 md1/md2
发布 devfs 硬链接 md1s1/md2s1；失败立即拒绝设备注册结果。专用挂载 SPI
仍只许可原容器/路径配对，内部改选 s1 名称，使原 APFS 解析到卷 index 0。
两个真实容器的卷表均只有 index 0 有效。不修改 APFS 或 VFS 资格检查。

候选 out/usb-system-mount-v30/Mu-fuxi-usb-ram.img，203,190,272 字节，SHA256
3b3a181c5ad47087e4e08a5fe63eadfdce5c224ff2b02715a999fe5a10316c1b。
prelink b30b118e…58df9；FUXIPAYL、实际 kernel base 与 skew 0x7c000 均核验。
四项必需主机检查、payload/KMEM/SPI/trace/参数/witness/卷节点与启动门全过。
新增检查执行真实旧/新 C 注册路径（ASan/UBSan）及交付原始解析器八组输入；
启动门九种 mock 情况验证卷节点报告与镜像/内核绑定，未执行真实设备命令。

详见 usb-system-mount-v30.md 和候选 REVIEW.md。用户再次确认 fastboot 并授权启动，
复核无其他构建/传输、fcde91e、八份交付报告绑定后已开始 attempt-01。
尚待双对象 END RAM 校验与实机照片，不宣称挂载修复通过。

## v30 attempt-01：Booting 阶段 USB 读取失败，未开始对象传输（2026-10-24）

product/unlocked 等检查通过；fastboot 显示 Sending boot.img OKAY，随后
Booting FAILED (usb_read failed with status e00002ed)，退出 1。
运行器在 temporary-boot 阶段停止，OS/SystemOS acknowledged_bytes 均为 0；
未写分区。该错误不证明固件或 XNU 已运行，也不推断其 panic。
原始 boot.log、boot-record.json、full-transfer.json 与 host-observation.json
保存在 attempt-01。

用户明确要求重新启动后，重新检查无活动传输、fcde91e 已处于 fastboot，
镜像和八份报告未变，使用独立 attempt-02 开始一次新的临时启动。
未在接收器状态不明或数据传输期间自动重复 fastboot boot。

## v30 attempt-02：双对象 END RAM 校验通过（2026-10-24）

重新核对 fcde91e、无活动传输以及未变的镜像/八份交付报告绑定后，
运行器 product=fuxi、unlocked=yes 门通过；本轮一次临时 fastboot boot 成功。
镜像 SHA256 3b3a181c5ad47087e4e08a5fe63eadfdce5c224ff2b02715a999fe5a10316c1b。
OS 6,077,580,800 字节和 SystemOS 3,303,014,400 字节的 END 实际 RAM
SHA256 均匹配，561.043 秒，运行器退出 0。未写分区。

原始 preflight.json、boot.log、boot-record.json、full-transfer.json 和
host-observation.json 保存在 out/usb-system-mount-v30/attempt-02；失败的
attempt-01 独立保留。已提示等待接收器正常退出，再手选
fuxi RAM diagnostic: system-mount 并拍完整最终屏幕。

尚待 XNU/witness 照片，不能确认已越过 invalid fsindex、挂载通过或
RAM_SYSTEM PASS。normal_controller_stop_verified 保持 false；
配置 kernel_handoff=true 不替代 XNU 屏幕证据。后续照片须回填 REVIEW.md。

## v30 实机照片：越过卷索引拒绝，缺少 IOKit media（2026-10-24）

IMG_6968.HEIC 已归档到 attempt-02，显示 errno=1、at=apfs-mount、
/mnt1、dev=3000001、opens=0、spec=4、apfs=1、covered=84800，
open=0 count=1 /dev/md1，位置 @vfs_syscalls.c:430。

APFS 仍选中 xid=418 的稳定 checkpoint（desc 2/data 4），随后明确识别
md1s1 上的 DawnUpdate21H461.J120J121J207J208OS 卷，调用者 kernel_task。
这次已越过 v29 invalid fsindex: -1 拒绝点。新的原始日志为
apfs_vfsop_mount:2833: md1s1 fs iokit node was not found，随后
apfs_iokit_mount_eligibility not eligible, err: 1，最终回调返回 EPERM。
不是设备打开失败，也不是专用 wrapper 的 kernel_task 入参检查失败。

固定哈希原始 APFS 的只读反汇编确认：0x5671c78 按 IOMedia + BSD Major /
BSD Minor 匹配；0x56808c4 的 media 缺失分支返回 1。devfs 硬链接没有
发布 IOKit 存储对象。审计与原图/转写分别保存为 attempt-02 的
iokit-eligibility-audit.{json,txt} 和 photo-observation.json/photo-transcript.txt。

/mnt1 仍失败，/mnt2 未尝试，未达到 RAM_SYSTEM PASS，panic 后等待不会推进。
照片不含控制器停机三标记，normal_controller_stop_verified 保持 false。
下一步需要真正的只读 RAM IOKit 存储发布与受验证的卷身份；不跳过 APFS
资格检查。现有 NativeRamBlock 是可写 Data/持久化路径，且 RAM_ROOT_PROBE
明确禁止同时启用，不能直接套用其开关；本轮未构建或启动 v31。


## v31：只读 RAM IOKit 存储候选（2026-10-24，实机待回填）

v30 IMG_6968 已证明 s1 选择生效；新阻塞为原 APFS 缺少 IOMedia/BSD 身份，
资格检查返回 EPERM。v31 在 XNU 新增独立 NativeRamSystemBlock：实际 RAM
对象 SHA256 通过后借用只读 APFS slice，原 IOBlockStorageDriver/APFS 发布
真实卷节点。SPI 保留固定 md1→/mnt1、md2→/mnt2 输入，内部解析唯一、只读、
有效 BSD/UUID 且 provider 链属于本次 RAM 设备的 AppleAPFSVolume diskNs1。
最多 30 秒查询，歧义失败，附读取/发布统计；没有修改原 APFS 资格检查。
物理 UFS、可写 NativeRamBlock、持久化和用户态均保持关闭。

独立 out/usb-system-mount-v31 APFS clone 构建完成，四项必需主机检查及
payload/KMEM/SPI/trace/args/witness/只读 IOKit/启动门检查均退出 0。
新驱动 190 个真实 ABI 槽位、23 个覆盖通过显式静态 bring-up 审计；同一 C++
源码在 IOKit 依赖替身/ASan/UBSan 下通过 >4GiB 末扇区、边界、写入拒绝、
完成回调、卷归属、坏身份、歧义及超时测试；17 个 mock fastboot 门禁通过。
主机验证不能证明实机 metaclass/匹配/I/O/完整 APFS 挂载。

镜像 203190272 字节，SHA256
9a623fcd52e1d2e0bf26745a8598bc2f17b95a9d339e49501cc40f7b7baa716e；
prelink 8ea05e09ff61e89efd11f3353fb61b486f8124e12fd20ff2115649c7978d3b8c。
FUXIPAYL、kernel base 0xfffffff00427c000、重算 skew 0x7c000，KMEM 下界
662548480 均通过。UEFI/DarwinBoot 功能未改，组合镜像已重构建打包。

无活动传输，发现 fcde91e 处于 fastboot，准备按 REVIEW.md 一次临时启动
attempt-01 并传输 OS/SystemOS。暂未宣称实机挂载或 RAM_SYSTEM PASS。
结果及照片需同时追加本日志和回填候选 REVIEW.md。


### v31 attempt-01：Booting USB 读错误，尚无挂载证据

发送镜像 OKAY（4.202 秒），Booting usb_read failed e00002ed，退出 1；
serial/product/unlocked 门禁已通过。运行器停于 temporary-boot，两个对象
acknowledged_bytes 均为 0，没有协议数据或分区写入。原始记录已归档
attempt-01。之后只读 IOUSB 枚举未发现接收器，fastboot 列表也为空；
设备实际屏幕待用户确认，不据 USB 消失判定 HALTED，也未自动重启/重传。
v31 驱动实机匹配、节点发布、只读挂载与 RAM_SYSTEM PASS 均仍未验证。


### v31 attempt-02：用户明确要求再次临时启动

用户确认手机显示 fastboot 并说“你再进一遍”。重新确认没有传输任务，
fcde91e 在主机 fastboot 列表，使用同一镜像及九份绑定报告开始独立
attempt-02。命令见候选 REVIEW.md；上机/传输结果待回填。


### v31 attempt-02：双对象 END 实际 RAM SHA256 通过

用户明确要求重试后，本轮 fastboot 临时启动成功（Sending 4.202 秒，Booting
1.846 秒），OS 6077580800 字节、SystemOS 3303014400 字节均完成 END 实际
RAM SHA256 校验，总用时 560.733 秒，运行器退出 0，无分区写入。原始记录
在 out/usb-system-mount-v31/attempt-02，attempt-01 的 USB 失败记录保留。

源代码 ae963f4、设备集成 3503c1f 已推送到 mouse233 fork。接下来等待接收器
正常停止，拍停机三标记并手选 system-mount，再拍最终 IOKit/APFS/挂载结果。
当前没有 v31 XNU 挂载照片，不把传输成功或 kernel_handoff=true 当成挂载
通过；normal_controller_stop_verified=false，RAM_SYSTEM PASS 仍未验证。


### v31 attempt-02：最终照片确认 RAM_SYSTEM PASS（2026-10-24，首次）

用户手选 system-mount 后发来 IMG_6969.HEIC（已归档 attempt-02）。完整最终
屏幕显示 RAM_SYSTEM PASS、OS/SystemOS actual RAM SHA256=OK、
APFS readonly=1、/mnt1 and /mnt2 mounted，以及 userland not started;
partition IO absent @RamSystemProbe.c:80 与 TCR/SCTLR/pages/free。
v31 NativeRamSystemBlock 只读 IOKit 存储驱动实机匹配、卷节点发布与两个
APFS 只读挂载均通过，RAM_SYSTEM PASS 首次在实机达成；用户态未启动、
物理 UFS partition IO 关闭，与设计一致。

未验证：接收器停机三标记照片仍未提供，normal_controller_stop_verified
保持 false；屏幕未展示卷 BSD 名/UUID。下一验收点是用户态
（launchd/RestoreRamDisk），需单独候选决定从只读挂载推进用户态的路径。


### v32：只读系统卷 user-entry 候选（2026-10-24，实机待回填）

v31 IMG_6969 首次 RAM_SYSTEM PASS 后，下一验收点是用户态。v32 新增
`ram-system-probe=2`：双只读挂载与哈希校验不变，成功后内核校验
/mnt1/sbin/launchd（主机从未密封 OS 卷挂载实测，561856 字节 SHA256
8cec9a3c…b6a25，与恢复盘 launchd 字节一致），随后不再 panic，而是让
load_init_program 经新补丁 exec /mnt1/sbin/launchd，既有 exec 钩子改为
接受系统卷路径，PID1 在 EL0 首条 SVC 处被陷阱截停，预期屏幕
RAM_SYSTEM_USER PASS。根文件系统仍为 md0 RestoreRamDisk，系统卷是 exec
来源而非根；物理 UFS、可写 NativeRamBlock、持久化、recovery、SVC 派发
均保持关闭。

独立 out/usb-system-mount-v32 APFS clone 自 v31；探针档案以
--system-user-entry 生成 RamSystemProbePins.h，新补丁
zzzzzzz-ram-system-user-init.patch 注入 init 路径覆盖，
zzzzzz-ram-system-probe.patch 的 noreturn 改为按 XNU_RAM_USER_ENTRY 条件化。
源码 unixnu-source 62a9efd、UniXNU-fuxi 8060edb 已推 mouse233 fork。
检查脚本 check-usb-system-mount.py 支持双 profile 并强制系统 launchd 钉子。

镜像 203190272 字节，SHA256
68f9abe80249a3da8458dc53f92a211dde5aab43d10d976a4209fe16c12afeec；
prelink 6daf564156246b2fe7883d6e91d74a7b0e1afd77a902182ae6370c5327a0bd83。
v31 同集 12 项主机检查全部退出 0（smmu/route/receiver/image/mount/kmem/
spi/trace/volume-args/witness/iokit/gate，gate 为 17 个 mock 门禁）；
volume-nodes 未运行，其断言描述 v30 时代 md1s1 直连路径，v31 引入 IOKit
后不再成立且 v31 亦未运行。KMEM 剩余 VA 下界 662548480 与 v31 相同。
主机检查不证明实机 exec；user-entry 实机证据待 attempt-01 与照片回填。


### v32 attempt-01：双对象 END 实际 RAM 校验通过（2026-10-24）

fastboot Sending OKAY（4.220 秒）、Booting OKAY（12.643 秒），OS
6077580800 字节与 SystemOS 3303014400 字节均完成，object RAM SHA256 逐个
verified，总用时 583.681 秒，full_os_object_transfer_verified=true，
无分区写入。原始记录在 out/usb-system-mount-v32/attempt-01。
接下来等接收器正常停止（拍三标记）、手选 system-mount、拍最终屏幕；
RAM_SYSTEM_USER PASS 与 user-entry 实机证据待照片，不据传输成功推断。


### v32 attempt-01 实机：IOKit transport EINVAL panic，未达挂载（2026-10-24）

IMG_6972：手选 system-mount 后 XNU panic，屏幕
`RAM_SYSTEM IOKit transport refused id=1 errno=22 @RamSystemProbe.c:87`。
实际 RAM SHA256 校验已过，publish id=1 即被拒：驱动 configure() 门禁
硬编码 `ram-system-probe == 1`，profile 2 被拒。UEFI 接收、菜单、
DarwinBoot 到 probe 的链路实机走通。修复 unixnu-source 89a1bf1
（configure 接受 profile 2，只读发布契约不变；shim 与替身测试同步），
重建后交付镜像 203190272 字节 SHA256
53f3df5724b52478637bfe1f9dc4037474b7783cf96d73468a62f6ed2cb4c6f3，
prelink e0f0347b9a9f7ad217b7605aa5e1c6ba9ed4adf5fe1475ecb07040eea507043d，
全部检查报告重绑该镜像。主机替身测试不覆盖启动参数取值，此前未拦住，
已在 Test/transport.cpp 补 profile 2/3 用例（独立实例，占配置槽问题已修）。


### v32 attempt-02/03：启动门禁拒绝，未触达设备（2026-10-24）

两次 test-usb-ram-full.py 均在 boot-ram-window-once.py 候选验证门禁退出
（boot.log：candidate verification is absent or belongs to a different
image），未执行 fastboot boot，手机停留 fastboot 属预期。attempt-02：
并行检查脚本开头删除 payload-verification.json 与门禁读取竞态；
attempt-03：mount-spi/iokit-storage 报告仍绑定旧镜像 68f9abe8。
教训：写门禁输入的检查（check-usb-system-mount.py 与镜像绑定类）必须先于
传输完成，可与传输并行的只有源码树检查；spi baseline 须用 v25 源码文件
（v31 已含 SPI 修复会 SIGABRT）。门禁输入全部绑定 53f3df57 后发起
attempt-04；传输与实机结论待回填。


### v32 attempt-04：门禁通过后双对象传输成功（2026-10-24）

门禁输入全部绑定 53f3df57 后 fastboot Sending/Booting OKAY，OS
6077580800 字节与 SystemOS 3303014400 字节全部送达并逐对象实际 RAM
SHA256 verified，总用时 603.544 秒，full_os_object_transfer_verified=true，
无分区写入，记录在 attempt-04。normal_controller_stop_verified 仍 false。
后续：拍接收器停止标记，手选 system-mount，拍最终屏幕；
RAM_SYSTEM_USER PASS 与 user-entry 实机证据待照片，不据传输成功推断。


### v32 attempt-04 实机：RAM_SYSTEM_USER PASS，首次实机 user-entry（2026-10-24）

IMG_6974（归档 attempt-04）：手选 system-mount 后最终屏幕与预期逐行一致：

    RAM_SYSTEM_USER PASS
    root=/mnt1 apfs readonly=1
    system launchd exec=OK
    PID1 executed at EL0; SVC trapped
    pc=1006b6fb4 cpsr=0 esr=56000080
    x16=74 instruction=d4001001
    stopped BEFORE first syscall dispatch
    recovery programs not started @RamRootProbe.c:160
    TCR=0x00000022a51a251a
    SCTLR=0x0000000003454593d
    pages=0x000000000000ac664
    free=0x000000000000aa47e

解读（陷阱代码 RamRootProbe.c:160 及其前置校验为准）：stage 8、pid 1、
cpsr=0（EL0）、ESR EC=0x15 SVC64、指令 d4001001=svc #0x80 校验全过后，
按 user-entry 钉子 panic 截停。/mnt1 只读 APFS 系统卷上的 launchd 已被
load_init_program exec 并运行到其首条系统调用；x16=74 经 XNU syscall.h
查得 mprotect（dyld 早期页保护）。SVC 未派发、recovery 程序未启动、
根文件系统仍为 md0——user-entry 是 exec 来源验收点，不是完整 userland。
normal_controller_stop_verified 仍 false（本 attempt 未拍停止标记）。
下一步验收点：放开 SVC 派发与 recovery 程序，观察 launchd 真实用户态启动。


### v32 后续：v33 候选（系统 launchd 运行到 LC_MAIN+4，2026-10-24）

v32 验收后下一步：启用既有 XNU_RAM_MAIN_PROBE 机制（RamLaunchdProbe.c，
md0 线曾实机到达 stage=10 main=PASS）。SVC 真实派发，硬件断点钉 launchd
LC_MAIN+4，命中即 RAM_SYSTEM_MAIN PASS；fork/vfork/execve/posix_spawn/
mount/unmount/reboot/ptrace 派发前 panic；30 秒/2048 条 SVC 兜底。
改动层：package/RamLaunchdProbe.c（argv0 接受 /mnt1/sbin/launchd、PASS
屏幕区分 system entry）、scripts/build-ram-root-probe.py（放开
--system-objects 与 --main-entry 组合并要求系统 launchd 与 md0 launchd
字节一致）；断点钉子 main_entry_offset=85896 主机侧对 OS 卷 launchd 实
字节核验一致（系统卷与恢复盘 launchd 同为 561856 字节 8cec9a3c…b6a25，
该偏移曾在 md0 线实机验证）。ram-root-probe=2→3，ram-system-probe=2
不变；候选 config 需 CONFIG_XNU_RAM_MAIN_PROBE=y（克隆自 v32 时为 n，
首次构建在档案一致性检查处失败）。UniXNU-fuxi bf72331 已推 fork。
镜像 203190272 字节 SHA256
f5e13fcf31c7664a92dc3397c3002cebed20357bb0bed18468af64f20fde26b5，
11 项检查全部退出 0 并绑定该镜像（spi baseline v25 文件、witness parent
v28、volume-nodes 不适用同 v32）。attempt-01 传输与实机结论待回填。


### v33 attempt-02 照片与 v32 来源结论更正（2026-10-24）

用户提供 IMG_6978 2.HEIC，原件/PNG/转写已归档 v33 attempt-02。屏幕为
RAM_MAIN unexpected original launchd argv: argc=1 errno=0 @RamLaunchdProbe.c:162，
不是 RAM_SYSTEM_MAIN PASS；照片没有打印实际 argv。源码顺序表明这发生在
替换线程 FINAL_WAIT 放行前，未到 EL0/main；等待不会推进。

实际产物审计发现 v32/v33 kern_exec.o 均仅定义 XNU_RAM_USER_ENTRY=1，
没有 XNU_RAM_ROOT_PROBE=1，且未引用 fuxi_ram_system_user_init_path。
init 路径覆盖虽在补丁/生成源码中，却被预处理编译掉。结合 release init
路径可推论当时选择的是 md0 /sbin/launchd；不是照片直接观测到的字符串。
证据归档 v33 attempt-02/init-source-audit.json 与两版 load-init 反汇编。
因此撤回 v32“系统卷 launchd 已 exec”的来源结论；照片的 PID1/EL0/首条
SVC 截停证据保留。此前首次用户态表述也过大，md0 线 10-23 已达 user-entry。
根始终为 md0，旧屏幕 root=/mnt1 是诊断文字错误，不是换根的证据。

v33 attempt-01 的 USB 读失败及 fastboot 消失也不能证明“OKAY 应答丢失、
已进入接收器或正常停机”；此前确定性归因撤回。只确认 Sending OKAY、
Booting USB 读失败、运行器未发送对象；attempt-02 是用户再次启动授权后的
独立重试。所有 normal_controller_stop_verified 仍保持 false。

### v34：实际 system-init 编译修复与阶段契约（2026-10-24）

独立 APFS clone 自 v33，保留旧镜像/attempt。XNU 构建为 kern_exec.o 补
ROOT_PROBE=1，版本化 stamp 强制旧对象失效；新增 exec 前路径核对，系统模式
只接受 /mnt1/sbin/launchd，缺少来源校验不放行替换线程，不放宽 argv 保护。
成功屏幕分别报告 root=md0、exec=/mnt1/sbin/launchd，argv 错误显示 expected/actual。
ram-root-probe=3、ram-system-probe=2，pins 首条 SVC 截停=false，main 截停=true；
仍为 SVC 有界派发至 LC_MAIN+4、30 秒/2048 条上限及进程创建拦截。
未启用换根、物理 UFS、可写 NativeRamBlock、持久化或 recovery 程序。

修正 profile 3 拒绝测试，使用全新实例；故意让驱动接受 profile 3 的变异
现会被测试抓住。新增阶段策略/真实编译对象/探针档案/链接检查，原始 launchd
重新解析得 LC_MAIN=0x80000028、entryoff=85896；GLM 的“没有 LC_MAIN”是常量
漏掉 LC_REQ_DYLD 的误判。新检查入口逐项记录真实退出码及镜像身份，clone
不继承成功日志、检查报告或旧镜像身份，避免旧报告/管道掩盖失败。

v34 镜像 203190272 字节，SHA256
67f10f04db9393f6b63b47a47502ce1f8e51575ca148660c3676176027f55d12；
prelink c3837ccead2425ec09587a3158cf4c931ffef969525da3cd10b4a692891caf36。
14 项检查全部退出 0 并绑定该镜像：smmu/route/receiver/image/mount/KMEM/
SPI/trace/volume-args/witness/IOKit/gate/probe-policy/system probe-guards。
阶段策略含 34 项有效/变异/入口/旧缺陷验证，系统入口 C guard 48 项；另跑
普通 md0 main 46 项、首条 SVC 30 项通过。新对象实含 ROOT_PROBE/USER_ENTRY
及覆盖/来源/替换线程钩子，旧 v33 缺陷会被新检查拒绝。kernel base
0xfffffff00427c000、重新计算 skew=0x7c000，FUXIPAYL 与只读存储绑定通过。

用户已确认“已进 fastboot”，无活动传输、fcde91e 已核对，attempt-01 执行一次
fastboot boot 与 RAM 传输。命令/报告见候选 REVIEW.md。传输及新实机 main
结果待回填；主机检查不等于实机修复通过。


### v34 attempt-01：临时启动及双对象 RAM 校验通过（2026-10-24）

Sending OKAY 4.213 秒，Booting OKAY 9.046 秒；OS 6077580800 字节与
SystemOS 3303014400 字节均完成 END 实际 RAM SHA256 verified，总用时
581.749 秒，运行器退出 0，full_os_object_transfer_verified=true，无分区
写入。记录在 out/usb-system-mount-v34/attempt-01；14 项主机检查均已退出 0
并绑定镜像 67f10f04…。源码 95070f2/760c9c5 已推 mouse233 fork。

等待接收器停止标记、用户手选 system-mount 后的最终屏幕。当前不能宣称
RAM_SYSTEM_MAIN PASS，normal_controller_stop_verified 仍 false。


### v34 attempt-01 最终照片：system-init 生效，启动约束拒绝（2026-10-24）

用户 IMG_6980.HEIC 已归档 attempt-01，含 PNG、转写、photo-observation.json
和 launch-constraint-audit.json/相关源码片段。屏幕为 unexpected SIGKILL of
launchd (CS_KILLED)，namespace 3 code 0x4，Launch Constraint Violation，
error info c[1]p[1]m[1]e[2]，Constraint not matched，launch type 0，
failure proc [vc: 1]: /mnt1/sbin/launchd。不是 RAM_SYSTEM_MAIN PASS。

直接证据：这次 exec 的目标路径确实为 /mnt1/sbin/launchd，旧 init 覆盖未编入
导致 md0 fallback 的问题已修复。不能据此宣称 exec 完成、argv 校验通过或
PID1 已进入 EL0。源码顺序：process_signature 中启动约束钩子拒绝后设置
CS_KILLED、向 initproc 发送 SIGKILL，kern_sig.c 随即 panic；此处在
fuxi_ram_user_exec_ready/argv 校验/EL0 放行之前。先前即时口述“越过 argv
拒绝点”不够准确，现以本条顺序说明为准。

14 项主机检查仍全为退出 0，镜像/检查脚本/探针源码绑定复核一致（67f10f04…）。
新阻塞是 exec 安全策略的启动约束，具体失败条件尚未定位；不能仅凭错误
子字段断定信任缓存缺失、launchd 损坏或单纯路径问题。后续应审计原始约束
与运行时路径/卷身份/启动上下文的匹配关系，不跳过约束来宣称修复。
本轮未改运行时代码、未构建或启动新候选。根仍为 md0；没有停机三标记照片，
normal_controller_stop_verified 仍 false。panic 后继续等待不会推进。


### v34 启动约束深入审计：原始类别 2 要求根/授权 APFS 卷（2026-10-24）

只读审计固定哈希原始 AMFI 和 launchd，新增可复现脚本
scripts/analyze-ram-launch-constraints.py；详细报告/DER/机器可读结果在
v34 attempt-01/launch-constraint-detail。原始 launchd 签名没有槽8/9/10
的嵌入约束；其 CDHash 1d52c69747dffc4b1937d241bdbc9cee3b22df18 在原始
StaticTrustCache v2 第279项（零基）的 constraintCategory=2。原始 Restore
v1 第32项也匹配但没有类别字段，v34 未加载它，本轮没有修改缓存或优先级。

实际执行原 AMFI 类别2选择函数，提取并独立解码75字节 DER：
on-authorized-authapfs-volume=true OR on-system-volume=true。
没有路径/identifier/launch-type 条件。固定二进制数据流表明照片 c1/p1/m1/e2
是类别型 self 检查不匹配，e2 在该路径为类别2，不是 errno/ENOENT。

原始 on-system-volume 叶实际读取 vnode_mount→vfs_flags 的 bit14，即
MNT_ROOTFS=0x4000；仅只读不够。原始授权 APFS 叶检查 VNOP_IOCTL 请求
0x80084118 是否返回0。Unicorn 直接执行未修改的两个 AMFI 叶，9项接口
模型用例通过；不等于完整回调模拟或手机 ioctl 实测。AMFI __TEXT_EXEC
SHA256 a1088aaa…41db6，镜像仍67f10f04…，输入/策略未修改。

v34 /mnt1 是次级只读挂载、根仍为md0，故非系统根是源码/角色推论；结合
实机类别2 OR失败可推论授权 APFS 条件也未满足，但具体手机flags/ioctl返回
仍待只读观测。这解释同字节launchd在md0根可过、换成/mnt1来源却拒绝，
不能推广成其它 AMFI 检查已通过。下一步可先观察真实卷身份/授权ioctl，
再设计真正系统根挂载；改argv、chroot或路径名不能解决该根标志要求。
本轮未构建/启动新候选，未修改AMFI、信任缓存或挂载flag来绕过拒绝。


### v35：原始启动约束的旁路实机观察候选（2026-10-24）

自 v34 创建独立候选。目标是记录原始 AMFI hook 期间目标 launchd 卷的
vfs_flags 与授权 VNOP_IOCTL(0x80084118) 实际返回，不主动发起额外 ioctl。
XNU mac_process 在原 hook 前后限定作用域；kpi_vfs 只旁路记录原始返回，
kern_sig 在原始 init SIGKILL 原因后附加 RAM_LC_OBS。原始 hook 错误合并、
原因、CS_KILLED、SIGKILL 和返回路径均保留；原始 AMFI/APFS 可执行代码
SHA256 不变。UEFI/DarwinBoot 功能未改，组合镜像已重构建打包。

独立开关 CONFIG_XNU_RAM_LAUNCH_OBSERVE=y、archive
--launch-constraints-observer、pins observe_launch_constraints=true、boot
ram-launch-observe=1；其它候选默认关闭。仅 stage7/PID1/system-entry/AMFI/
profile2 首次回调，按 owner thread 与 exact vnode/mount 过滤；观察未出现
明确显示 unseen，返回/flags 变化会显示 change=1，不把未观察到当作0。
根仍为 md0，/mnt1 仅 exec 来源；保留 v34 的有界 main/SVC/进程创建保护，
不修改根标志、信任类别或安全判定来放行。

15 项主机检查全部退出0并绑定镜像：v34 同集14项加 launch-observer。
新测试提取真实 patched XNU 包装及MAC调用块，连同实际观察C，在开关0/1
下各31用例通过ASan/UBSan；覆盖作用域/外部线程/对象拒绝、unseen、变化、
flags/errno透传、ENOTTY/SSD提前返回、每次原始dispatch调用次数、原理由
指针和原MAC错误合并保留。编译对象/探针档案/pins/源码/链接绑定均通过。
首次档案编译发现 proc_getpid 私有声明缺失，补 sys/proc_internal.h 后重建
成功；交付镜像为新产物，没有沿用父候选镜像或成功日志。

镜像203190272字节，SHA256
54bd42bb6b5a58a241918a5928e1acbb9aeeba93ce4944dc7c8ca1868c4c4f6a；
prelink 66fff7096dff62f8e116cbf59617768cb9c2351940825a2e60a9a58439e7ae8c。
FUXIPAYL、kernel base 0xfffffff00427c000、重新推导 skew=0x7c000 通过。
用户确认“已进 fastboot”，核对无传输任务与 fcde91e 后发起 attempt-01。
命令/报告见候选 REVIEW.md。RAM_LC_OBS 实机字段尚未取得；传输结果待追加，
normal_controller_stop_verified=false，不能据主机通过宣称约束已修复。


### v35 attempt-01：双对象 RAM 回读校验通过，等待观察照片（2026-10-24）

一次 fastboot boot 成功：Sending OKAY 4.231秒、Booting OKAY 1.397秒。
OS 6077580800字节与SystemOS 3303014400字节均完整确认且接收端实际 RAM
SHA256 verified，总用时580.219秒，运行器退出0，无分区写入。
full_os_object_transfer_verified=true；报告归档于
out/usb-system-mount-v35/attempt-01/full-transfer.json。
15项主机检查均已退出0，绑定镜像54bd42bb…4c4f6a。源码62739cd/467af51
已推送mouse233 fork。

等待接收器停止标记、用户手选system-mount及包含原始错误与RAM_LC_OBS的
完整照片；实机flags/ioctl字段尚未知。normal_controller_stop_verified仍
false；kernel_handoff配置位、传输成功均不是进入XNU或exec/EL0/main通过
的证据。本候选仅观察原始拒绝过程，尚不能宣称启动约束已修复。


### v35 attempt-01 最终照片：rootfs=0、授权ioctl=EAUTH（2026-10-24）

用户IMG_6984.HEIC原件/PNG/转写/photo-observation.json及PHOTO-REPORT.md
归档到本次attempt。原始launchd CS_KILLED、namespace3 code0x4、Launch
Constraint Violation、c[1]p[1]m[1]e[2]、launch type0、/mnt1/sbin/launchd
仍保留。新增观察原文：

```
RAM_LC_OBS err=1 fatal=1
flags=14909001 rootfs=0 reads=6 change=0
auth=80084118 errno=80 calls=3 change=0
```

真实探针以%llx输出flags，故值为0x14909001，MNT_ROOTFS(0x4000)未置位。
目标errno.h定义80=EAUTH；hook err1=EPERM，二者不是同一层返回。fatal1
表示原理由长度非零。观察范围内同线程、目标mount/vnode的6次flags读取、
3次现有ioctl返回分别无变化，不把计数当作类别评估次数。实际VNOP_IOCTL
包装提前返回仅ENOTTY25或0，故80来自原有vnode操作dispatch；APFS内部
具体授权失败分支尚未定位，不据此断言密钥/签名/seal或镜像损坏。

结合固定原始类别2 DER，根卷与授权auth-APFS卷两个OR条件均未满足的
推论现获实机支持。v35观察目标完成，exec/EL0/main仍未通过，panic后等待
不会推进。15项既有主机检查均退出0，交付镜像完整SHA256及当前检查脚本
哈希与报告重新核验匹配；没有重新构建、启动或改运行时代码。

下一步审计原生系统根建立流程与RAM媒体接入时序，核对认证、rootvnode、
/dev/旧根引用和只读边界后再做独立候选；不把给次级挂载补root位、改信任
类别或改安全判定当修复。照片未提供停机三标记，normal_controller_stop_verified
仍false；不能把观察成功写成完整系统启动。


### v36：原生根认证预检与系统根时序审计（2026-10-24）

新增system-root-audit.md：当前RAM媒体发布在md0根与devfs之后，/mnt1、
/mnt2仅次级挂载。bsd_rooted_ramdisk由rd参数判定，不能提前换rootdev却
继续用rd=md0跳过原生认证。原生iOS根认证请求FSIOC_KERNEL_ROOTAUTH为
0x80046833，与v35的AMFI授权查询0x80084118不同。原生vfs_switch_root有
目录/引用/挂载搬移前置要求，不能简化成补root标志。

独立v36从v35创建。XNU探针档案新增可选RamSystemRootPreflight.c，根仍md0，
system-user/main/progress/observer关闭；在system stage6的noreturn预检中，
先检查kernel task/PID0/阶段、md0根及两个不同只读APFS卷根，再每卷发一次
与原生bsd_init同参数的根认证请求并如实报告返回，最终停在PID1之前。
这是主动请求原生认证，不是被动读取。另记录前后flags和同卷dev、private/var、
System/Volumes/Restore/BaseSystem目录errno，不切根、不改信任类别/判定。
mount-only init接口补为意外到达即停止，防止回退md0 init；原生kern_exec
ROOT_PROBE/USER_ENTRY宏保持1并验证编译对象，没有以关闭宏掩盖缺符号。

主机build-kernel→Docker固件→FUXIPAYL打包完成；13项检查真实退出0并绑定
交付镜像。新C31用例和两项突变拒绝通过ASan/UBSan；原始AMFI/APFS/存储
代码哈希保持父候选一致。首次unused buffer及mount-only缺init符号均已
修复重建。镜像203190272字节，SHA256
3f9d16c07e10c77f90ebfe0a6bf37736c1d8b0f0db6f30e8b98598ce99e403fd；
prelink c9ca22b31247ad1942f931bb1871134a4138344c1dbb2d6e706aa7193d81acb1。
base0xfffffff00427c000，重算skew0x7c000，KMEM剩余VA下界662548480。

用户确认“已进入”，核对互斥无传输且fastboot显示fcde91e，发起attempt-01。
命令、报告见候选REVIEW；启动/传输及实机结果待追加。原生rootauth在次级卷
的成功/失败仍受挂载和启动上下文影响，不等于真正系统根建立或完整启动。
normal_controller_stop_verified=false。

### v36 attempt-01 启动失败与 attempt-02 重试（2026-10-24）

attempt-01于23:26发起：fastboot Sending OKAY（198428KB/4.199s）后
Booting FAILED（usb_read failed with status e00002ed），fastboot退出1，
test-usb-ram-full以temporary boot failed退出。两对象acknowledged_bytes
均0，未发任何协议DATA，无分区写入；属主机侧boot应答读取失败，不是
接收器或XNU结果。事后设备回fastboot可见。失败证据保留在attempt-01/，
REVIEW.md已回填。

23:35重核对互斥与fcde91e、镜像SHA256后发起attempt-02（新目录，脚本
拒绝复用失败报告）。本轮Sending与Booting均OKAY，boot_returncode=0，
已进入discovery并开始双对象传输；RAM SHA256校验与实机结果待回填。

### v36 attempt-02 双对象传输校验通过（2026-10-24）

attempt-02临时启动成功（Sending/Booting均OKAY，boot_returncode=0）。
597.8秒完成双对象传输：OS 6,077,580,800字节、SystemOS 3,303,014,400
字节，实际RAM回读SHA256均匹配（e5634d04…/b4908cde…），无分区写入。
full_os_object_transfer_verified=true；normal_controller_stop_verified
仍false，待停机三标记照片。候选代码与检查已提交41a6fbf并推送fork。

待实机：停机三标记照片后手选 `fuxi RAM diagnostic: system-mount`，
照片须完整包含RAM_ROOT_PREFLIGHT的auth返回、flags前后值、目录errno
与unchanged字段；auth=0不等于系统根建立，结果待照片回填。

### v36 attempt-02 最终照片：SystemOS 原生根认证成功（2026-10-24）

用户IMG_6986原件/PNG/转写/photo-observation.json及PHOTO-REPORT.md
归档到attempt-02。探针自证前置全过后输出：root=md0 unchanged=1
userland=0；OS（/mnt1）auth=80 EAUTH，SystemOS（/mnt2）auth=0成功；
两卷flags前后均0x14909001未变；/mnt1有dev与private/var目录，
/mnt2卷根四项目录全ENOENT。无切根、无用户态，panic为预期停止。

首次实机证明授权卷身份可在次级只读挂载上通过原生
FSIOC_KERNEL_ROOTAUTH建立。边界：auth=0不等于vfs_switch_root前置
满足、launchd自/mnt2 exec通过AMFI类别2或完整启动；rootauth成功与
授权分支的关联仍是待验证假设；真实切根到/mnt2将缺dev/private/var
等目录，/mnt1有目录但认证失败。照片无停机三标记，
normal_controller_stop_verified仍false。

下一候选两个具体问题：launchd从/mnt2 exec是否满足授权分支；/mnt2
切根目录前置如何补足。仍不以改ioctl返回、补MNT_ROOTFS位或改信任
类别当修复。v36观察目标完成。

### v37：原生 vfs_switch_root 切根预检（2026-10-25）

主机侧原生挂载确认卷身份：/mnt1为DawnUpdate21H461…OS（完整iOS系统卷，
sbin/dev/private/var俱全，Sealed=No，features=0x12）；/mnt2为…SystemCryptex
（System/usr/lib框架内容，无sbin无launchd，Sealed=Yes，features=0x2）。
v36的auth=0/80与密封状态一致：rootauth验证卷密封，OS卷restore预装态
无密封故EAUTH，此启动路径不可修复；/mnt2无launchd，"从/mnt2 exec"路线
否决。imageboot pivot在切根前认证且失败即放弃；直接调用vfs_switch_root
无认证前置。审计确认其前置仅为入根真卷根、旧根安置目录、保留挂载点
目录，flags=0不查虚拟设备；rd=md0使bsd_init根认证门跳过，切根在门后
不冲突。

独立v37从v36创建，新archive --system-root-switch（ram-system-root-switch=1，
ram-root-probe=2，system-user/main/progress/observer关闭）。stage6
noreturn探针先校验md0根、两互异只读APFS卷根、/mnt1上Developer安置目录
与dev目录、/mnt1/sbin/launchd字节SHA256 pin，释放全部vnode后调用
vfs_switch_root("/mnt1","Developer",0)；成功后核验换根身份、MNT_ROOTFS
由vfs_subr原生落位/清除、/Developer旧根、/dev仍devfs、新根/sbin/launchd
字节不变，记录换根后rootauth原始返回（不设门），停于PID1前。

主机build-kernel→Docker固件→FUXIPAYL打包完成（首次finalize因容器
停机失败，重启后成功）。13项检查真实退出0并绑定交付镜像；新
check-ram-system-root-switch.py以真实C跑42用例与三项突变拒绝
（ASan/UBSan零报告），并绑定vfs_subr的MNT_ROOTFS契约与原始执行体
哈希。镜像203190272字节，SHA256
59c76a5376df295ed9aa3fe32a3ab9930b6bf29ae636e747333554f2bc010dd8；
prelink da439dd864342ab5…；base 0xfffffff00427c000，skew 0x7c000，
KMEM剩余VA下界662548480。

上机与实机结果待追加。预期验收：pivot=OK、rootfs落位、launchd=
/sbin/launchd SHA256=OK、userland=0；auth行仅记录（EAUTH80或0都不
改变本轮停止点）。pivot任何失败按errno如实panic。换根成功不等于
launchd exec通过AMFI类别2，那是v38的问题。

### v37 attempt-01 临时启动与传输发起（2026-10-25）

用户回复"已进入"。操作前核对无活动传输，fastboot显示fcde91e，镜像
SHA256重新核验匹配后发起attempt-01。本轮Sending与Booting均OKAY
（boot_returncode=0），双对象传输开始；RAM SHA256校验与实机pivot
结果待回填。

### v37 attempt-01 双对象传输校验通过（2026-10-25）

589.2秒完成双对象传输：OS 6,077,580,800字节、SystemOS 3,303,014,400
字节，实际RAM回读SHA256均匹配，无分区写入。full_os_object_transfer_
verified=true；normal_controller_stop_verified仍false。待实机：停机
三标记照片后手选 `fuxi RAM diagnostic: system-mount`，照片须完整
包含RAM_ROOT_SWITCH的pivot/rootfs/outgoing/dev/launchd/auth/userland
字段；pivot失败时errno照实归档。

### v37 attempt-01 最终照片：原生切根成功（2026-10-25）

用户IMG_6987原件/PNG/转写/photo-observation.json及PHOTO-REPORT.md
归档到attempt-01。最终panic结构完整可读：pivot=OK old=md0
newroot=apfs；rootfs=1且旧根rootfs cleared；outgoing=/Developer；
dev=devfs；launchd=/sbin/launchd SHA256=OK；auth=80；userland=0；
@RamSystemRootSwitch调用点。flags十六进制与unchanged数字位照片有
噪声已注明，'d'位恰对应0x14909001|0x4000=0x1490d001。

该文本仅在全链校验（换根身份、rootfs置位/清除、安置目录、devfs、
新根launchd字节一致）通过后可达，结论不依赖噪声位。**首次在本
启动路径建立原生系统根**：OS卷成为/并持有MNT_ROOTFS，md0迁至
/Developer，devfs保留。换根后rootauth仍EAUTH80（未密封，仅记录）。
停机三标记未拍，normal_controller_stop_verified仍false。

v38方向已定：同架构加回有界system user entry（init=新根
/sbin/launchd，SVC前截停），直接验证AMFI类别2根卷分支是否放行
launchd exec；不以改判定/信任/标志宣称修复。

### v38：切根后有界 exec 候选（2026-10-25）

独立v38从v37创建。boot参数不变（rd=md0、ram-root-probe=2、
ram-system-probe=1、ram-system-root-switch=1），档案模式改为
--user-entry --system-objects --system-user-entry --system-root-switch：
switch探针完成v37全部校验后记录auth/flags、武装fuxi_ram_native_root_entry
并返回；bsd_init以默认init路径创建PID1，/sbin/launchd解析在切换后的
OS卷根。exec前钩子在native模式额外复验根挂载MNT_ROOTFS/apfs身份
（新旧launchd字节相同，不能靠路径/哈希区分来源）；SVC截停panic为
RAM_NATIVE_ROOT_USER PASS变体并内嵌pivot记录。mount-only行为不变。

探针与构建/检查层改动，原生vfs_switch_root/AMFI/APFS不改。主机
build-kernel→Docker固件→FUXIPAYL完成；13项检查退出0，root-switch以
--mode user-entry跑真实C 43用例（成功路径断言武装态）与三项突变
拒绝，ASan/UBSan零报告，并绑定内核字节（user-entry变体在、
mount-only报告不在）与镜像。镜像203190272字节，SHA256
0768bbad331ea6c62245eb5a47a60186c53492ef147fc72f50df85bfdd27c033；
prelink 8384468415115d2e…；skew 0x7c000，KMEM下界662548480。

上机待手机fastboot后发起。验收分支：根卷分支放行→RAM_NATIVE_ROOT_
USER PASS（首次越过v34以来Launch Constraint Violation，exec完成、
EL0、SVC截停）；仍拒绝→保留约束原文按新拒绝点定位。不以改判定、
信任或标志宣称修复。

### v38 attempt-01 临时启动与传输发起（2026-10-25）

用户回复"已进入"。核对互斥无活动传输、fastboot显示fcde91e、镜像
SHA256重新核验匹配后发起attempt-01。Sending与Booting均OKAY
（boot_returncode=0），双对象传输开始；RAM SHA256校验与实机exec
结果待回填。

### v38 attempt-01 双对象传输校验通过（2026-10-25）

573.6秒完成双对象传输：OS 6,077,580,800字节、SystemOS 3,303,014,400
字节，实际RAM回读SHA256均匹配，无分区写入。full_os_object_transfer_
verified=true；normal_controller_stop_verified仍false。待实机：停机
三标记照片后手选 `fuxi RAM diagnostic: system-mount`，最终屏幕完整
归档——RAM_NATIVE_ROOT_USER PASS（含switch auth/flags）或约束拒绝
原文，两者均为决定性证据。

### v38 attempt-01 最终照片：launchd 放行，PID1 到达 EL0（2026-10-25）

用户IMG_6988原件/PNG/转写/photo-observation.json及PHOTO-REPORT.md
归档到attempt-01。最终panic：RAM_NATIVE_ROOT_USER PASS；root=switched
OS-volume apfs readonly=1 rootfs=1；exec=/sbin/launchd（after native
vfs_switch_root）；PID1 executed at EL0; SVC trapped；pc=100904fb4
cpsr=0 esr=56000080；x16=74（mprotect）instruction=d4001001；
switch auth=80 flags=1490d001；stopped BEFORE first syscall dispatch。

**v34以来阻断系统卷launchd的Launch Constraint Violation消失**：
AMFI类别2的根卷分支由真实切根的MNT_ROOTFS满足，exec全链（约束、
信任缓存、签名）完成，dyld启动，PID1在EL0发出首个SVC后被探针截停。
exec来源经exec钩子的根身份复验（新旧launchd字节相同，路径/哈希不足
区分）与v37切根链共同判定为OS卷。首个SVC与v32时代同二进制的ramdisk
launchd一致（mprotect），符合预期。rootauth EAUTH80为记录项。

边界：无syscall派发，主循环/dyld完整装载/用户态程序未验证；根为
只读RAM；不把本PASS写成完整系统启动。停机三标记未拍，
normal_controller_stop_verified仍false。下一验收点：SVC派发
（progress模式，沿用mount/reboot/ptrace封锁）。

### v39：切根后 SVC 派发与用户态跟随候选（2026-10-25）

独立v39从v38创建，组合模式--user-entry --system-objects
--system-user-entry --system-root-switch --main-entry --progress，
boot参数ram-root-probe=4。switch探针与v38一致；exec_ready武装
LC_MAIN+4硬件断点（偏移/字节pin沿用，两份launchd同二进制）；SVC
放行派发并观察，unix前后记录code/errno/rval与spawn路径；
mount/unmount/reboot/ptrace封锁（tmpfs例外），fork/exec/posix_spawn
放行跟随；90秒或131072次SVC后bounded run ended收场。progress快照
按native标志区分根身份文案。progress快照屏为固定ASCII字体渲染。

主机build-kernel→Docker固件→FUXIPAYL完成。13项检查退出0；首次
套件暴露两处检查脚本缺陷（mount检查硬编码非progress策略、
root-switch符号断言漏progress分支）与一处脚本语法错误，修正后
全过，失败记录保留。root-switch以--mode progress跑43例真实C与
三项突变拒绝。镜像203190272字节，SHA256
35833600a64cb72cc169d86fafeb39f85e0fc59b26ef8201299688e9dfb2ce72；
prelink 01a3a3bd2bd76cb3…。

上机待fastboot。验收：RAM_PROGRESS快照与收场panic中main=PASS、
spawn成功（e=0 r=pid）即用户态拉起的直接证据；只读卷EROFS/ENOENT
类失败照实归档。不把部分spawn写成完整系统启动。

### v39 上机前静态预检（2026-10-25）

按"事后取证前移"审查v39实机路径，发现并修复一处既有缺陷：
RamLaunchdProbe的spawn结果回填环用&3掩码（只扫槽0–3）而插入用&7
（数组8），第5个spawn起最新条目落在槽4–7不可见，e=/r=会错标或
滞留-9999——恰是v39要采集的spawn成功证据字段。修正为&7掩码、
回看8条，与unix事件环一致。源码已改，镜像重建中，重建后重过
全套检查并更新哈希。

其余审计无新缺陷：main_arm的argv0期望在native模式为/sbin/launchd
（fuxi_ram_system_user_entry仅enabled==2置位，v39为enabled=1）；
事件/ recent环掩码一致；deadline与mount封锁panic均带路径；SVC
钩子阶段矩阵（stage7 exec期无EL0 SVC、stage8放行PID1、stage10
跟随）闭合。

OS卷静态读取：无/etc/fstab（launchd启动不做fstab数据卷挂载，
mount触发线早期命中风险低）；/private/var存在但只读（写EROFS，
同二进制在恢复盘有先例）；System/Library/LaunchDaemons共523项
（子进程spawn将大量发生，spawn环修复正及时）；dyld缓存位于
cryptex卷，v38已证launchd dyld装载成功，子进程同路径。

### v39 静态预检完成：dyld 库解析链闭合（2026-10-25）

spawn环修复后重建v39并重过13项检查：新镜像203190272字节，SHA256
beace6e1e172c1173d72cc58626b25e269190e4b44448d50f6c3d6c524419d92；
prelink edaf6c8c38373a0bf…（REVIEW已同步，attempt命令同号新哈希）。

深度静态预检（详见diagnostics/ram-root/svc-dispatch-preflight.md）：
v38的PASS仅证明dyld已启动（首SVC mprotect可能在加载库前）。静态
推演v39放行后：launchd依赖仅存于dyld共享缓存；dyld三条缓存路径与
内核vm_unix scdir表在切换后OS根上全为ENOENT（/usr/lib无libSystem、
/private/preboot为空且只读无法建Cryptexes、/System/Cryptexes/OS
悬空）；cryptex卷含6.1GB分片缓存（System/Library/Caches/com.apple.
dyld/dyld_shared_cache_arm64[.01…]）但其usr/lib无核心dylib。
结论：v39预期以dyld库解析失败→init死亡告终，快照write文本可能
带dyld错误——此为必需真机证据，把库来源问题与exec问题分层。

v40设计静态完备：pivot后把cryptex只读挂到/private/preboot（OS卷
现存空目录），注入env DYLD_SHARED_CACHE_DIR=/private/preboot/System/
Library/Caches/com.apple.dyld（与cryptex内部布局逐层对齐）；通道
复用上游offline-owner-systemos-mount.patch的native_owner_cache_prepare
envp机制（当前树load_init_program_at_path已带envp），cryptex授权
用v36实测rootauth=0。不需真实dylib、不动dyld/AMFI/信任类别。

静态终点：DSC文件级映射校验、dyld对该env的行为、launchd只读根
运行行为、523守护进程spawn时序、高频SVC竞争——只能真机回答。
v39上机命令不变（镜像哈希见REVIEW）。


### v39 会话审计更正与 v40 修复交付（2026-10-25）

保留上文历史，撤回“静态分析已到终点/v40 静态完备”的判断。审计
发现：spawn 返回仍按 PID/code 匹配会把同进程不同线程的 A/B 结果
交换；posix_spawn 子 PID 经用户指针输出，成功 rv=0，旧 e=0 r=pid
判据错误；profile4 被套件当成非 main 跳过 policy/guards；重复检查
覆盖日志；旧 owner 缓存路径还存在 ignition 关闭目录 fd 的 EBADF
先例，不能仅凭 envp/路径对齐宣称闭合。

新独立 usb-system-mount-v40 从 v39 创建，旧镜像不覆盖。XNU 探针与
检查/构建层修复：syscall 入口返回 64 位 token，经真实内核入口/
返回补丁携带；记录按 token+thread+code 关联，掉入口/覆盖槽位不
回填旧调用；结果初始化未知。spawn 分开显示 e/rv/child/pe，成功
原始返回为 e=0 rv=0；child 是锁外读取的 PID 输出观察，缺失/失败
为 -1，不把用户共享指针的观察值当不可变内核 PID 或已执行证明。
profile4 必跑 --system-entry --native-root --progress guards/policy，
核验 init/sleh/systemcalls 对象；每轮 check-runs 独立保留失败与成功，
新候选清除父检查历史。

缓存实现 --system-cache：原生 pivot 后复用 /Developer/mnt2 原
cryptex 挂载，与 pivot 前原 /mnt2 mount 身份核对，只读 APFS、
原生 rootauth 必须实际返回 0，失败以原 errno panic。不重复挂载，
不创建只读目录。PID1 exec 前核对 mount/上下文和原始 OS dyld
SHA256，注入 DYLD_SHARED_CACHE_DIR=/Developer/mnt2/System/Library/
Caches/com.apple.dyld；dyld_flags|=8 并更新真实 commpage，避开已知
ignition 描述符清理分支，保留其他 flags。OS 根 auth80 仍为原有记录。
原始 dyld/AMFI/APFS/IOStorage、信任缓存和认证返回均不修改。

主机 XNU→Docker 固件→FUXIPAYL 组合镜像完成。镜像203190272字节，
SHA256 1718540b450ba9b49d6eb6c4191fa5befb390a9f99dad3410413a2a010415a0e；
prelink a0c6d89db2f408d7421832dfad54a7f44843eb763775fb7bf9d2be1798103122；
skew 由交付载荷重新推导同余通过，仍0x7c000；KMEM剩余VA下界
662548480。16项主机检查全部真实退出0；root/main实际C 87例（含
同PID多线程、环覆盖、丢入口、PID读取失败）；switch 43例+3突变；
cache C 23例+3突变；ASan/UBSan零报告。未修改原始 CacheFinder
构造/析构指令，Unicorn跑5输入：bit3置位不进ignite、fd0至析构
才关闭；桩提供外部调用，不声称完整dyld/AMFI仿真。

额外可重复静态检查：prepare-ram-cache-audit.py 读取真实SHNGROOT，
两对象重算SHA256与清单及已传输输入匹配，只读挂载，确认OS dyld
937056字节与模型原件同哈希f8df3c58fae6e409…；53个缓存文件、201083
完整签名块哈希匹配，4K映射对齐与四个子块破坏拒绝通过。此为文件
哈希和格式证据，不是CMS信任链或实际内核映射通过。脚本已自行
卸载其临时镜像。完整报告/命令/失败日志在v40 REVIEW，规范分析
svc-dispatch-preflight.md已更正；v39 REVIEW已标旧判据不可用。

首次内核构建因沙箱拒sysctl停止，首次套件在Unicorn/JIT处被沙箱
以-4终止；原失败日志独立保留，相同交付镜像沙箱外全套重跑通过。

内核补丁提交dd0b80c。交付后检查无设备占用任务，但fastboot列表
为空，未发起boot/重启/传输。v40实机、真实cache lookup/rootauth/
AMFI映射/LC_MAIN/子进程执行均待新attempt证据；normal_controller_
stop_verified仍false。不预判历史PMGR依赖必然复现或已经修复，不把
主机验证写成实机或完整系统启动成功。


### v40 attempt-01 发起（2026-10-25）

用户确认已进入 fastboot。重新核对无活动设备任务、fcde91e 可见，
交付镜像 SHA256 1718540b450ba9b49d6eb6c4191fa5befb390a9f99dad3410413a2a010415a0e
与完整16项检查及脚本绑定匹配；使用新 attempt-01。完整脚本重新
校验两对象源窗口后，仅 fastboot boot 临时启动，Sending/Booting
均 OKAY（4.269s/1.489s，returncode=0）。接收器发现和双对象 RAM
传输/回读校验继续；没有分区写入。尚无 XNU/cache/progress 实机
结论，normal_controller_stop_verified 仍 false。


### v40 attempt-01 双对象 RAM 传输与校验完成（2026-10-25）

完整脚本退出0，584.740秒完成：OS 6,077,580,800字节、SystemOS
3,303,014,400字节，两对象acknowledged_bytes完整，实际设备RAM回读
SHA256分别为e5634d04a8ac35f024845f583394e0a35deb26a04cb0c00286a8674e25a9a4f2
和b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a18b1600c96a3fd70414f，
均与源匹配；full_os_object_transfer_verified=true，无分区写入。
交付镜像仍1718540b450ba9b49d6eb6c4191fa5befb390a9f99dad3410413a2a010415a0e，
16项检查及当前检查脚本哈希绑定重新核验匹配。原始full-transfer、
boot.log、device-test和check-suite归档到attempt-01。

normal_controller_stop_verified仍false。待接收器停机三标记照片，
再手选fuxi RAM diagnostic: system-mount，归档RAM_CACHE的原始
返回/ready、RAM_PROGRESS快照和最终panic。传输完成不证明已进入
XNU或缓存映射/launchd main通过，不预填实机结论。


### v40 attempt-01 最终照片：cache准备完成，dyld无缓存、main前终止（2026-10-25）

用户IMG_6989原件/PNG/转写/photo-observation.json/PHOTO-REPORT归档。
照片直接显示stage=8 main=pending；root=switched OS-volume apfs
readonly rootfs=1；cache=2 dyld_flags=8；pid1 svc70 unix54 exec0 drop0；
x16=521。最终initproc failed to start，namespace6 subcode1，
Library not loaded: /usr/lib/libSystem.B.dylib；Reason: no such file,
no dyld cache。末尾syscall号与本候选头核对：4/6/48/521分别为
write/close/sigprocmask/abort_with_payload。exec0是后续exec/spawn
观察计数，不能解释为初始exec没发生。

阶段与编译源码共同推得：原/mnt2在切根后/Developer/mnt2身份检查、
实际原生cache卷rootauth=0、OS原始dyld哈希、env copyout与flags/
commpage更新均已完成；cache2不证明缓存映射成功。SVC派发已经
发生，LC_MAIN+4未命中，未观察子进程。照片不保留目录open/openat
或shared_region映射的具体返回，故不能把本次错误直接写成EPERM。

后续源码核查发现上一轮预检漏项：实际vm_unix对象带载体卷拒绝
错误串，NCONFIG_CSR=0且XNU_OWNER_CACHE_CARRIER=0；非根卷缓存
必须与/private/preboot/Cryptexes解析出的mount同一卷。scdir_enforce=0
不关闭这道独立检查。v40的env路径没有建立该原生载体身份；旧
check-owner-cache-preflight-checkpoints.py亦记录过同检查EPERM，
但旧离线结果不是本次errno实测。下一步须保留目录/映射原返回、
满足原生布局，不关闭检查或改返回宣称修复。

16项检查/镜像/当前检查脚本绑定重新核验全部匹配后写结论。
停机三标记未拍，normal_controller_stop_verified仍false。未到main、
未证明缓存映射或完整用户态启动；本次仅照片归档/源码核查，未操作设备。


### v41：满足原生缓存载体检查，启用完整16K块验证（2026-10-25）

针对v40照片main前no dyld cache，独立APFS clone候选，父v40镜像
哈希未变。补齐两项静态遗漏：v40 ubc_subr.o的native hash cache
关闭，使4K内核的CodeDirectory页大小门拒绝16K签名块；非CSR的
vm_unix.o还要求缓存与/private/preboot/Cryptexes同mount。这是
源码/编译对象证据，v40照片没有最初open/签名/映射的实测errno。

改动位于XNU、原始tmpfs预链接配置与构建/检查/打包层。PID1 exec
前用同一21H461原始tmpfs和普通mount/symlink，在/private/preboot
建立Cryptexes -> /Developer/mnt2，然后原生只读封存；重新解析确认
是已认证的真实cache APFS mount/VROOT。RAM quota=1MiB、32节点，
readonly/nosuid/nodev/noexec。原载体检查源码与v40同哈希，不关闭
策略、不改身份返回、不写只读APFS或分区，任一步错误原状态终止。

启用已有CONFIG_XNU_NATIVE_CS_HASH_CACHE=y，先验证完整原始16K
签名块，再从同缓冲区派生4K哈希，只读保护成功才发布。缓存/签名/
CDHash/trust/原始dyld与AMFI/APFS执行字节不改。新增cache open/map
最后一次原返回sticky记录、carrier及签名进展计数，未观察仍-9999；
首次约3.3GB签名预读期限300秒，SVC上限131072、既有写入封锁保留。

最终主机XNU、Docker固件和FUXIPAYL打包完成。镜像203190272字节，
SHA256 d9111f8b934df910a67bf6787a83b618988aa4cef3cd77eee2de985ab4f31468；
prelink 9193914ee024830dc7b0a3b68f25f7f91bb109a6d963db0ee888668127258e2d；
最低非空段0xfffffff00427c000，交付载荷与skew重新推导同余仍0x7c000，
KMEM剩余VA下界662548480。17项检查全部真实退出0，当前源码和
检查脚本哈希与suite/镜像再次绑定通过。root/main C90例，switch43例
及3突变；cache C43例及4突变；CacheFinder原指令5输入；新增实际
内核签名代码24例及2突变、原页大小门8组合、原载体分支5例、原tmpfs
指令5例。tmpfs新挂载模型停在copyin，不是完整节点分配或硬件挂载。

按真实SHNGROOT清单重算两个窗口，只读审计53缓存文件/201083完整
签名块及4K对齐/子块损坏拒绝；原始OS dyld哈希匹配。脚本自行卸载
临时窗口，cleanup_complete=true。这些不是CMS信任链或真实映射
验收。构建失败/模型失败和中间版本日志保留，不覆盖最终成功记录。
完整交付与命令见v41 REVIEW，规范设计cache-carrier-fix.md。

互斥检查无活动设备任务，fastboot列表为空，未发起v41 boot/重传。
无分区写入，normal_controller_stop_verified=false。候选待fastboot
及新attempt照片：tmpfs加载/封存、缓存映射、launchd main、子进程
实际执行和完整系统启动均未验证，不预判后续PMGR依赖已经修复。


### v41 attempt-01 发起（2026-10-25）

用户确认fastboot可测试。重读最新日志和v41 REVIEW，互斥检查无活动
设备任务；fcde91e可见，helper确认product=fuxi、unlocked=yes，当前
slot=a（只查询，未修改）。最终镜像d9111f8b934df910a67bf6787a83b618988aa4cef3cd77eee2de985ab4f31468
与17项套件/当前检查脚本/探针源码以及UEFI/loader源码绑定再次通过。
新建独立attempt-01，完整脚本重新校验两个源窗口后仅fastboot boot。
Sending/Booting均OKAY（4.209s/1.939s，returncode=0）。接收器发现和
双对象RAM传输/回读流程继续；尚无XNU/cache/main实机结论。
已归档原始boot.log、device-test及镜像绑定检查报告，无分区写入，
normal_controller_stop_verified=false。不将kernel_handoff配置位当
作进入XNU证据，不因fastboot列表消失而重复boot。


### v41 attempt-01 双对象 RAM 传输与回读校验完成（2026-10-25）

完整脚本退出0，557.490秒完成。OS 6,077,580,800字节、SystemOS
3,303,014,400字节全部确认，两个对象ram_readback_verified=true，
RAM回读SHA256分别匹配源e5634d04a8ac35f024845f583394e0a35deb26a04cb0c00286a8674e25a9a4f2
及b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a18b1600c96a3fd70414f。
镜像仍d9111f8b934df910a67bf6787a83b618988aa4cef3cd77eee2de985ab4f31468；
17项检查与当前检查脚本、探针源码及镜像绑定再次核验通过。完整
full-transfer、boot.log、device-test、transfer-console、host-checks和
post-transfer-binding均归档到本次attempt-01。

无分区写入。full_os_object_transfer_verified=true仅证明RAM传输；
normal_controller_stop_verified=false。待接收器180秒无通信后结束
接收，拍HALTED/路由恢复/USBRAM QUIESCE: Success；再手选
fuxi RAM diagnostic: system-mount，拍完整RAM_CACHE/RAM_PROGRESS
和最终panic。carrier/cache准备、签名验证、实际映射、LC_MAIN与
子进程执行均待照片；kernel_handoff=true仍只是配置位，不是进入
XNU证据。未因fastboot消失而重启或重复传输。


### v41 attempt-01 实机照片：缓存映射成功，main PASS，子进程运行（2026-10-25）

IMG_6990.HEIC（SHA256 962cbfe9b45ed3d8872949f4443d6f4b37b136f760814db5e3be5d57971d8ccc）
归档于attempt-01，含转写与photo-observation.json。写结论前镜像哈希
d9111f8b934df910a67bf6787a83b618988aa4cef3cd77eee2de985ab4f31468与
post-transfer-binding（17项、脚本/探针源码匹配）重新核对一致。

照片直接证明：carrier=1 cache=2 dyld_flags=8；sigblobs=53
blocks=201083与主机审计逐一吻合；map=1 err=0 rv=0为
SYS_shared_region_map_and_slide_2_np原返回0，缓存实际映射成功；
stage=10 main=PASS由LC_MAIN+4硬件断点+指令字节比对置位。PID1
posix_spawn出xpcproxy（child=69 pe=0，e=0 rv=0），PID69再spawn
/System/Library/CoreServices/Rep…（路径截断）成功；exec=65，
pid=41/57/63/69多进程EL0运行（FeatureFlags plist、
/usr/swift-backtrace stat、write尝试）。v40的main前no dyld cache
阻塞已消除。

终止：PID57调用ptrace(PT_DENY_ATTACH=31)（a0=0x1f，与
ios-obj EXPORT_HDRS syscall.h/ptrace.h核对），属follow模式设计内
封锁集（mount/unmount/__mac_mount/fmount/reboot/ptrace），
RamLaunchdProbe.c:387派发前panic停机。探针策略停机，非启动链故障。

边界：末次cache-open err=2不与map成功矛盾（多路径探测，以map原
返回为准）；PID69 spawn的child=-1 pe=-1为子PID读出未捕获，非失败；
Rep…全名未证实；pages=0xac664 free=0x3a2仅记录不断言；停机三标记
照片未拍，normal_controller_stop_verified=false。完整系统启动、
可写根、持久化仍待后续。

下一候选决策点：ptrace(PT_DENY_ATTACH)放行策略（自保护调用）、
写入封锁粒度、内存余量跟踪。实机推进方向：放开首条策略性阻塞后
观察launchd后续服务矩阵与首个用户态写入需求。


### v42：放行 PT_DENY_ATTACH（2026-10-25）

针对v41照片PID57 ptrace(PT_DENY_ATTACH=31)触发设计内封锁，独立
clone候选，仅改XNU探针与探针档案/检查脚本。fuxi_ram_progress_unix
内与tmpfs例外同层放行arg0==31的单请求，其余ptrace与mount/unmount/
__mac_mount/fmount/reboot维持派发前拒绝；fuxi_ram_main_unix(SYS_ptrace)
本体仍无条件拒绝。编译位RAM_ROOT_FOLLOW_DENY_ATTACH由pins.json新增
follow_ptrace_deny_attach驱动，缺省False时与v41编译行为等价；
非progress模式置位被validate_stage_policy拒绝。写入封锁粒度复核：
封闭集无write类syscall，EROFS来自切根卷MNT_RDONLY原生行为，不新增
内核侧可写区；panic尾部pages/free仅按片记录。设计见
follow-deny-attach.md。

最终镜像203190272字节，SHA256 1c4ad272f052b9be6c44e7dc2e309affd0316a2118cf96da548cf86c466d2cb0；
kernel.macho 5a08a3cfa36d2d2b6475e617af93751346e724d0b877cd6529867a6c0370cf33；
payload 4f5cd94db0b4a85ee2fcadb4a51df72b9b7ecf278a240cb4bb8078bc721f426a。
OEM=FUXIPAYL，skew重推导仍0x7c000。内核与容器固件构建均退出0，
探针档案以v42 pins重建（RamRootProbePins.h含FOLLOW_DENY_ATTACH=1）。
17项检查run_id=20261025T045148.785716Z全部退出0并绑定本镜像；
probe-policy 37例、probe-guards 93例（含放行1例/拒绝2例）。
缓存/签名/载体源码与v41逐字节相同，窗口审计沿用v41记录。

互斥检查无活动设备任务；尚未发起v42 boot。待fastboot出现后按
REVIEW命令attempt-01：若越过PT_DENY_ATTACH后RAM_FOLLOW再现
syscall=26需核对a0（31=例外未生效的绑定问题；其他request号属预期
拒绝），关注EROFS类失败与下一停点。normal_controller_stop_verified
仍false，完整系统启动不预判。


### v42 attempt-01 临时启动与双对象传输完成（2026-10-25）

互斥检查无活动任务，fcde91e可见，完整脚本核验serial/product/
unlocked后仅fastboot boot（Sending 4.233s/Booting 2.139s，OKAY）。
OS 6,077,580,800字节、SystemOS 3,303,014,400字节全量传输，两对象
ram_readback_verified=true，回读SHA256分别匹配源
e5634d04a8ac35f024845f583394e0a35deb26a04cb0c00286a8674e25a9a4f2
及b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a1（与v41同源同值）。
镜像仍1c4ad272f052b9be6c44e7dc2e309affd0316a2118cf96da548cf86c466d2cb0；
post-transfer-binding确认17项套件绑定、探针源码与镜像一致。
boot.log与full-transfer归档attempt-01；无分区写入。

normal_controller_stop_verified=false。待接收器180秒无通信结束后
拍HALTED/路由恢复/USBRAM QUIESCE: Success，手选system-mount，拍
RAM_CACHE/RAM_PROGRESS与最终画面。若RAM_FOLLOW再现syscall=26核对
a0：31=例外未生效；其他request号属预期拒绝。kernel_handoff=true
仍只是配置位。


### v42 attempt-01 实机照片：PT_DENY_ATTACH 壁垒消除，运行至观测上限（2026-10-25）

IMG_6991.HEIC（SHA256 71520f1c811105734ea8b60dfbd58d36e2766964758e450baf53e831fba2cb1c）
归档attempt-01，含转写与photo-observation.json。镜像哈希、17项套件
与post-transfer-binding重新核对一致。

照片直接证明：无RAM_FOLLOW blocked panic，v41停点已越过；系统停在
探针有界观测上限（svc=131073>131072，deadline_expired于
RamLaunchdProbe.c:147）——观测窗口关闭，非故障非封锁。stage=10
main=PASS、map=1 err=0 rv=0保持；unix=87745、exec=95，PID1连续
spawn xpcproxy（child=90/91，pe=0），PID90末尾getpid自返回90，
前序shared_region_check_np/mprotect/bsdthread_register为新进程
libSystem标准初始化。pagechecks 7426→15767。launchd（pid=1）实际
发起write，缓冲以com.apple.xpc.launchd1970-01-01 00:开头：系统
时钟未设置，epoch时间戳；write已派发，返回码未保留。

边界：300秒期限与SVC上限谁先到照片不可分；free页930→631随推进
下降未耗尽，仅记录；cache-open末次ENOENT与map成功并存（多路径
探测）；停机三标记未拍，normal_controller_stop_verified=false。
完整系统启动、可写根、持久化未验证。

下一候选决策点：扩大观测窗口（SVC上限/期限）看守护进程矩阵收敛
或下一个自然停点（EROFS/内存/时钟依赖）；时钟未设置（1970时间戳）
与内存余量（free 631页）跟踪。


### v43：扩大 follow 观测窗口（2026-10-25）

针对v42在131072 SVC观测上限处收窗时用户态仍在推进，独立clone候选，
仅改观测参数：pins progress_deadline_seconds 300→900、新增
progress_svc_limit=1048576（缺省131072保持旧行为）；探针SVC上限由
生成头RAM_ROOT_PROGRESS_SVC_LIMIT驱动。300/900期限均要求system
cache完整性模式；提高SVC上限要求progress档案。封锁集、例外、快照
格式与采集逻辑不变；free页与1970时间戳按片继续记录。设计见
progress-window-widen.md。

最终镜像203190272字节，SHA256 3f0d9910f2f90b1483037f9ec5bc98bf5174d681303d2f778d0439e1370ec5b4；
kernel.macho 8e777d6a3514fd394e9fc449077a52bd59a3fb6164f1502e227511d5bc5d0906。
OEM=FUXIPAYL，构建与finalize均退出0。17项检查
run_id=20261025T051932.729404Z全部退出0并绑定本镜像；guards 93例
（SVC上限路径以1048576触发），cache检查断言期限/SVC pin与生成头一致。

实机待attempt-01照片：越过v42停点形态后的exec/unix增量、spawn路径
多样性、write缓冲、free页趋势、新自然停点或预期封锁。900秒期限内
可多拍中间画面。本候选无实机结论，normal_controller_stop_verified=false。


### v43 attempt-01 临时启动与双对象传输完成（2026-10-25）

互斥检查无活动任务，fcde91e可见，完整脚本核验后仅fastboot boot。
OS 6,077,580,800字节、SystemOS 3,303,014,400字节全量传输，两对象
ram_readback_verified=true（回读SHA256同源同值：e5634d04…与
b4908cde…）。镜像仍3f0d9910f2f90b1483037f9ec5bc98bf5174d681303d2f778d0439e1370ec5b4；
post-transfer-binding确认17项套件绑定与探针源码一致。boot.log与
full-transfer归档attempt-01；无分区写入。

normal_controller_stop_verified=false。待接收器结束后拍HALTED/
路由恢复/USBRAM QUIESCE: Success，手选system-mount。本次窗口最长
900秒/1048576 SVC，期间可多拍中间画面；终帧判据：exec/unix增量、
spawn路径多样性、write缓冲、free页趋势、新自然停点或预期封锁。


### v43 attempt-01 实机照片：第一个自然停点，watchdogd SIGTRAP（2026-10-25）

IMG_6993.HEIC（SHA256 1b830330d2dae71e089a0cdd47d86da47a8777c9829c1c43df5f1d75828501ea）
归档attempt-01。镜像/17项套件/post-transfer-binding重新核对一致。

停机性质（源码核实）：panic行为"watchdogd[N] exited -- exit reason
namespace 2 subcode 0x5 description: none"，与kern_exit.c
proc_handle_critical_exit→panic_with_options格式逐一吻合（带冒号
description区分于1843行printf）；watchdogd是crash-panic策略关键进程，
死亡即panic，iOS原生行为。namespace 2=OS_REASON_SIGNAL、subcode 5=
SIGTRAP（编译树reason.h核实）。非探针收窗：svc=187220远低于1048576，
无bounded run ended行；非封锁：无RAM_FOLLOW行。panic屏渲染器
（patch-xnu-entry-probe.py注入）确认此为内核panic帧。

推进证据：exec 95→129，守护进程矩阵首次越出xpcproxy——PID1 spawn
xpcproxy(121读出成功)，121再posix_spawn /usr/libexec/watchdogd成功
（孙PID未捕获）；121读取Logging/Subsystems plist与/usr/swift-backtrace
做前置配置。map err=0不变，pagechecks 15767→17476。free 631→1797页
回升（短命守护进程退出释放），时钟仍1970。

边界：SIGTRAP直接原因未证实（无faulting PC/ESR；假设：guard违例或
Apple brk断言，均未证）；u70909 workq_kernreturn在途属pid121非死因；
watchdogd PID数字受转写限制；三标记未拍，
normal_controller_stop_verified=false。

下一候选方向：取证（探针捕获致命信号现场：PC/ESR/guard码）或推进
（关键退出记录不panic，越过watchdogd看后续矩阵与写入需求）。


### v44：致命 brk 故障取证（2026-10-25）

针对v43照片watchdogd SIGTRAP根因未证，独立clone候选。内核补丁在
ESR_EC_BRK_AARCH64 EL0分支handle_breakpoint前挂fuxi_ram_progress_fault
(pc,cpsr,esr,far)，纯观测不改异常语义；探针sticky记录fault_pc/far/
esr/pid/count并新增快照行fault=%u pid=%d esr=%x pc=%llx far=%llx
（有记录才显示）。ram_probe_policy sleh.o符号绑定+1；guards 96例
（fault记录/覆盖/锁丢弃3例新增）。策略、封锁集、900秒/1048576窗口
不变。设计见fatal-fault-forensics.md。

首轮构建因手写补丁hunk行数错误（+19应+20）被git apply --check
拒绝（corrupt at line 38，build-console-01.log保留）；修正后
work树apply-check通过重建。最终镜像203190272字节，SHA256
495ab2d81e7c767a2d1989567b67101fed1a13c79088e49945f6b3a7b260ddd0；
kernel.macho 01b1430a8e120db8decb30bb8465bf0d536bd47dbcec1b4e6e9dca216826d928。
sleh.o三fuxi符号核验。17项检查run_id=20261025T060926.115746Z全部
退出0并绑定本镜像。

实机待attempt-01：watchdogd若复现SIGTRAP，fault=行给出pc/far/esr
（含brk imm16），主机端反查dyld缓存符号与断言类别；若死于SIGTRAP
而无fault=行，来源在brk之外（EXC_GUARD等），为否证信息。内核补丁
在ramdisk-test/unixnu-source仓库同轮提交。


### v44 attempt-01 临时启动与双对象传输完成（2026-10-25）

互斥检查无活动任务，fcde91e可见，完整脚本核验后仅fastboot boot。
OS 6,077,580,800字节、SystemOS 3,303,014,400字节全量传输（555.0秒），
两对象ram_readback_verified=true（回读SHA256同源同值）。镜像仍
495ab2d81e7c767a2d1989567b67101fed1a13c79088e49945f6b3a7b260ddd0；
post-transfer-binding确认17项套件绑定与探针源码一致。boot.log与
full-transfer归档attempt-01；无分区写入。

normal_controller_stop_verified=false。待接收器结束后拍HALTED/
路由恢复/USBRAM QUIESCE: Success，手选system-mount。终帧判据：
RAM_PROGRESS的fault=行（watchdogd复现SIGTRAP时给出pc/far/esr）
与panic行；无fault=而行死于SIGTRAP则为brk外来源的否证。

### v45：USB RAM 传输吞吐提升，chunk 64 KiB → 1 MiB（2026-10-25）

针对v43 attempt-01实测16.5 MB/s（9,380,595,200字节/567.977秒；OS段
≈15.7、SystemOS段≈18.3 MB/s）远低于USB2高速实用上限（~40 MB/s），
判定瓶颈为停等协议每chunk固定成本（每64 KiB三相传输编排+事件处理+
轮询停顿约2.7ms，而64 KiB高速批量线时间仅约1.1ms），而非PHY。

改动层：UEFI接收器RamBulk.h RB_CHUNK 65536→1048576（HELLO广播值，
协议语义、封锁集、END回读校验不变）；主机usb-ram-send.py改用HELLO
协商值（限64 KiB..1 MiB且512对齐，与旧固件双向互操作）；驱动README
补记。DWC3单TRB远低于DWC3_MAX_TRANSFER_SIZE。父候选v43——v44制备时
目录处于被清理的中间态（candidate.json/最终镜像/check-suite缺失，
无构建进程），避免撞车；v45内核为fresh-patch自HEAD a89eaa1构建，
内容等价v44系列（含EL0 brk取证钩子），与共享sleh符号策略一致。

最终镜像203190272字节，SHA256
82fcba8bc1a748438673a32b645d63d9a2fe274e752713e49db21043edd11b77；
kernel.macho 1ee04673…（裸）/4bf75b6f…（打包）；FuxiUsbRamDxe.efi
529d3104…，与v43副本6b29ca68…不同，证明头文件改动触发重编、无
陈旧二进制。host-tests.json由套件receiver步重新生成（绑定新
RamBulk.c/h与sender哈希）；smmu/route/ep0/stop沿用v23记录（绑定源
未变，check-fuxi-usb-ram-image.py复检通过）。17项检查
run_id=20261025T063710.276659Z全部退出0并绑定本镜像。

实机待attempt-01：制备时手机不在USB总线。验收点：双对象全量传输
elapsed_seconds与分段速率（对标v43的567.977秒）、ram_readback_verified
=true、HALTED/路由恢复/USBRAM QUIESCE: Success照片。预期≈35-40
MB/s、总时长约230-300秒；若仍≈16.5 MB/s则瓶颈判定有误，回溯DWC3
事件/TRB路径。本候选不进XNU即可完成传输验收。


### v44 attempt-01 照片：同PID brk #1，IOWatchdog查找失败的强定位线索（2026-10-25）

用户IMG_6995原件SHA256=159e52a66c030b5433c25fff9eff4f11d2bf566ecf905c60a7638da3e4decab9，
原件/PNG/转写/photo-observation/PHOTO-REPORT归档attempt-01。镜像仍
495ab2d81e7c767a2d1989567b67101fed1a13c79088e49945f6b3a7b260ddd0；
17项退出0、当前检查脚本和探针源码与镜像绑定再次通过。

直接观察：main=PASS，root切OS只读，cache2/carrier1/flags8，map1
err0 rv0，sigblobs53/blocks201083/pagechecks17473；svc193095、
unix85602、exec158、drop156178。fault12 pid135 esr=f2000001
pc=1011e6b0c far=101252000；最终watchdogd[135] SIGNAL/5关键退出
panic。ESR明确为EL0 BRK_AARCH64、imm16=1；与死亡PID一致。全局
fault12不表示watchdogd崩溃12次，BRK的FAR不当有效访存地址解释。
同PID的xpcproxy→watchdogd与SETEXEC相容，未采flags，不推断孙PID，
不将PID135后续事件自动归给旧xpcproxy或排除死因。free1344页仅记录。

重新绑定OS源窗口SHA256，只读提取原watchdogd（ded4c688…13a01），
自行卸载临时镜像。原__text中唯一4K页对齐brk#1候选为0x10000ab0c，
照片PC对应候选slide0x11dc000；其前__os_crash消息为
connect_to_kext() unexpectedly failed - unable to lookup watchdog KEXT service。
原plist匹配IOProviderClass=IOWatchdog。既有原始指令模型4输入通过：
非VM无服务到该brk，VM无服务exit，打开错误到另一brk，连接成功返回。
外部IOKit/sysctl均为桩，不是实机服务返回。最强候选为原生IOWatchdog
服务查找失败；运行时映像基址/UUID、TID/退出关联与实际lookup返回
仍未采集，不把唯一候选写成完整根因证明，不反推v43帧。

修订早先取证判据：锁忙会丢fault，全局最后记录也可覆盖，故无fault
不能排除brk，有fault也须关联死亡现场。原生critical-exit机制不排除
本地平台/移植原因。下一步核验IOWatchdog注册/匹配/启动与原生接口，
补运行时基址/致命退出关联；不以关闭panic或伪造成功作为修复。

full-transfer.json最终elapsed=595.345秒（先前555.0不是最终全流程
耗时），双对象RAM回读仍通过。三停机标记未拍，normal_controller_
stop_verified=false；完整系统启动/持久化未验证。本轮仅只读分析和
归档，无新候选、无设备操作。

### v45 attempt-01 实机传输：协议工作，提速远低于预期（2026-10-25）

互斥检查无活动任务，fcde91e可见，完整脚本临时启动（boot 5.775秒）。
OS/SystemOS双对象全量传输elapsed 509.49秒（aggregate 18.4 MB/s；
两对象ram_readback_verified=true、SHA256同源同值，stage=passed，
full-transfer.json归档attempt-01）。对比v43的567.977秒/16.5 MB/s
仅+11%，远低于预期~2倍。

两点拟合（v43每64KiB 3.96ms vs v45每1MiB 56.85ms）：每chunk固定
成本仅≈0.43ms——节省的58.5秒≈134,474个省略chunk×0.43ms，反证
1MiB chunk确已生效；剩余≈54ns/B串行成本是真墙，渐近≈18.5 MB/s。
"每chunk固定成本主导"的原瓶颈假设被实测证伪。

外围排除：源镜像外置盘读取678 MB/s（dd实测）；同一口/线ABL
fastboot推198,428 KB@47.2 MB/s（attempt-01 boot.log），排除主机
控制器/线缆/eUSB2中继。嫌疑收敛到本栈bulk-OUT流本身（~20 MB/s）：
macOS libusb同步bulk路径或DWC3 RX流配置。下一步：设备空闲时用
scripts/usb-ram-timing-probe.py（只读诊断，生产sender模块外包
计时，发256MiB后ABORT、不留会话状态）把每chunk 56.85ms拆成
payload线时间/头尾往返/间隙，据此定host侧（异步/流式提交）或
固件侧（DWC3流配置）的后续候选。

normal_controller_stop_verified=false（未拍三标记；接收器传输完成
后已发布对象回菜单）。FuxiUsbRamDxe.efi 529d3104…≠v43副本，
新固件确在运行。


### v46：基于v45，原始IOWatchdog服务链与软件计时后端（2026-10-25）

独立APFS clone父v45，父镜像哈希未变，保留1MiB HELLO分块。核验
原AppleARMWatchdogTimer personality仅匹配AppleARMIODevice /
wdt,s5l8960x，fuxi未提供此Apple设备，故预链接不等于服务启动；
不伪造Apple MMIO设备。原模块提供IOWatchdog基类及完整原客户端。

改动层：XNU设备包、构建/检查、组合打包；原始Apple程序/AMFI/trust/
关键退出策略均不改。PID1 exec前加载原模块并验证metaclass/大小，
晚注册真实RamWatchdog子类，attach原生IOResources、调用原初始化，
再真实serviceMatching确认唯一且为自身，成功置wdt3。原root、
com.apple.private.iowatchdog.user-access、type1及独占策略/10项
派发保留。软件后端每秒在原锁/中断保护下只读原始heartbeat sequence/
deadline与enabled，到期真实panic，不改原始心跳/成功返回。

这是明确标识的软件计时后端，检测内核仍运行时的用户态心跳超时，
不是SM8550硬件WDT，不能覆盖CPU/调度器全挂死；不写硬件寄存器或
分区、不关watchdogd、不伪装VM、不取消关键退出panic。设计与验证
边界见watchdog-service.md，独立v46 REVIEW含重现与全部失败日志。

最终镜像203190272字节，SHA256
37255ef9caa87dd631581b6886cf176e0321790aedccc98e824d6f042f38b895；
prelink ccef2bfddf614ad89b1f7be9a5ac36fdb76edd103202bac2fea5f394bbff3834，
52072448字节；FUXIPAYL，skew按交付载荷重推导仍0x7c000，KMEM剩余
VA下界662548480。原kernelcache绑定，171槽/248字节基类/232字节
客户端、心跳指令和5项原始绑定均验证，原模块执行字节未改。

完整18项检查run_id=20261025T072234.829932Z全部真实退出0并绑定
交付；root/main C96、switch43、cache C45/5突变；新后端真实C++26例
与3行为突变，另跑原始client/heartbeat指令9例（含root/entitlement/
占用/type拒绝与正常打开/原客户端start/启停/pet）。外部KPI为明确
桩，不冒充手机服务验收。首轮构建绑定清单、首轮旧归档白名单以及
原始模型桩不全的失败日志保留；最终完整重跑成功。

用户确认fastboot并要求后台启动。互斥无活动任务、fcde91e可见，
helper核对product/unlocked/镜像后仅fastboot boot；Sending4.188s、
Booting1.641s均OKAY。独立attempt-01双对象传输及设备RAM回读SHA256
均通过，完整脚本退出0、514.342秒；OS6077580800字节哈希e5634d04…
与SystemOS3303014400字节b4908cde…均同源匹配。原始报告、绑定和
本次脚本归档attempt-01，无分区写入，无重复boot/重传。

normal_controller_stop_verified=false，待三停机标记照片后手选
system-mount。wdt=3只证明服务注册/匹配/启动，clients增量证明原
客户端打开，beats增量证明真实心跳；越过旧lookup brk不等于完整
系统启动。v46尚无运行照片，服务实际验收/完整系统/持久化待证；
保留fault全局覆盖与锁丢失边界，无fault不能排除brk。


### TWRP /data → UEFI 移植分支：M0说明与只读基础（2026-10-25）

按用户要求从实验UEFI 4fda548d创建feat/twrp-data-uefi，独立检出
ramdisk-test/uefi-twrp-data；原feat/ramdisk-test未切换。本阶段改动
位于UEFI诊断、主机审计/测试和说明文档，不改DarwinBoot/XNU/USB
对象交接或系统打包；UEFI提交e64deffc。

输入为用户更正的twrp-3.7.1_12-v9.0_A16-fuxi-skkk.img，SHA256
68f3829ba4b7faaf8b873fd0dce7dabd03b2bdd750debbae56b1dbe326f87da5。
重新解包并双pin image/CPIO，13组件哈希和ELF接口档案入UEFI文档；
找到Decrypt_DE/User、vold取钥/装钥、高通KeyMint/Gatekeeper/QSEE与
UFS-crypto/SCM依赖。源码路径和符号不是运行时解密证据。

M0实现：限界只读回调、GPT CRC/身份/重叠检查、F2FS头部几何识别、
唯一UFS LUN0 BlockIo适配、SCM/QSEE协议发现及单独开关的INFO调用
存在性查询。默认两个固件开关均0。查询只执行INFO，不执行ICE
set/clear/derive，不读取口令/密钥、不写分区、不加载TA或调用RPMB。

82个fixture直接编译生产C，经ASan/UBSan通过；容器独立DXE构建两版
真实退出0。no-smc 12800字节SHA256 d0b54e2fda7c01d25db5d6d858ea4ad
564819bde13ac58f295a128d4e9eaffd1；query 13824字节SHA256
90ad4c5e4a3e8022292ced90636ca1545d4758986e7461622b3d8ddd48286baa。
源绑定复核通过；EFI反汇编分别0/1条SMC。明细在独立检出的
out/data-port-m0/REVIEW.md、build.json和host-tests。

没有完整启动镜像、fastboot操作或attempt，不占用v46手机会话。
当前默认固件无UFS提供者，原生UEFI UFS接管、F2FS文件读取、元数据
解密、fscrypt及本地RAM对象发布均未实现；availability与F2FS头部
识别不代替解密/挂载验收。已有2026-10-23-ufs-readonly照片确认的
fuxi XNU原始GPT/首块读取可参考，但不是UEFI结果。

移植说明：UEFI docs/fuxi-twrp-data-port.md，列M1原生只读UFS→
M2 metadata文件→M3安全/元数据解密→M4已知文件明文SHA256→M5完整
对象加载各阶段和停止条件。下一实现点为M1，重核本机MMIO/SID/DMA/
队列停机，不能直接打开整个vendor栈或复用USB SID路由。


### v46 attempt-01 照片回填：准备阶段失败（2026-10-25）

IMG_6997.HEIC已归档attempt-01。屏幕明确RAM_WATCHDOG prepare failed: errno=6
@RamSystemCache.c:163；按交付调用链在初始PID1 exec前失败。加载、attach、
start、最终匹配共用ENXIO，照片不含阶段，不能凭6定位分支。未验收到
服务注册/匹配/启动、客户端或心跳。18项主机检查与交付/源码绑定复核
通过，实机结论仍为失败。未拍停机三标记，normal_controller_stop_verified=false。
后续须拆分失败阶段并核验异步发布/即时匹配竞态；不跳过原失败保护。


### v47：修复异步服务匹配竞态，保留准备失败分支（2026-10-25）

父v46独立APFS clone，保留v45的1MiB分块。XNU设备包/构建检查与组合
打包改动，UEFI源码、原始AppleWatchdog/client执行字节不改。
v46照片errno6不能唯一定位失败分支。独立源码/原始指令核验发现：
原start虚槽84以options0异步registerService；即时枚举仅接受MatchedState。
用v46交付源码及延迟匹配IOKit桩复现成功断言失败。v47覆盖注册槽84，
委托原IOService清Asynchronous并设置Synchronous；保留原start及唯一/
自身实例验证，不跳过保护。该竞态证据不等于v46照片分支已定因。

新增step/detail保留load原OSReturn、匹配计数等；失败屏幕显示
RAM_WATCHDOG <step> errno=<d> raw=<x>，继续阻止PID1 exec。
原root/entitlement/type/独占客户端、启停、心跳和软件deadline不改；
仍不宣称SM8550硬件WDT。详见watchdog-service.md与v47 REVIEW。

镜像203190272字节SHA256
a95d8052863380289bd16a99813beb927b1dfb82c15b424e808f697c9a50f78b；
prelink 52076544字节SHA256
ceafad072116db1018a40ccb3e289ac63c5e87c7efb9335f58739451e8098299。
FUXIPAYL、最低段0xfffffff00427c000/skew0x7c000、KMEM余量662548480
重新验证。构建第二轮/容器固件打包真实退出0，首轮沙箱sysctl失败日志保留。
完整18项检查run_id=20261025T074819.554188Z全部真实退出0，当前源码/
检查器/fixtures/对象/归档/镜像绑定复核；C++33例/4突变（含异步回归）、
原始客户端指令9例、缓存C45/5突变和CacheFinder5例，外部KPI显式桩。

本轮只读设备互斥检查无活动任务、fastboot列表为空。用户明确答复
“稍后测试”，没有新boot/传输/attempt。实机注册/匹配/启动、客户端、
心跳和完整系统启动均待验收，normal_controller_stop_verified=false。
下次用户准备fastboot后用v47 REVIEW命令与独立attempt-01；不重用v46
照片或主机成功作为服务已实机通过的证据。父v46交付镜像哈希保持不变。


### TWRP /data → UEFI 移植分支：M1 原生 UFS 只读诊断实现（2026-10-25）

feat/twrp-data-uefi 提交 85e38d18（UEFI 仓库，独立检出
ramdisk-test/uefi-twrp-data）。改动层：UEFI 诊断（新模块
FuxiNativeUfsDxe、MemoryMapLib 两个 MMIO 窗口、APRIORI 顺序）、主机
测试与候选构建工具、移植说明；DarwinBoot/XNU/USB 接收器协议/系统
打包未改。M0 探针模块与开关行为不变（no-smc/scm-query 变体重建
哈希与 M0 REVIEW 一致）。

实现按本机 XNU 已验证的 NativeUfs 读取路径转录：停/起队列序列、
单槽 UTRD 提交与轮询（完成/致命/超时，疑点即隔离缓冲并停止发令）、
响应/sense/query 校验、SMMU stream 0x60 计划与配置回读。数据方向只
允许 0/2，写入方向参数检查即拒绝；无密钥操作、无 BlockIo 发布。
诊断在 DXE 调度即执行（APRIORI 排在 USB 接收器之前：receiver-only
候选在入口内等待 180 秒后 CpuDeadLoop 驻留，到不了 ReadyToBoot），
空闲门不满足不写任何寄存器；独立 DMA 窗口 139264 字节、4GiB 以下、
不覆盖 ABL 遗留请求列表；SMMU 从次顶 bank 开始规划，顶 bank 原样
留给 USB 路由试验；GPT/F2FS 走 M0 限界读取；原始块 SHA256 为独立
实现；收尾再次停队列并打印 doorbell/故障寄存器。

验证：建模控制器主机测试（UTRD/UPIU、轮询隔离、响应、停队列计划、
SMMU 计划器含 From(1) 语义、SHA-256 对 hashlib）ASan/UBSan 通过；
M0 82 fixture 回归通过；DataPort.dsc 与完整 fuxi.dsc 平台构建均在
容器真实退出 0；交付模块反汇编 SMC 指令 0 条。receiver-only 候选
Mu-fuxi-usb-ram.img 1871872 字节 SHA256 9535d644afd4e1cf1f08a7e1956
e2257d3d0a1dda26b9efd1841497480f110c7，六项主机检查（smmu-audit、
route、usb-ram、dwc3-ep0、dwc3-stop、image）全部退出 0，报告绑定
交付镜像与源码。候选构建用实验检出 ramdisk-test/unixnu-source 的
bootimg.py（v3 头+os_version）；顶层 unixnu-source 旧版 bootimg 在
verify 处失败，此差异已记录在移植说明 §9，不改共享仓库。

未上机：当前 USB 总线无设备（fastboot/adb 均空），不满足
fcde91e 前置，未执行 fastboot boot。实机预期对照：GPT CRC 通过、
userdata 0x2f7840..0x3afc3fa、首块 magic f673c25f 7086bec0（XNU
2026-10-23 记录）、停机 readback doorbell=0。实机结论待照片；
主机检查通过不等于实机通过。


### TWRP /data → UEFI 移植分支：M1 实机迭代与 PRDT 字节序修复（2026-10-25）

feat/twrp-data-uefi 提交 c8ff2e23。上机迭代四轮，只 fastboot boot、
无分区写入、无载荷传输；每轮互斥检查与 fcde91e/product/unlocked 核对。

第一轮（9535d644）：屏幕照片显示加载 FuxiNativeUfsDxe.efi 后无任何
输出即复位。第二轮加 FUXIUFS stage 阶段标记（e1751e37）：照片定位
到 capacity 后复位——第一条带数据阶段的命令。根因：PRDT 表项被写成
大端；UFS PRDT 是小端原生存储（只有 UPIU 字段为大端），控制器按翻转
地址 DMA 触发 SMMU 故障、整机复位。修复为与 XNU 一致的小端存储，
主机测试同步断言小端 PRDT。

第三/四轮（21771c3b/89b40386，另加双通道数值输出 FnuLog 与
FUXI_USB_EP0_TRACE=0 静默逐包 EP0 跟踪）：诊断全程跑完、接收器枚举
成功。照片证据：USBRAM S19 ConfigureEnableEndpoints returned Success、
USB ROUTE VERIFIED; first DMA permitted、主机 SET CONFIGURATION 完成
（1209:0001）、USB CONTEXT existing bank=5。UFS 诊断与 USB 路由试用
在同一 SMMU 共存：UFS 从次顶 bank 规划，USB 按既有策略落在 ABL 留下
的 context bank 5，顶 bank 未被动用。该诊断入口在 receiver-only 候选
于 DXE 调度即执行（APRIORI 在接收器之前），接收器 180 秒窗口后驻留
CpuDeadLoop，永不进入 ReadyToBoot——M0 的 ReadyToBoot 触发点在
receiver-only 形态下不会执行。

最终候选 89b40386c0663a5c714973e97b0ef018ee874a2a619f26079ab02722651d
c1b9（1871872 字节，FD bfd51c19…），六项主机检查（smmu-audit/route/
usb-ram/dwc3-ep0/dwc3-stop/image）全部退出 0 并绑定交付。尚未采集到
FUXIUFS 数值块照片（GPT 结果、userdata 0x2f7840..0x3afc3fa 对照、
首块 magic f673c25f 7086bec0 对照、SHA256、stop readback）；接收器
枚举成功只证明诊断入口正常返回，不等于读取结论正确。实机结论仍为
进行中，四项 M1 证据待数值块照片回填。


### Windows / WSL2 迁移恢复与 Linux 主机检查适配（2026-10-25）

从 unixnu-handoff-20261025.tar.7z 恢复到 Ubuntu WSL2 的
/home/mouse/work/UniXNU；原 Windows 目录保留压缩包和入口报告，旧 WSL
UEFI 检出未改。六仓库快照、十个未跟踪文件与 58 个输出记录目录恢复，
五个非空补丁先检查再应用并通过反向检查；初次逐文件核对 3062 个文件、
2573743015 字节一致，v47 UEFI/DarwinBoot 共 30 项绑定匹配。原始记录和
快照另存，未激活旧认证或跨架构 venv。先恢复 449f06f，再快进 a0fa052
纳入打包后两条 M1 日志；独立 TWRP 检出保留包内阶段 M0 e64deffc。

改动层为主机检查器：check-usb-ram.py 的测试适配层在 Linux 使用
-shared/-fPIC 与 OpenSSL SHA256；macOS 保留 CommonCrypto/-dynamiclib。
生产 RamBulk 和 sender 不改。保留初次 Linux 失败日志；SMMU audit、
route、生产 C/Python 收发测试现均退出 0；watchdog 33 例/4 突变拒绝
通过。上游 Linux 主机依赖检查通过，UDIF 6 例在固定 LZFSE 源构建后
全部通过，无跳过。新结果在根 migration/20261025，不覆盖历史候选报告。

上游 16e592ab 已有 Linux XNU 工具链路径，不再要求 macOS/Xcode；
恢复的实验构建器尚无该支持，普通 Clang 18 也不含所需 XNU builtins。
完整定制工具链未安装，未执行完整 XNU 重建。实验 RAM-root/user-entry/
main-entry 扩展不在上游同名脚本中，后续移植须保留宏与编译对象保护，
不能直接覆盖。详见 windows-wsl-migration.md 和根迁移审查报告。

交接包无候选 .img、work/cache、完整 IPSW、boot-tests 等必要输入；本轮
未重跑完整镜像/编译对象门禁，未操作手机，不新增实机结论。v47 待上机、
v46 watchdog 准备失败的历史边界保持不变。容器恢复独立记录宿主 amd64
工具与 AARCH64 固件目标，不沿用旧 ARM64 venv。


### 2026-10-26：WSL 存储故障恢复与 v48 Linux 独立重建准备

宿主 D 盘耗尽曾引发 WSL Bus/I/O error；释放宿主空间并重启 Ubuntu 后 EXT4
日志恢复，根 rw、账号解析、dpkg/apt 检查正常。未重装或删除发行版。大任务新增
宿主 /mnt/d 余量监测与 12 GiB 保护；不再使用 ext4 虚拟余量代替实际容量。

已补齐完整 21H461 IPSW（全 ZIP CRC、组件 SHA384、固定输入 SHA256 通过）、
原始 launchd/dyld 及 RAM OS/SystemOS 载荷，窗口哈希与历史记录一致。实验 source
选择性引入四项上游 Linux 支持，01e3168 已推 mouse233 fork；本地 RAM 保护与
DarwinBoot 原恢复 diff 保留。UDIF6例、watchdog生产33例/4突变通过。

新建 usb-system-mount-v48-linux 与独立 Linux ABI reference，从配置记录重建，
不继承 v47 镜像/缓存或检查通过结论。UEFI/DarwinBoot 独立 smoke 构建成功，
但它只有 dummy 载荷。工具链及新内核/组合镜像/完整18项检查仍在推进。未操作手机，
v47 待上机和 v46 prepare errno=6 的历史边界不变。详见 wsl-linux-rebuild.md。


### 2026-10-26：UniXNU / UEFI 上游合并与 TWRP M1 Linux 构建

按本轮授权同步 mouse233 fork。UniXNU 上游 main ae68700 历史重写，
以旧上游 245aca1 到原 fork main 8612a53 的九文件差异逐项迁入，保留
RAM-root/user-entry 与静态 storage 审计保护。main c994e23、实验
feat/ramdisk-test 7e237de 均已推送；实验保留 DarwinBoot fork da5b43b、
RAM system/main/cache/trust 补丁、Linux dwarfdump/libsa 适配和旧候选
依赖/脚本兼容入口。原共享检出两处有未提交改动，未覆盖其工作；实验
audit-amfi-pmgr-contract.py 重叠使本机快进安全中止，合并树保留在
根 migration/20261026-upstream/unixnu-{main,ram}，远端分支已更新。

UEFI 上游 fuxi 5493eb07 已合入主分支 4fbf22ac、USB RAM 分支
32b3b57e、TWRP 分支 3222f6a7（文档跟进 7fdd506d），全部推送且远端
SHA 复核通过；本机三个 UEFI 检出均快进。保留本机 FD/内存布局、
受限 SEC FDT/载荷检查、USB 路由与 UFS 小端 PRDT；上游独立 loader/
extents 源码入库，但不自动调度其另一套 RAM 扫描器或未经检查的 HOB。

改动层：共享 XNU/构建框架、UEFI 源码合并及主机构建/打包工具；
不把组合镜像称作仅改 UEFI。TWRP 构建器兼容当前 amd64 Linux 宿主，
目标仍 AARCH64。no-SMC/query 两版 DataPort.dsc 与完整 fuxi.dsc/
Android-v3 receiver-only 打包真实退出 0；两个 DXE 均归档并绑定源。
M0 82 fixtures、M1 建模 ASan/UBSan/9 SHA256 向量通过；SMMU audit、
route、receiver、EP0、stop、交付 image 六项检查全部退出 0并绑定
同一镜像/源码。EFI 反汇编 M0 两版 0/1 条 SMC，UFS 两版均 0。

独立候选位于 migration/20261026-upstream/uefi-twrp/out/
upstream-sync-ufs-m1-receiver/，REVIEW 记构建命令/输入/边界；镜像
Mu-fuxi-ufs-m1.img（校验器同字节别名 Mu-fuxi-usb-ram.img）
1867776 字节，SHA256 a2b338dcf0ae19c647f92fdd714b5d90284a9faf0427486d452a8fa5f6b3470a。
使用合并后的 feat/ramdisk-test 基础；顶层 main bootimg 仍无本地 v3
元数据扩展，不改用其替代实验基础。

未操作手机、未新增 attempt/照片；GPT/magic/SHA256/停机 readback
数值块照片仍待采集，未宣称 /data 解密、BlockIo 发布或 XNU/系统启动。
原 v47 待实机、v46 errno=6 与 v48 完整 XNU/系统重建边界保持。
仓库源内合并说明见 docs/fuxi-upstream-sync-20261026.md；根
UPSTREAM-SYNC-20261026.md 为报告入口，详细退出码/日志在同名迁移目录。


### 2026-10-26：TWRP M1 Linux 镜像 attempt-01，进入接收器后 EP0 超时

用户进入 fastboot 后授权单次启动。交付仍为 upstream-sync-ufs-m1-receiver
的 Mu-fuxi-ufs-m1.img，1867776 字节，SHA256
a2b338dcf0ae19c647f92fdd714b5d90284a9faf0427486d452a8fa5f6b3470a。
改动层/测试范围：UEFI 原生 UFS 诊断与 USB 接收器；本轮没有源码/固件
改动、XNU 或系统载荷。复核镜像及 30 项源码绑定，沿用已绑定且退出 0
的六项主机检查。未覆盖其他会话改动。

互斥检查无活动测试；Windows fastboot 核对设备、product=fuxi、unlocked=yes。
当前 a 槽，a/b 均 successful=yes、unbootable=no、retry-count=6。原单次
工具在独立 attempt 适配 Windows fastboot 路径，保留 SHA256、verification、
usb-ram profile 与身份检查。16:27:39 +08:00 Sending/Booting 均 OKAY，退出 0。
只执行一次 fastboot boot，无 flash/擦除/改槽/重启/载荷传输。

用户照片 IMG_7051..JPG：USBRAM S19 ConfigureEnableEndpoints returned Success；
WAIT 1209:0001；speed=3 两次；USB CONTEXT existing bank=5 checked=1，
USB ROUTE VERIFIED。随后 Endpoint Command 0x8 on Physical EP 0 timed out；
ENDTRANSFER failed，RscIdx=0。Windows 启动后 USB 快照未见 1209:0001。
因此确认固件到达接收器，但不能判为 USB 枚举/传输成功。

源码推论：EP0 命令等待 CMDACT 清除超时后置 Ep0RestartFailed，阻止再武装；
照片不能区分具体触发事件或硬件根因。RscIdx=0 对 EP0 OUT 是合法值，不能
仅凭该值归因。照片未含 FUXIUFS GPT/magic/SHA256/stop 数值块，也未含
QUIESCE Success、路由恢复或 HALTED，UFS 验收及正常停机仍待证据。
候选 REVIEW 已回填；照片、device-test.json、assessment.json 与 REPORT.md
归档在 migration/20261026-upstream/uefi-twrp/out/
upstream-sync-ufs-m1-receiver/attempt-01；Windows 入口为
D:/Projects/UniXNU/migration/20261026-m1-test/attempt-01/。
后续应定位 EP0 ENDTRANSFER/重置再武装路径，并改善 UFS 数值证据留存；
本轮不自动重复启动，不宣称 /data 解密、BlockIo 或系统启动通过。


### 2026-10-26：M1 ufs-m1-report-v2，UFS 原始读取/队列停止与缓存重放实机证据

改动层：UEFI UFS 诊断文本缓存/状态快照、接收器驻留前报告重放和主机构建/
检查工具。48行×192 CHAR16有界缓存封存后不可追加，截断/溢出显式提示；
诊断结束停留20秒，接收窗口停止后重放，不重新读取寄存器或发令。新增
接管前后HC/SID60/SMMU快照；UfsCore/PRDT/队列/SMMU策略及DWC3实现未改。
未引入参考仓库的提前接管分支、写盘日志或根卷发布放宽。

独立候选 ramdisk-test/uefi-twrp-data/out/ufs-m1-report-v2/，镜像1871872字节，
SHA256 2720dbdd9ad49d9cf64fb21703b9dc5ef9459d28777188d20e5a6b37cb3fbae9。
两个DataPort变体/完整平台/Android-v3打包退出0；D盘监测始终>46GiB。
缓存ASan/UBSan、M1控制器/9SHA向量、M0的82fixtures通过；六项交付检查
及额外报告/EFI绑定检查均真实退出0。验证33项源码，UFS EFI在实际交付FD
内、协议存在于两个模块；交付及两变体UFS的SMC指令均0。

用户授权下一步测试，互斥检查空闲，设备/product/unlocked及profile/哈希
核验；当前a，两槽successful=yes/unbootable=no/retry=6。共享启动器被另一
会话补了Linux路径发现，独立适配首轮仅主机fastboot路径预检查失败，未发
设备命令；修正独立副本后16:56:29+08:00仅一次fastboot boot成功，退出0。
无flash/擦除/改槽/载荷传输/重启。原始报告与照片归档于候选attempt-01，
Windows入口 D:/Projects/UniXNU/migration/20261026-m1-report-v2/attempt-01/。

IMG_7052显示NOP/query/wakeSSU/TUR/capacity/GPT成功；LUN0 last=3afc3ff，
block=4096，metadata=4000..7fff，userdata=2f7840..3afc3fa。GPT成功绑定
生产header/array CRC与边界验证。metadata F2FS头成功，userdata未找到；
仅头部检查，不是挂载/解密。userdata magic=964cc46e 60ab062f，与历史
f673c25f 7086bec0不同，待独立当前读对照，不能凭差异宣称损坏或解密。
userdata首块SHA256 ad3ccf81e2258a4754e7c28dcd67866de05dee85b5b732190aa0345395156f4d；
metadata首块SHA256 e77426cf7e4e7d0c2af46e8c8434a358a6c8f923c7b614ec8a72ec288446e51d。
它们是照片中的单次读取哈希，缓存重放不等于重复读一致或独立比对通过。

UFS选slot104/bank68/pages128，SID60从0个匹配变1个，最终SCTLR=0、
CBAR=10000/CBA2R=1、GFSR=0。FSR=400仅format[10:9]=2，按Linux
ARM_SMMU_CB_FSR_FAULT掩码c00001fe没有故障位，不能把非零FSR或FAR
直接当DMA故障证据。UFS stop=1/db=0/run=0，最终tdb=0、HCS=10f/IS=0、
poisoned=0，获得原始读/GPT和UFS队列停止实机证据。

IMG_7054重现USB EP0 cmd8/ENDTRANSFER超时；IMG_7055补齐接收超时后的
DWC3 halt timeout DSTS=0010ff11/route retained，QUIESCE=Time out，
buffers retained。随后完整CACHED REPORT及STOP证明最终重放实机执行。
USB枚举/传输和正常停机仍失败，normal_controller_stop_verified=false；
不把UFS队列停止当作USB HALTED，不放开保留DMA缓冲与路由的保护。
候选REVIEW与docs/fuxi-ufs-cached-report.md已回填三张照片结论。后续定位
EP0结束传输/重置再武装与USB停机，另取独立userdata对照；无系统启动结论。


### 2026-10-26：M1 EP0 A/B 三候选（V-A/V-B/V-C），纠正 trace=0 的前提与停机 DSTS 读数

改动层：UEFI DWC3 gadget 驱动的编译期宏与 M1 候选构建工具。UFS 核心/SMMU 策略/
PRDT/队列/超时隔离/USB 状态机/payload/内核均未改。本轮**未操作手机**。

前提纠正一：上一轮把 `FUXI_USB_EP0_TRACE=0` 记为"同一套代码只少日志"。实际打开
trace 编译不过：`FUXI_TRACE(Arg)` 定义为 `DEBUG Arg`，调用点已自带一层括号，展开
后 DEBUG 收到两个参数即报错；`=0` 时宏为空可编译。故本分支所有产物的每包 EP0/
SETUP 追踪是**不存在**而非静音。两处改为 `DEBUG (Arg)` 修复（Dwc3Endpoint.c 4 处、
FuxiUsbRamDxe.c 2 处；Dwc3Core.c 另一套裸字符串约定，正确未动）。产物侧证据：
交付 EFI 内 `FUXI EP0` 命中 4（父候选 0）、`USBRAM: setup rt` 命中 1（父 0）。

前提纠正二：重判读归档照片 IMG_7055，停机失败值为 `DSTS=0x0010FFD1`，此前记录写作
`0010ff11`（照片放大核对）。两种读数结论一致：`DEVCTRLHLT`(bit22)=0、USBLNKST=4、
COREIDLE=0，核心从未确认停机；差别仅在 SOFFN 字段。历史条目未改写，此条作追加更正。

新增源码结论（与上游 torvalds/linux dwc3 对照）：EP0 物理编号 OUT=0/IN=1、
`0xC800+n*0x10` 与上游一致，排除该嫌疑；但上游 `ep0.c dwc3_ep0_end_control_data()`
构造 `ENDTRANSFER|CMDIOC|PARAM(resource_index)`、DEPCMDPAR 全 0 且**不带 ForceRM**，
本地 `Dwc3EndTransfer` 无条件带 `HIPRI_FORCERM`，且本地注释"END 完成前保留共享 TRB"
与之自相矛盾。上游 `gadget.c` 亦记新版编程指南建议 ForceRM 清零以便完成时更新 TRB
进度。此为命令构造分歧，是否成因需实机判定，不据源码宣称修复。

照片可判读的排除项：`FUXI SNAP ABL`/`SNAP POST`/`FUXI LINK t=0ms` 三处独立读到
`PHYCFG=0x00102400`，`SUSPHY`(bit6)=0、`ENBLSLPM`(bit8)=0。上游要求 speed≤HIGH 或
EndTransfer 时每次端点命令前清零重载这两位（DWC_usb3 3.30a §3.2.2）；本候选在启用
连接时已为 0，且驱动除 `Dwc3CoreExit` 外不再置 SUSPHY，故该 PHY 挂起假设无证据支持。
边界：照片无超时瞬间的 PHYCFG 采样。t=0ms 时 link=0(U0)、DSTS speed=0(HIGH)、核心
未停机，与随后 `USBRAM: speed=3` 一致；停机时链路已落状态 4。

三候选（容器 unixnu-fuxi-build-arm64，x86_64 宿主，AARCH64/CLANGPDB；工具新增
`--usb-ep0-trace`/`--usb-ep0-forcerm`/`--without-ufs` 并写出双镜像名）：
V-A `m1-ab-ep0trace-v3` Mu-fuxi-ufs-m1.img 1871872 B
b64dfe070131f6fedcb3b08fe92437204fcfb08da72f68bd365cffb68ec2e697（trace 开、ForceRM
同已验收、UFS 前置在）；V-C `m1-ab-ep0noforcerm-v4` 1871872 B
82925412455da1c76e9e74c73c386bbd0813587d763d620e9785affa0be92723（仅 EP0 不带 ForceRM）；
V-B `m1-ab-usbonly-v3` Mu-fuxi-usb-ram.img 1863680 B
ba22cc22bb3f8d7012a1c55168b209d03f2444b6000ed4abca02ddf91f802eb2（`FUXI_DATA_PORT_UFS=0`，
USB 前无 UFS 接管/SMMU 重配/DMA 分配，亦是最接近历史 macOS 接收器的对照）。

源码绑定：V-A/V-B 相对父候选差异恰 3 文件（两处宏修复 + 构建工具），FuxiNativeUfsDxe
与 UFS 核心逐字节相同，故父候选 UFS 结论可延续。修复宏后首建 V-A 为 f7eeeeff…，加
define 后重建为 b64dfe07…，说明本流程镜像哈希跨次重建不稳定；因此"默认值保持原行为"
不以哈希相等取证，改以 `logs/firmware.log` 取证：V-C 有 5 条
`-DFUXI_USB_EP0_FORCERM=0`，V-A 无该宏（走源码默认 1）。

主机检查：`migration/run-m1-ab-checks.py` 逐条记真实退出码，三候选各 6 项（SMMU audit、
route、receiver、EP0 模型、stop 模型、交付镜像）全部退出 0。未复跑因此不宣称：
`check_fuxi_ufs_report_image.py`（依赖候选自身 verification.json 与 modules-no-smc/
modules-scm-query 独立构建）、缓存 ASan/UBSan、M0 82 fixtures、9 组 SHA256 向量。
既有缺口记录：EP0/stop 模型检查编译时不带 `-DFUXI_USB_DMA32` 也不带本轮两个宏，测的
不是交付 TU，且缺"同一 DEP 块 CMDACT 未清时再发命令"与"EP0 命令未确认时清 RUN_STOP"
两个当前最相关场景；其 fixture 仍未入库（mtime 10-23），干净克隆跑不了这两项检查，
按 §2 保留其他会话未完成改动未代为提交。

D 盘余量全程 46G，高于 12 GiB 下限；未触碰其他会话的 18 个未提交改动与
usb-system-mount-v48-linux 的失败排队任务（`work/rerun-linux-checks.py` 第 34 行
`PosixPath % int` 自身报错，与本轮无关）。

下一步：设备需用户重新进 fastboot，按 §0 核验互斥与 fcde91e/product/unlocked，只用
`fastboot boot`，每台一次、独立 attempt。顺序 V-A（首次取得真实 EP0 命令序列）→
V-C（判定 ForceRM 分歧）→ V-B（仅 V-A 同样失败时，隔离 USB 前的 UFS/SMMU 状态）。
`Ep0RestartFailed` 全驱动无清零点，一次 500 ms 超时永久失去 EP0，与 Windows 快照
看不到 1209:0001 一致；它是症状放大器不是根因，无实机证据前不放宽该保护。
分析入口 D:/Projects/UniXNU/migration/20261026-m1-ep0-ab/REVIEW.md。


### 2026-10-26：M1 EP0 A/B 实机三轮（V-A/V-C/V-B）与 V-D 被自身模型否决

改动层：仅 UEFI DWC3 gadget 诊断输出与候选构建开关；UFS 核心、SMMU 策略、
PRDT、队列、超时/隔离策略、USB 状态机、payload、内核均未改。三次各一次
`fastboot boot`，无 flash/擦除/改槽/载荷传输/重复启动。互斥检查均空闲，
`fcde91e`/product=fuxi/unlocked=yes，profile=usb-ram，启动前镜像与
verification.json 哈希一致。

V-A `m1-ab-ep0trace-v3` b64dfe07…，18:46:xx 启动 OKAY。首次取得真实 EP0
序列：`cmd=1` 建立 ep0-ep3 全部 `complete reg=00000001`（status 0）、`cmd=6`
武装 SETUP `reg=00000006`、`evt=7 status=0`（EPCMDCMPLT）、
`USBRAM: bus reset; session cleared` 后完整重建端点。关键：出现
`complete ep=0 reg=00000908`，即 CMD=8、CMDACT(BIT10)=0、STATUS=0 的
**EP0 EndTransfer 成功完成**。因此上一轮记的“EP0 ENDTRANSFER 超时”不能读成
“该命令从不完成”，此前记录把间歇失败写成了必然失败。`USBRAM: bus reset;
session cleared` 一行本身在坏宏的 FUXI_TRACE 内，修复前任何产物都印不出来。
主机侧 `Get-PnpDevice` 278 次采样（18:48:20-18:58:15）全部无 VID_1209，
也无以其他 ID 落地或报错的 USB 设备。停机仍 `STOP status=Time out;
buffers retained`。

V-C `m1-ab-ep0noforcerm-v4` 82925412…，19:02 启动 OKAY。**否证 ForceRM 假设**：
仍走到同一条 `DWC3: Endpoint Command 0x8 on Physical EP 0 timed out!` /
`ENDTRANSFER failed on Physical EP 0, RscIdx 0: Time out`，主机
（至 19:04:42）全 none。照片给出改动生效的直接证据：提交的命令字为
`cmd=108`，而 V-A/V-B 为 `cmd=908`，差值正是
`DWC3_DEPCMD_HIPRI_FORCERM = BIT11 = 0x800`（Dwc3.h:242）；
`CMDIOC = BIT8 = 0x100`，两者相加 0x8|0x100|0x800 = 0x908 与 V-A 读数逐位吻合。
该轮失败前有两轮 EndTransfer 在**同一端点同一调用点**成功完成，第三轮才不完成。

V-B `m1-ab-usbonly-v3` ba22cc22…，19:22 启动 OKAY。**否证“USB 前先接管
UFS/SMMU/DMA 导致 USB 失败”**：`FUXI_DATA_PORT_UFS=0`，无 FuxiNativeUfsDxe、
无 20 秒停留、无 FUXIUFS 早期报告，`S19`/`WAIT 1209:0001` 之后的序列与
V-A/V-C 同形，最后仍 `cmd=908` 于 Physical EP 0 / RscIdx 0 超时；主机 109 次
采样全 none。三条轴（trace 编译与否、ForceRM、UFS 前置）到此关闭。

独立第二读（关闭上一轮遗留项）：V-A 与 V-E 屏幕上的报告给出 userdata 首块
SHA256 `545471f32e59c962688f8ae078fd768ea54750fa9c14f0a160859c31c3aa8c3e`，
与 report-v2 16:56 的 `ad3ccf81…` **不同**；而 metadata 首块
`e77426cf7e4e7d0c2af46e8c8434a358a6c8f923c7b614ec8a72ec288446e51d` 两次
**逐字符相同**，magic 两次均为 `964cc46e 60ab062f`。4096 字节的 metadata 块不变
而 userdata 块变、头 8 字节不变，最合理读法是 Android 在两次启动之间写了 /data，
不是读盘不稳定也不是损坏。仍只是头部检查，屏幕明写
`metadata-decrypt=UNIMPLEMENTED file-read=UNIMPLEMENTED`，不构成挂载或解密。
同一屏内停留与 Park 重放是同一次读的重复显示，只有跨启动才是两次独立读。
容量/GPT/边界/停队列/bank68-slot104 与上一轮一致。

V-D 未产出镜像、未动设备：按“总线复位时恢复 EP0 闭锁”写了两版，都被驱动自己的
主机模型当场否决——同时清 `Ep0RestartFailed` 与两个 `EndTransferPending` 触发
`dwc3-ep0-host.c:329` 的 `CommandWrites == Commands` 断言（复位分支会向该 DEP 块
重新发命令）；只清 `Ep0RestartFailed` 触发 :325 的
`Dwc3SetHalt (&Dev, 0, TRUE) == EFI_DEVICE_ERROR` 断言。19 个用例里有 6 个专门
管 END 未结时不得信任 EP0 状态，其中
`both EP0 END acks are required and CONNECT_DONE cannot reset resources mid-cancel`
正是本改动要做的事，共同内容是“不得向 CMDACT 可能永不清零的块再写 DEPCMD”，
而这恰是本次故障的疑似物理成因。硬做须先删既有保护，且会让一次启动同时改变
两个变量，故撤销；`git status` 已回到干净，EP0 模型恢复 19/19。

撤销过程中确认一个真实后果并值得单记：`Dwc3Endpoint.c:895` 的 early-out 在
`DWC3_DCFG` 设备地址清零与 unstall 循环**之前**返回，所以首次 END 失败后的每次
总线复位只给接收器发消息、硬件层面什么都不做，设备一直保留旧地址。这解释了
“前两轮正常、第三轮起再也不行”，结论收窄为：**第一次失败的 END 是第一因，
闭锁是放大器**，修闭锁不等于修问题。

V-E `m1-ab-ep0snapshot-v6` 3790775f…，与 V-A 只差一段只读失败快照
（`FUXI SNAPFAIL`，回读 DEPCMD/DEPCMDPAR0/DCTL/DSTS/GEVNTCOUNT 与 TRB 所有权，
上限 6 次，不发命令、不应答事件、不分配；仅使用本树已定义偏移）。交付 EFI 内含
`SNAPFAIL` 字符串，六项主机检查退出 0；**EP0/stop 模型编译时不带
`FUXI_USB_DMA32`，该段代码模型完全覆盖不到，只能实机验证**。19:44 启动 OKAY，
19:47 抓到快照（V-E 主机侧 250 次采样仍全 none）：

```
FUXI SNAPFAIL ep=0 cmd=908 DEPCMD=00000D08 PAR0=00000000 DCTL=90F00000
  DSTS=0015D4D8 GEVNTCOUNT=00000010 trb=00000C23 started=1 rsc=0
```

逐位按本树定义解：`DEPCMD=0xD08` → CMD=8、**CMDACT(BIT10) 仍置位**、FORCE
RM(BIT11)+CMDIOC(BIT8) 与提交值一致、`STATUS=(x>>12)&0xF=0`（完全无结果）；
`DCTL=0x90F00000` → RUN_STOP 仍置位，核心在跑；`DSTS=0x0015D4D8` →
CONNECTSPD=0(HIGH)、`USBLNKST=5`，按本树 **USB2** 编码表（Dwc3.h:212-219，头文件
明写 SS 字段同但编码不同）为 `HS_EARLY_SUSPEND`，DEVCTRLHLT=0；
**`GEVNTCOUNT=0x10` 非零** → 超时那一刻事件环里还有未取走的事件；
`trb=0x00000C23` → **HWO(BIT0) 仍为 1，TRB 还在硬件手里**，`LST`=1、
`TRBCTL=(x>>4)&0x3F=2=CONTROL_SETUP`、IOC=1；`started=1 rsc=0`。

因此这不是驱动读回竞态：命令确实从未被核心退休，SETUP TRB 仍归硬件。由此留下两条
线索：(1) `Dwc3SendEpCommand` 等待期间只轮询 `DEPCMD`、不调用
`Dwc3ProcessEvents`，`GEVNTCOUNT` 非零说明完成事件无处被消费，这是唯一一个
“自己造成”的候选，形状正是死锁（命令待发完→事件不被取→没人取事件）；
是否需要读环并应答 `EHB` 核心才退休命令，仍属硬件问题。(2) 失败瞬间链路处于
early suspend。**更正本报告上文一处措辞**：前文“PHY 挂起假设无证据支持”依据的是
`FUXI LINK t=0ms`（启用连接时）的 `PHYCFG=00102400`，不是失败瞬间的采样；快照未含
`PHYCFG`，故本轮既不支持也不排除，应改述为“失败瞬间未见证据”。上游该规则正好适用
于 speed ≤ HIGH，也就是本次状态。

判读与照片、主机日志归档：`D:/Projects/UniXNU/migration/20261026-m1-ep0-ab/`
下 attempt-01..04（V-E 原图 `V-E-snapfail.jpg`
5d3d6ab4…、`V-E-later-session.jpg` 0ba6664e…）与 `VD-not-built.md`。
遗留：V-A/V-B/V-C 绑定提交 28e2f007，V-E 绑定其后的快照提交；
`dwc3-ep0-host.c`/`dwc3-stop-host.c` 仍未入库而被已跟踪检查脚本硬依赖。


### 2026-10-26：TWRP 分支 M1 read-proof v8 修复与 M2 草稿（待实机）

改动层：UEFI DWC3 命令串行保护、UFS只读诊断/收尾、构建输入绑定与主机检查；
没有修改 XNU 或系统载荷。撤出38ba3c33轮询中的完整事件分派，CMDACT未清时
不写该DEP的参数/命令；保留PHYCFG及最多4个CPU视图原始事件字快照，不消费事件。
新增RESET/CLEARSTALL命令覆盖与生产stop联合用例，22项EP0跨4配置及9项stop通过。

metadata/userdata在同次启动分别三次独立读取、使用不同缓冲区逐字节比较；
出错/不同即停止。撤销shutdown后的Read能力，隔离后不再读寄存器，stop证明包含
双doorbell、run及fatal状态。M2所需feature/cp_payload/CP/NAT/main也进入报告。

候选 ramdisk-test/uefi-twrp-data/out/m1-read-proof-v8，1871872字节，SHA256
fdab273913dbe4a7853b9d33ec3c8332b5943aedab344cce5782baf87ec32de8。
框架7e237de，显式设备board输入1d071da；共享schema2未改。完整平台与两种模块
构建成功，十项主机/交付检查真实退出0；实际UFS EFI位于交付FD、协议绑定通过，
交付及no-SMC/query变体的原生UFS SMC指令均0。D盘最终构建最低45.33GiB。

M2 F2fsReadOnly草稿随模块编译但不在设备调用：超级块/CP引导、NAT读取地址计划、
非inline目录项解析，13类fixture及读取证明经ASan/UBSan通过。NAT journal、inode、
路径和文件字节读取未完成，无挂载/解密结论。

用户稍后准备fastboot；本轮未启动/传输/刷写/改槽，无新实机通过结论。
原Windows USB枚举/停机故障仍待设备验证，不能以本轮主机通过宣称修复。
M1实机须拍三次读取proof、hash、GPT、队列stop和poisoned=0；USB停机另判。
构建/检查/候选REVIEW与M2续接见docs/fuxi-m1-read-proof-m2-draft.md；
Windows交付入口 D:/Projects/UniXNU/migration/20261026-m1-read-proof-v8/。

本轮UEFI源码提交：75a7faca13ebc849f1e04ea1241179a7464f32dd（mouse233/uefi-fuxi feat/twrp-data-uefi）；实机待准备。


### 2026-10-26：M1 read-proof v8 attempt-01，原始读一致性与UFS停队列通过，USB仍失败

互斥空闲、fcde91e/product=fuxi/unlocked与usb-ram profile及交付SHA核验后，
2026-10-26T21:53:19.181421+08:00仅一次fastboot boot，Sending/Booting OKAY、退出0。
镜像fdab2739…绑定75a7faca；本候选十项主机/交付检查已全部退出0。
无flash/擦除/改槽/载荷传输/重启/重复启动。

IMG_7066/7068/7069原图已存候选attempt-01，哈希与结构化判读在assessment.json。
NOP/query/wakeSSU/TUR/capacity/GPT成功，LUN0 last=3afc3ff、block=4096；
metadata=4000..7fff、userdata=2f7840..3afc3fa。两个分区均为
independent-read-proof=Success reads=3/3 distinct-buffers=1；是同次启动
三条读取命令的比较，缓存重放不另计读取。metadata SHA256
e77426cf7e4e7d0c2af46e8c8434a358a6c8f923c7b614ec8a72ec288446e51d；userdata SHA256
890d7f621df98d0e652092fd7e35e31f4adf4e72fa654a7eca6796e1d379be33。
UFS stop=1/db=0/tdb=0/run=0/HCS=10f/IS=0，final poisoned=0/ready=0。
bank68/slot104/SID60/GFSR=0，FSR=400无c00001fe掩码故障位。
本轮有界原始读取/一致性/队列停止范围通过；未新增TWRP独立块对照、
SMMU完整回滚、BlockIo发布、文件读取、解密或系统启动通过结论。

metadata M2-input：feature=499、cp-payload=0、cp=512、nat=2560、main=4096。
M2 v2按实测能力位仅扩展SB/CP引导识别并记录Features；inode/文件策略不放行。
14类fixture及AARCH64模块编译通过，无M2设备执行；v8镜像仍绑定旧提交。

USB仍EP0 ENDTRANSFER超时，DEPCMD=d08、PHYCFG=00102400，失败瞬间两挂起位为0；
DSTS=0001f958无DEVCTRLHLT。CPU视图原始事件pos80/84/88/92为
201/301/50301/101，即CONNECT_DONE、link(info0)、link(info5)、RESET，
所采样四字不含END完成事件，不支持此前把积压直接定性为END完成死锁。
count与事件字是分次采样，非原子历史。最终USB halt timeout DSTS=00112a41，
QUIESCE=Time out、route/buffers retained；normal_controller_stop_verified=false。
用户听见连接/断开提示音；Windows 59次采样无VID1209，但捕获新增
Unknown USB Device (Device Descriptor Request Failed)，VID0000/PID0002，
故不能写“主机完全无发现”，仍无成功描述符/枚举证据。
原图/报告位于out/m1-read-proof-v8/attempt-01，Windows入口同轮migration目录。

实机证据与 M2 v2 bootstrap 源码提交：`e813b0fd5e78ad8ea58af1b91f6a2c5ba0c513e1`（`mouse233/uefi-fuxi feat/twrp-data-uefi`）。本轮上机镜像仍绑定 `75a7faca`，新 M2 源码未替换旧镜像绑定，也未再次上机。


### 2026-10-26：TWRP 分支 M2 metadata-files v1（主机通过，未上机）

改动层为 UEFI 只读 F2FS、UFS 诊断入口和交付工具。clean checkpoint NAT journal
（含 compact journal）覆盖所选 NAT 副本，node/inode footer 与策略检查，inline/block
目录及绝对路径、直接/间接/双重间接与稀疏文件读取已实现；中途错误输出清零。
保留 dirty/未知特性/加密/verity/压缩/casefold、cp_payload、多设备等拒绝边界。

候选 ramdisk-test/uefi-twrp-data/out/m2-metadata-files-v1，1875968 字节，
SHA256 cf28d6375ae7a321334fa789ef996a7f7a8e72df2a806bde9d5e9453160cf009。完整 AARCH64 平台、两种启用 M2/park 的模块构建成功；十二项主机/
交付检查退出0。38组文件fixture、14组引导fixture、独立 mkfs/sload feature499
卷的三个真实文件读取对照通过（ASan/UBSan），均不是手机读取成功。
交付FD/EFI/源码绑定通过，交付及两个独立原生UFS EFI均0条SMC。

实机候选先执行原M1 proof，再列metadata根目录（最多16项），尝试
/vold/metadata_encryption/key/version（最多1MiB，存在性待确认），完整成功才打印SHA。
随后停UFS队列并驻留在APRIORI原生UFS入口，不调度USB接收器，不发载荷、不交接XNU。
这不修复原USB枚举/停机故障。TWRP独立文件/首块对照、M2实机结果均待采集。
本轮没有再次操作手机，无boot/flash/擦除/改槽/传输；不覆盖旧v8及其attempt绑定。
Windows交付入口 D:/Projects/UniXNU/migration/20261026-m2-metadata-files-v1/，
设计、命令与验收边界见 docs/fuxi-m2-metadata-files.md 和候选 REVIEW.md。

本轮 M2 源码提交：`6bcce79e08675dd732e72fdf3433a6566ee7fcb3`（mouse233/uefi-fuxi feat/twrp-data-uefi），交付49项源码哈希与该提交内容一致。默认关闭模块回归亦通过。


### 2026-10-26：M2 metadata-files v1 attempt-01，首个 inline 文件实机读取成功

2026-10-26T22:49:51.566129+08:00 serial/product/unlocked/profile/SHA及互斥检查后，仅一次fastboot boot，
Sending/Booting OKAY、退出0。镜像cf28d637…cf009绑定6bcce79e，十二项主机检查已退出0。
新M2单次启动适配器显式验证诊断驻留配置，未篡改receiver_only=false的原验证报告。
无flash/擦除/改槽/载荷传输/重启/重复启动。

IMG_7071原图SHA256 52b44e06e613dbb300ba42c7989c4e5543e85c40e455d61b323fe5b6cca3717b，保存在候选attempt-01。
M1两个分区3/3读取与首块SHA保持一致。M2 prepare/root-open/root-list/lookup成功，
CP pack2、flags145、version5788ea72、feature499、journal0；根目录13项（含点项）。
/vold/metadata_encryption/key/version：nid69、bytes1、inline1，file-read=Success，
SHA256 6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b，与主机SHA256(b'1')一致，但这不是TWRP独立读取证明。
UFS stop1/db0/tdb0/run0/HCS10f/IS0，poisoned0、ready0；最终
FUXIM2 PARK: no USB receiver; no handoff，watchdog-disable成功。

本轮首个inline文件读取已观察到；目标未经过非inline文件数据块路径，且journal0，
因此不能推广为非inline/非空journal实机通过。TWRP同状态独立对照、非inline完整
文件SHA比较仍待补齐；无Android解密、RAM对象加载或XNU交接。原USB故障未重测。
详见 out/m2-metadata-files-v1/attempt-01/REPORT.md、assessment.json 与
docs/fuxi-m2-device-20261026.md；Windows同轮migration目录。

实机证据归档提交：`8614cb4cb4bebbc6742fc040e4a868f2b59909cf`（mouse233/uefi-fuxi feat/twrp-data-uefi）；镜像源码仍固定6bcce79e。

### 2026-10-27：A17 TWRP 参考纠正与 M2 独立对照

用户纠正旧 A16 TWRP 不能解密本机 Android 17，上传并进入
`TWRP-FUXI-A17.img`（104857600字节，SHA256 56f5f36e9aaeb9e79815c702d9e9196c8c0e1b03d363ec4e4c9646cd4af1444c）。
静态解包逐条比较：新镜像 CPIO 4025项，旧镜像4026项，仅新旧共同条目的
`prop.default` 不同，旧镜像另有空 `.` 条目；主程序、fstab、KeyMint及
UFS/ICE/SCM模块未变化。新属性 release17，但SDK32、主程序A15标记仍在；
不能由属性差异单独断定解密成功原因。

新TWRP ADB确认 fcde91e/fuxi、release17、/dev/block/dm-7在/data以f2fs rw挂载，
/data/system与/data/media/0目录可见；用户报告解密成功。只读对照：
metadata version 1字节、SHA256 6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b；
metadata首块SHA256 e77426cf7e4e7d0c2af46e8c8434a358a6c8f923c7b614ec8a72ec288446e51d，
均与M2 attempt-01一致。userdata首块SHA256 f7480f0ad4b1080c71b3664b0402dbe9c0e8f11d9b6a539d1777139ecc41fbec，
与上次UEFI不同；TWRP已rw挂载卷，不据此判断损坏。
选定下一非inline目标 /metadata/aconfig/maps/system.package.map，10527字节、SHA256
14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991；尚无UEFI读取。
原M2镜像/源码绑定不变。静态报告及TWRP JSON在
D:/Projects/UniXNU/migration/20261027-twrp-a17-static/；仓库报告
docs/fuxi-twrp-a17-static-20261027.md。未flash/写分区/读取密钥内容。

### 2026-10-27：M2 non-inline file v1 主机候选

接续 A17 TWRP 已核验的非秘密文件 /metadata/aconfig/maps/system.package.map：
10527字节，SHA256 14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991。
新 UEFI 候选将卷相对路径固定为 /aconfig/maps/system.package.map，保持 M1 原始
重复读、GPT、F2FS clean policy、完整文件SHA、UFS停队列及 pre-USB park；未启用
Android解密、USB接收器、RAM对象或XNU交接。

源码提交0586ebe8af5a0ee6fe26ea3271f94ab6cfac5201；镜像
out/m2-noninline-file-v1/Mu-fuxi-m2-metadata.img，1875968字节，SHA256
6b7e00d7738d7372f6489fb1e39165b56db9034f1e63ad74a0598700d503432d。
完整平台、no-SMC/query独立模块及12项主机/交付检查退出0；交付EFI目标路径、
FD包含关系、源码绑定49项、模块绑定各19项通过。主机通过不等于设备通过。
本轮无设备boot/flash/擦除/改槽/载荷传输；手机仍由用户保持在A17 TWRP。
Windows交付入口 D:/Projects/UniXNU/migration/20261027-m2-noninline-file-v1/。
下一次仅在核对互斥与fcde91e fastboot后单次临时boot，拍下文件size/hash与
UFS停队列/park；非inline、非空journal与解密尚无实机结论。

### 2026-10-27：M2 non-inline file v1 attempt-01 已临时启动

2026-10-27 10:20:29 +08:00，互斥检查无并发设备任务，fcde91e/product=fuxi/
unlocked=yes、usb-ram profile、镜像SHA与12项检查核验后，只执行一次
fastboot boot；Sending OKAY 0.059秒、Booting OKAY 1.644秒、退出0。
镜像 SHA256 6b7e00d7738d7372f6489fb1e39165b56db9034f1e63ad74a0598700d503432d，
绑定源码0586ebe8af5a0ee6fe26ea3271f94ab6cfac5201。
当前等待手机照片；不把主机启动OKAY当作非inline读取、UFS停止或M2通过。
原始记录位于 out/m2-noninline-file-v1/attempt-01/device-test.json，Windows交付
目录为 D:/Projects/UniXNU/migration/20261027-m2-noninline-file-v1/attempt-01/。

### 2026-10-27：M2 non-inline attempt-01 checkpoint 不满足 clean policy

IMG_7090 原图 SHA256 f6c1010e630aea66d14817fd5654dc613bc7b84c875a9f8cdf9b247d03b127ed。
UFS NOP/query/wake/TUR/capacity/GPT成功，metadata与userdata独立读取各3/3，
两者首块SHA与此前A17 TWRP一致。metadata feature=0x499。
FUXIM2 prepare=Unsupported，selected-pack=2、cp-flags=0x44、read-bytes=0。
源码 F2rPrepare 要求 CP_UMOUNT_FLAG=0x01；0x44没有该位，所以在目录/文件
查找前按策略拒绝。无目标文件读取/hash，不能认定非inline读取通过；不把缺位
本身归因为文件系统损坏或确定TWRP操作是根因，也不回退旧CP或绕开保护。
UFS stop=1/db=0/tdb=0/run=0/HCS=0x10f/IS=0、poisoned=0，最终pre-USB park；
SMMU configured=1/GFSR=0/FSR=0x400，未宣称完整路由恢复。
下一步可正常启动Android并使用系统菜单关机，再从关机状态进入fastboot复测；
正常运行可能更新文件与metadata，清洁标志和文件基准均须重新判断。
原图/assessment/报告见 out/m2-noninline-file-v1/attempt-01/，Windows交付同名目录。

### 2026-10-27：M2 non-inline attempt-02 临时启动

用户报告从Android正常关机后进入fastboot。2026-10-27 10:27:41 +08:00，
设备互斥空闲、fcde91e/product=fuxi/unlocked=yes及profile/镜像SHA核验后，
仅执行一次fastboot boot；Sending OKAY 0.058秒、Booting OKAY 1.740秒，退出0。
镜像 SHA256 6b7e00d7738d7372f6489fb1e39165b56db9034f1e63ad74a0598700d503432d，
绑定源码0586ebe8af5a0ee6fe26ea3271f94ab6cfac5201。等待CP flags、文件读取与
停队列照片；不把主机OKAY当作实机通过。原始记录见独立 attempt-02/device-test.json。

### 2026-10-27：M2 non-inline attempt-02 文件对照通过

IMG_7091 原图 SHA256 25cad71e505cf6b408fbec3b0a8e70a1b555d342fede260e571f1d4835e221bb。
原生UFS NOP/query/wake/TUR/capacity/GPT成功，metadata/userdata独立缓冲读取各3/3。
FUXIM2 prepare=Success，selected-pack=2、cp-flags=0x45、cp-version=0x5788ea84、
journal=0；root-open/root-list成功，13项含点项。
lookup=/aconfig/maps/system.package.map 成功，nid=12、bytes=10527、inline=0；
file-read=Success bytes=10527，SHA256 14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991，
与先前TWRP的文件大小及完整SHA一致。两次之间经历Android启动/关机，不能宣称
全卷状态一致；该文件的非inline完整读取与独立文件对照成立。
UFS stop=1/db=0/tdb=0/run=0/HCS=0x10f/IS=0、poisoned=0，最终pre-USB park；
SMMU configured=1/GFSR=0/FSR=0x400，未证明完整路由恢复。journal=0，非空
NAT journal无实机覆盖；Android解密、RAM对象、XNU交接仍未实现或验证。
原图/assessment/报告见 out/m2-noninline-file-v1/attempt-02/，Windows交付同名目录。

### 2026-10-27：M3 metadata 映射规划器主机阶段

开始M3。UEFI/TWRP源码提交 44ea501733d9cbcec1bdeac528efe5ff13e252d8：新增严格限界的metadata-only
dm-default-key 地址/DUN规划器及TWRP只读首块参考采集器。4KiB、AES-XTS、
wrappedkey_v0、iv_large_sectors及完整映射必须由调用方核实；当前无实际映射
提供者、无磁盘IO、密钥处理、SMC/ICE执行或解密。合成规划测试ASan/UBSan、
采集器4项模拟测试、两种AARCH64模块构建均通过；编译对象函数存在，
no-SMC/query DataProbe分别0/1条SMC，NativeUfs两者均0。无可启动镜像及新设备操作。
手机预计仍在M2诊断驻留；下一步需A17 TWRP解密后只读采集data映射首块SHA及
原始userdata首块SHA/魔数，仍不能据此推断实际dm表几何或UEFI解密成功。
详见 out/m3-metadata-plan-v1/REVIEW.md、docs/fuxi-m3-metadata-plan-20261027.md。
Windows入口 D:/Projects/UniXNU/migration/20261027-m3-dm-plan/。

### 2026-10-27：M3 TWRP 明文参考与脱敏 DM 几何

用户进入A17 TWRP并解密data；fcde91e/fuxi、/data=dm-7 F2FS rw，userdata
映射直接下层sda32(259:16)，/metadata=sda17 F2FS rw。只读4KiB首块：
映射data SHA256 d99375331359c5120ef2dc6a6019ff5ab3b0a55063f38facd7e35d3be305257b，
offset1024有F2FS魔数；原始userdata SHA256 e815d5f476ef20b73c152bf6640a7881d7912f9dceeaea95700f10d9904d3d5a，
该偏移无F2FS魔数。块内容未保存，卷未冻结。
sysfs映射与原分区大小469917144个512字节扇区，起点24887808，块大小4096；
与之前UEFI GPT起点/长度一致。tmpfs只读DM_TABLE_STATUS程序要求密钥字段为
脱敏'-'，只输出白名单字段：default-key aes-xts-plain64，target begin=0、
length=469917144、device start=0、IV offset=0、backing=259:16、sector=4096、
iv_large_sectors与wrappedkey_v0均为真。临时程序已清理，未保存/传出密钥或表原文。
源码提交 c1fd1efed371d2172ecbde24ceb47640bcf58c11，设备程序SHA256 110771ceec7ac746d9b763ebbc32562c1ef3d191d177b4b5bcc80104fb2b86ba；
ASan/UBSan主机测试和设备自检通过。实测几何首末块/DUN主机规划通过。
UEFI安全服务、密钥操作、ICE、明文对照均未验证，无新的固件boot/分区写入。
报告及JSON位于 out/m3-dm-evidence-v1/，Windows入口
D:/Projects/UniXNU/migration/20261027-m3-dm-plan/。

### 2026-10-27：M3 DXE 安全协议发现候选（待上机）

UEFI 实验分支在 M2 原生 UFS DXE 入口增加 SCM/QSEE LocateProtocol，只输出
EFI 状态及接口是否非空，不调用任何安全服务，不直接 SMC、不解密。M0 旧探针
未纳入此前 M2 镜像，因此这次使用实际 DXE 路径；NOT_FOUND 只适用于该时点。
候选 out/m3-protocol-discovery-v1，SHA256
6cb8015b5d52ecbc47dd2b2d9138b991ad5078d61554bf978803fb2b4bebb948。
12 项主机检查、两种模块构建和专用镜像 GUID/日志绑定检查通过；反汇编 UFS
模块 SMC=0，硬件结果 pending。实机仅 fastboot boot，照片采集 DXE 协议状态、
M1/M2 及停队列；非 clean checkpoint 不跳过保护。M3 解密与 TEE 可用性未证明。

### 2026-10-27：M3 DXE 协议发现 attempt-01 — 待照片

设备 fcde91e/fuxi/unlocked=yes，镜像 SHA 6cb8015b5d52ecbc47dd2b2d9138b991ad5078d61554bf978803fb2b4bebb948，
已核验与提交 e1ef51ae 的 51 个源码输入绑定；仅一次 fastboot boot，Sending 和
Booting 均 OKAY、退出 0。手机直接进 fastboot，不假定 metadata checkpoint clean。
仍需照片确认 SCM/QSEE 协议状态、UFS 读取及停队列。原始记录位于
uefi-twrp-data/out/m3-protocol-discovery-v1/attempt-01/，Windows 入口
D:/Projects/UniXNU/migration/20261027-m3-protocol-discovery-v1/attempt-01/。

### 2026-10-27：M3 DXE 协议发现 attempt-01 照片

IMG_7108 原图 SHA256 3cdf8d3a73e447ff8365d1bcee74362cc12ef598595225b9d59652610d67cc68。
SCM=Not Found nonnull=0、QSEE=Not Found nonnull=0；仅证明当前 DXE 时点未
发现 UEFI 协议，不能推断底层 TEE 不可用。UFS 基础命令/GPT 成功，两分区
独立读取各 3/3；metadata pack=2 flags=0x44，prepare=Unsupported，
read-bytes=0：因缺少 clean-unmount 位而遵循现有只读策略停止。UFS stop=1、
db=0、tdb=0、run=0、poisoned=0；SMMU GFSR=0/FSR=0x400，完整路由恢复
未证明。最终 PARK，未执行 SMC、KeyMint、ICE 或解密。实机照片及 assessment
位于 out/m3-protocol-discovery-v1/attempt-01/，Windows 对应同名交接目录。

### 2026-10-27：M3 SMC64 INFO 自查询候选（待上机）

在 DXE 协议未发现后，新增独立默认关闭的 SMC64 INFO 自查询探针；一次调用
x0=c2000601、x1=1、x2=02000601，既不执行 key 操作也不查询 ICE 功能。
UFS 候选 out/m3-scm-info-v1，镜像 SHA256
d23874d80d1ce8c1a60b69568a7b1252b4053e7e5af9c0a21846fd0979a544dd。
12 项交付检查通过，镜像 UFS EFI 1 条 SMC、两份对照 UFS EFI 0 条；
硬件 pending。同步 SMC 无软件硬超时，禁用 watchdog 的驻留候选可能停在
begin；上机需用户在场，拍 begin/return，可能需要手动重启。查询成功不证明
ICE/KeyMint/密钥/解密可用，不得把不返回推断成 unsupported。

### 2026-10-27：M3 SMC64 INFO attempt-01 — 等待屏幕结果

互斥、fcde91e/fuxi/unlocked、usb-ram profile、镜像 SHA
d23874d80d1ce8c1a60b69568a7b1252b4053e7e5af9c0a21846fd0979a544dd
及 51 个源码文件与提交 1a38a511 绑定后，只执行一次 fastboot boot。
Sending OKAY；Booting 阶段 usb_read failed，Windows 错误 31、退出 1。
设备是否进入诊断、是否执行 SMC 均未知；不自动重试，等待屏幕照片。
原始记录位于 uefi-twrp-data/out/m3-scm-info-v1/attempt-01/，Windows 入口
D:/Projects/UniXNU/migration/20261027-m3-scm-info-v1/attempt-01/。

### 2026-10-27：M3 SMC64 INFO attempt-02 — 等待照片

首次 Booting 因 Windows USB 读取错误 31 失败且设备状态未知；用户明确要求
再次启动，重新检查 fcde91e/fuxi/unlocked 与设备互斥后，仅再执行一次
fastboot boot。Sending OKAY、Booting OKAY、退出 0。未据此判定 SMC 返回；
等待屏幕照片。原始记录为 uefi-twrp-data/out/m3-scm-info-v1/attempt-02/
与 D:/Projects/UniXNU/migration/20261027-m3-scm-info-v1/attempt-02/。

### 2026-10-27：M3 SMC64 INFO attempt-02 照片

IMG_7111 SHA256 7a811c99d46feeaa04d5e611355f89c58ea4889b18523538cadd73934204bea5。
SCM64 INFO 自查询 return a0=0 a1=1 decode=Success available=1；SCM/QSEE
UEFI 协议仍 Not Found。UFS 基础命令/GPT 成功、两分区独立读取各 3/3；
metadata pack=2 flags=0x44，prepare=Unsupported、read-bytes=0，按策略停止。
UFS stop=1、db=tdb=0、run=0、poisoned=0；SMMU GFSR=0/FSR=0x400，完整
路由恢复未证明。PARK；ICE、KeyMint、密钥加载和解密未测试。照片与 assessment
位于 out/m3-scm-info-v1/attempt-02/ 和 Windows 对应交接目录。

### 2026-10-27：M3 ICE SCM 可用性查询候选（待上机）

基于 INFO 自查询实机成功，新候选只用 INFO 查询 ICE set/clear/derive
三个 service ID 的可用性；先自查询，异常停止，不执行这些服务或密钥操作。
候选 out/m3-scm-ice-avail-v1，镜像 SHA256
06911cdf369d5f87fd167a234b66800ac31d241db6e5f80677bf81dfb6c79361。
12 项主机检查通过；UFS EFI 一条 SMC 指令、最多调用四次，对照模块零条。
同步 SMC 无硬超时，可能停在 begin、需要手动重启；硬件结果 pending，
可用性不证明权限、密钥加载或解密。上机只允许一次 fastboot boot。

### 2026-10-27：M3 ICE 可用性 attempt-01 — 待照片

互斥、fcde91e/fuxi/unlocked、usb-ram profile、镜像 SHA
06911cdf369d5f87fd167a234b66800ac31d241db6e5f80677bf81dfb6c79361
及 51 个源码文件和提交 bec3bce1 绑定后仅一次 fastboot boot；Sending、
Booting 均 OKAY，退出 0。等待照片确认 INFO/subject 返回及 UFS 状态。
原始记录位于 uefi-twrp-data/out/m3-scm-ice-avail-v1/attempt-01/ 和
D:/Projects/UniXNU/migration/20261027-m3-scm-ice-avail-v1/attempt-01/。

### 2026-10-27：M3 ICE SCM 可用性 attempt-01 照片

IMG_7134 SHA256 a67a3c6d7d12bc8419c466f8e6f9790376748c8f7962b5591c869b1ea7f2ffe0。
INFO 自查询及 02001005/02001006/02001007 均 a0=0 a1=1 decode=Success
available=1；仅证明 INFO 报告可用，不证明服务执行、权限、key 或解密。
UFS 命令/GPT 和两分区独立读取各 3/3；metadata pack=1 flags=0x45、
cp-version=0x5788ea97、journal=0；M2 文件 10527 字节 SHA256
14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991
与既有 TWRP 基准一致。stop=1、db=tdb=0、run=0、poisoned=0；
SMMU GFSR=0/FSR=0x400，完整路由恢复未证明。PARK，无解密或 XNU handoff。

### 2026-10-27：M3 QSEE smcinvoke 主机静态审计

A17 ramdisk 的 smcinvoke_dlkm 注册 qseecom_proxy 的函数表并依赖 SCM 和
qtee_shmbridge；libqtikeymint 导入的传输函数由 libkeymasterdeviceutils
导出，后者导入 QSEECom。七个文件哈希、符号种类和注册调用的可复现审计
通过，修改模块的拒绝测试通过。只读静态结果，未执行设备代码/安全调用、
未读取密钥；匹配本机的 QSEE ABI 和 UEFI 访问权限仍未证明。见
uefi-twrp-data/docs/fuxi-m3-qsee-static-20261027.md 和 out/m3-qsee-static-v1/。

### 2026-10-27：M3 SM8550 smcinvoke 候选源码对照

固定 LineageOS SM8550 模块源码 9e8972287159837a256d0afcddc3f636f67ce8dd，
含 QSEE proxy 注册、client environment、app loader、invoke/callback 和共享
内存路径；关键符号与 TWRP smcinvoke 模块相符。七个固定源码文件哈希已保存；
仍未证明与本机模块同一次构建或 ABI 完全一致，未发起 TA/SMC 或访问密钥。

### 2026-10-27：M3 smcinvoke SCM 可用性候选（待上机）

公开 SM8550 源码与本机 SCM 模块静态对照显示 owner 0x32/service 0x06 的
legacy、callback、object invoke 编号 0/1/2；尚未证明源码和模块构建完全一致。
独立候选通过 INFO 查询 32000600/01/02 是否可用，不执行这些调用或 TA/密钥
操作；异常停止后续，最多四次同步 SMC。候选 out/m3-smcinvoke-avail-v1，
镜像 SHA256 bbe2159579a5a3d2a1bcd3908860b97ce2ae8f3cae9bb801892cd3f9f3916943。
12 项交付检查、两份对照模块和查询白名单 ASan/UBSan 检查通过。硬件 pending；
SMC 无硬超时，可停在 begin，需用户在场且可能手动重启。

### 2026-10-27：M3 smcinvoke 可用性 attempt-01 — 待照片

互斥、fcde91e/fuxi/unlocked、usb-ram profile、镜像 SHA
bbe2159579a5a3d2a1bcd3908860b97ce2ae8f3cae9bb801892cd3f9f3916943
及 51 个源码文件与提交 6a11bda3 绑定后仅一次 fastboot boot；Sending、
Booting 均 OKAY，退出 0。等待照片确认 INFO/subject 返回及 UFS 状态。
记录位于 uefi-twrp-data/out/m3-smcinvoke-avail-v1/attempt-01/ 和
D:/Projects/UniXNU/migration/20261027-m3-smcinvoke-avail-v1/attempt-01/。

### 2026-10-27：M3 smcinvoke 可用性 attempt-01 照片

IMG_7135 SHA256 0e64f2a6519501e1398b69fbcbac69607b33d684f98f3da6d710e179f068093d。
INFO 自查询及 32000600/32000601/32000602 均 a0=0 a1=1 decode=Success
available=1；仅证明 INFO 报告可用，不证明 invoke、共享内存、权限或解密。
UFS 命令/GPT 和两分区独立读取各 3/3；metadata pack=1 flags=0x45、
cp-version=0x5788ea97、journal=0；M2 文件 10527 字节 SHA256
14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991
与既有 TWRP 基准一致。stop=1、db=tdb=0、run=0、poisoned=0；
SMMU GFSR=0/FSR=0x400，完整路由恢复未证明。PARK，无解密或 XNU handoff。

### 2026-10-27：M3 smcinvoke 对象布局主机原型

基于固定 SM8550 候选源码，构造仅合成内存的 root register 和 app loader open
请求；设备模块反汇编局部核对 root=1、register op=5/counts=0x1100、
loader open op=0/counts=0x1001/UID=122 及 12 字节头和 8 字节 descriptor。
主机四项矢量/无效句柄测试常规及优化模式通过；未执行 SMC、访问手机或
读取密钥。响应/回调、共享内存和完整硬件 ABI/权限仍未验证。见
uefi-twrp-data/docs/fuxi-m3-smcinvoke-layout-20261027.md 与 out/m3-smcinvoke-layout-v1/。

### 2026-10-27：M3 UFS 加密请求头主机草案

仅生成合成 UTRD 读取头，基于通用 Android UFS 格式及现有 FnuFillUtrd；
端序、范围和错误输入的 4 项测试在常规与 -O 下通过。无设备 I/O、
无密钥、未接入固件。本机 crypto capability、slot、DUN、ICE/HWKM、
厂商 PRDT 与错误清理尚未验证，不能据此提交硬件请求或声称解密。
详见 uefi-twrp-data/docs/fuxi-m3-ufs-crypto-layout-20261027.md。

### 2026-10-27：M3 SMCI 响应与生命周期主机实验

候选驱动在原输入缓冲区读取对象返回，另一个缓冲区承载 callback/piggyback；
错误、未知终态与非空输出缓冲区隔离。固定哈希 TWRP 模块局部核对 release
0xffff/4096 和 legacy flush、invalidate 调用。5 项主机测试常规及 -O 通过；
无 SMC/手机/密钥操作，不实现回调、内存桥或真实安全释放，不能视为 M3 完成。
详见 uefi-twrp-data/docs/fuxi-m3-smcinvoke-response-20261027.md。

### 2026-10-27：M3 UFS crypto capability 只读候选（待上机）

UEFI 有界读取 CCAP/最多四项 CRYPTOCAP；不读取配置区、不执行 SMC 或
密钥操作。门禁及读范围模型经 ASan/UBSan 检查，12 项资格检查退出 0，
交付 EFI 零条 SMC。候选 uefi-twrp-data/out/m3-crypto-cap-v1，镜像 SHA256
4e183affa8537ec24fa376bcd14d5e12c8320a2f64fada3aff5825ffd890a9f1。保持 M2 和 USB 前 PARK；
硬件 pending，不能推断槽位可用、QTEE 权限或解密。

### 2026-10-27：M3 UFS crypto capability attempt-01 — 待照片

互斥、fcde91e/fuxi/unlocked、usb-ram profile、镜像 SHA
4e183affa8537ec24fa376bcd14d5e12c8320a2f64fada3aff5825ffd890a9f1
和提交 36377ba5 的 51 个源码文件绑定检查通过；只执行一次 fastboot boot，
Sending 和 Booting 均 OKAY、退出 0。待照片确认 capability、UFS 停队列；
未确认 checkpoint clean。原始记录位于
uefi-twrp-data/out/m3-crypto-cap-v1/attempt-01 与
D:/Projects/UniXNU/migration/20261027-m3-crypto-cap-v1/attempt-01。

### 2026-10-27：M3 crypto capability attempt-01 照片

IMG_7137 SHA256 81ccee211dc2d8f1f93f566f37893c59b000f5cf23c220bca6a1e873b2cb0f6e。CCAP=80003F06 advertised=6 read=0，
Compromised Data 是探针自身错误拒绝配置基址 0x8000 超出映射窗口，不是
控制器返回错误；未读配置区。M2 pack=2 flags=0x145 文件 10527 字节、
SHA256 14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991；
UFS stop=1 db=tdb=run=0 poisoned=0；SMMU GFSR=0 FSR=0x400，完整路由
恢复未证明。PARK，无解密。后续修正边界并重新验证。

### 2026-10-27：M3 UFS crypto capability v2（待上机）

修复 v1 将配置区基址 0x8000 位于 UFS 窗口外误判为数据损坏；仍拒绝能力
区与配置区重叠，最多读取八项，不读配置、不调用 SMC。真实 CCAP 回归和
12 项交付检查通过；候选 SHA256 e71fc149d6088a6e24bc25dadd84e2eb7656e48ed22153237f488ae1f4a32964，
硬件 pending，参考 uefi-twrp-data/out/m3-crypto-cap-v2/REVIEW.md。

### 2026-10-27：M3 UFS crypto capability v2 attempt-01 — 待照片

互斥、fcde91e/fuxi/unlocked、usb-ram profile、镜像 SHA
e71fc149d6088a6e24bc25dadd84e2eb7656e48ed22153237f488ae1f4a32964
及提交 d85d675f 的 51 个源码文件绑定核验通过。只执行一次 fastboot boot，
Sending、Booting 均 OKAY，退出 0；待照片确认能力条目和 UFS 停队列。
原始记录位于 uefi-twrp-data/out/m3-crypto-cap-v2/attempt-01 与
D:/Projects/UniXNU/migration/20261027-m3-crypto-cap-v2/attempt-01。

### 2026-10-27：M3 UFS crypto capability v2 照片

照片 SHA256 88335d0ef0b66c8e8ffbd8ddb2033d4dcaa195c20809850b389f11286491d044。crypto-cap Success，CCAP=80003F06，6/6 项：
0001FF00、0001FF01、0001FF02、0003FF00、0003FF01、0003FF02。
按标准字段解读：第 3 项为 AES-XTS、key-size code 3、SDUS mask FF（含
4096 字节）；CFGC=3f 对应 64 个报告配置，不证明可用槽位或钥匙。
M2 pack=2 flags=145 cp-version=5788EAA2，文件 10527 字节 SHA256
14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991；
UFS stop=1 db=tdb=run=0 poisoned=0；SMMU GFSR=0 FSR=400，完整路由
恢复未证明。PARK，无密钥装载或解密。

### 2026-10-27：M3 共享内存桥 INFO 查询候选（待上机）

TWRP qcom-scm.ko 设备静态封装对照 service 0c、command 1c/1d/1e。
新候选仅 INFO 查询 02000c1c/1e/1d，不执行桥操作、装钥或解密；
保留 UFS 能力探针与 M2。白名单 ASan/UBSan 和 12 项资格检查通过，
交付 EFI 一条 SMC；镜像 SHA256 c19c942743a6ac49f60fbd3aed35feb0ca72f95ec26acb2f691d1948aa869311。
硬件 pending，SMC 无硬超时；available 不证明桥建立及访问权限。

### 2026-10-27：M3 桥接查询及 UFS 能力选择候选 v2（待上机）

增加依据实测能力选择 AES-256-XTS/4096 条目，实测向量主机模型选择 index 3；
固定哈希 UFS/SCM/HWKM 模块静态审计与篡改拒绝通过。候选 INFO 查询
共享内存桥 1c/1e/1d，可用性不证明权限；不装钥、不解密。12 项交付检查
通过；镜像 SHA256 52a79ea3af33f926b7cf4125c72d3696d2fc275a565079361ef9e5219e4b4586，硬件 pending。

### 2026-10-27：M3 bridge availability v2 attempt-01 — 待照片

互斥、fcde91e/fuxi/unlocked、usb-ram profile、镜像 SHA
52a79ea3af33f926b7cf4125c72d3696d2fc275a565079361ef9e5219e4b4586
与提交 9c03b551 的 51 个源码文件绑定检查通过；只执行一次 fastboot boot，
Sending、Booting 均 OKAY，退出 0。待照片确认 INFO 查询、能力选择与 UFS 停机。
原始记录位于 uefi-twrp-data/out/m3-bridge-avail-v2/attempt-01 和
D:/Projects/UniXNU/migration/20261027-m3-bridge-avail-v2/attempt-01。

### 2026-10-27：M3 bridge availability v2 attempt-01 照片

照片 SHA256 37ac257dff2a583bd4b0e3a07f216db31c7a7327dcf54be5bef53921c04862be。INFO 自查询和 02000c1c/1e/1d 均为
a0=0 a1=1 decode=Success available=1；只证明 INFO 报告可用，未建立桥、
不证明共享内存权限、QTEE 会话或解密。crypto-cap status=Success
CCAP=80003f06 advertised=6 read=6：0001FF00/0001FF01/0001FF02/
0003FF00/0003FF01/0003FF02；crypto-xts4096 select=Success index=3。
M2 pack=2 cp-flags=00000145 feature=00000499 cp-version=5788EAAC，文件
10527 字节 SHA256 14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991。
UFS stop=1 db=tdb=run=0 poisoned=0 smmu-configured=1；SMMU GFSR=0
FSR=400 FAR=1BBBE7FD98DF，完整路由恢复未证明；PARK，无密钥装载、
桥操作或解密。镜像 52a79ea3af33f926b7cf4125c72d3696d2fc275a565079361ef9e5219e4b4586（提交 9c03b55140d9b7808ef77a1a9872067b41674ad4 的 51 个源码文件绑定）。

### 2026-10-27：M3 metadata key 目录只读候选（待上机）

只读打开 /vold、/vold/metadata_encryption、/vold/metadata_encryption/key：
/key 为目录时最多列出 6 个条目，对最多 3 个普通文件（单文件 8192 字节上限）
计算 SHA256 并打印不超过 16 字节的前缀；不装钥、不调用 SMC/TA、不解密。
生产 F2FS 读取器在合成嵌套镜像上经 ASan/UBSan 验证；13 项交付检查通过，
交付与两种对照模块 `smc #` 均为 0。镜像 SHA256 04467d7df7a820f885c0abd2c61288ab379d3bb525131a8891c8da3c8a975f67。
硬件 pending：实际布局、blob 大小/哈希与 checkpoint 状态均未取得。

### 2026-10-27：M3 QTEE 共享内存桥闭环调用候选（待上机）

按固定哈希 qcom-scm.ko 反汇编实现标量 SMC64（x0=0xC2000000|svc<<8|cmd、
x1=arginfo）与桥 owner 2 的 enable 0x1c / create 0x1e / delete 0x1d。
候选分配一页 4 KiB，先做已实机通过的 INFO 自查询；仅当 available=1 时
enable→create(vm_count=0)→取 a1 为句柄→delete，delete 尝试后才释放页面。
create 的 arg1=phys|6、arg3 VMID、条件 0x1a 均为静态推断；同步 SMC 无硬超时。
主机 ASan/UBSan 与 13 项交付检查通过；交付 EFI 1 条 `smc #`，两种对照模块 0；
镜像 SHA256 e711317c585a20cf66bd97ec119972bacafe4db54a823297b7c1342115f0dd28。
硬件 pending；闭环成功也不证明 TA 会话、共享内存权限或解密。

### 2026-10-27：M3 metadata key attempt-01 — 待照片

互斥、fcde91e/fuxi/unlocked、usb-ram profile、镜像 SHA
04467d7df7a820f885c0abd2c61288ab379d3bb525131a8891c8da3c8a975f67 与提交 0a454145 的 52 个源码文件绑定检查通过；只执行一次
fastboot boot，Sending、Booting 均 OKAY，退出 0。待照片确认 metadata key
目录读取结果。原始记录位于 uefi-twrp-data/out/m3-metadata-key-v1/attempt-01
与 D:/Projects/UniXNU/migration/20261027-m3-metadata-key-v1/attempt-01。

### 2026-10-27：M3 metadata key attempt-01 照片

照片 SHA256 2632f8dfcc83eafe9a94cab441445b89384df516f51e0e1f07893c4c766314b6。`prepare=Success pack=2 feature=00000499`。
`/vold` 2 项；`/vold/metadata_encryption` 1 项 `key`（nid 68，目录）；
`key` 目录 4 项：

- `version` nid 69，1 字节 inline，SHA256 `6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b`（内容 `1`）
- `secdiscardable` nid 70，16384 字节，超过 8192 上限，按策略只报大小
- `keymaster_key_blob` nid 71，208 字节 inline，SHA256 `9a57f01af0b8c9836cdf04f9abc2f83ad21baf94527b2c80b18b06f04ab2321e`
- `encrypted_key` nid 72，276 字节 inline，SHA256 `262a72de710d1f0f6b9b902dba7e16096759ace3a27cb2c38e3a0c6860b4684c`

`hashed=3 result=Success`。UFS `stop=1 db=tdb=run=0 poisoned=0 smmu-configured=1`；
SMMU `GFSR=0 FSR=400 FAR=1BBBE7FD98DF`；PARK，无密钥使用、无 SMC、无解密。
交付候选的目录名打印有缺陷：列表复用缓冲区未补 `\0`，把上一条名字的尾部粘在
`key` 后（显示为 `keydata_encryption`）；`.../key` 查找本身返回 Success mode=4000，
且 nid/类型/后续 4 项均一致，故条目名按 `key` 记录。源码已补终止符，镜像未重建。
镜像 04467d7df7a820f885c0abd2c61288ab379d3bb525131a8891c8da3c8a975f67（提交 0a45414530d03676a53915ecd613ae6cd306dd54 的 52 个源码文件绑定）。

### 2026-10-27：M3 bridge call attempt-01 — 待照片

互斥、fcde91e/fuxi/unlocked、usb-ram profile、镜像 SHA
e711317c585a20cf66bd97ec119972bacafe4db54a823297b7c1342115f0dd28 与提交 0a454145 的 52 个源码文件绑定检查通过；只执行一次
fastboot boot，Sending、Booting 均 OKAY，退出 0。待照片确认桥
enable/create/delete 的返回码与句柄。同步 SMC 无硬超时：任何一步停在
begin 行都表示该次调用未返回，需保留画面后手动重启。
原始记录位于 uefi-twrp-data/out/m3-bridge-call-v1/attempt-01 与
D:/Projects/UniXNU/migration/20261027-m3-bridge-call-v1/attempt-01。

### 2026-10-27：M3 bridge call attempt-01 照片

照片 SHA256 d4767b2bc5aebb297865a80e25d56970f5f464eac3f7b5b87ccccb82aaea956c。页 `phys=C6A41000 bytes=1000`。

- `enable` x0=`C2000C1C` x1=0 → a0..a3 全 0
- `create` x0=`C2000C1E` arg0=`C6A41000` arg1=`C6A41006` arg2=`1000` arg3=0
  → a0=0、a1=`5C`（句柄）、a2=a3=0
- `delete` handle=`5C` → a0=0、a1=`59`、a2=a3=0
- `FUXIM3 BRIDGE result=Success handle=5C`

即：设备接受 UEFI 引导服务页作为桥内存，三步 INFO 之外的共享内存桥操作均返回
成功。这是桥注册闭环，不是 TA 会话：句柄 `5C` 只表示 QTEE 登记了这页，未做任何
数据传输，也未证明 TA 可访问该内存或存在 KeyMint 会话。delete 的 a1=`59` 含义未知。

UFS `stop=1 db=tdb=run=0 poisoned=0 smmu-configured=1`；SMMU `GFSR=0 FSR=400
FAR=1BBBE7FD98DF`；M2 仍 `file-sha256=14f3d867…0991`；PARK，无密钥、无 TA、无解密。
镜像 e711317c585a20cf66bd97ec119972bacafe4db54a823297b7c1342115f0dd28（提交 0a45414530d03676a53915ecd613ae6cd306dd54 的 52 个源码文件绑定）。

### 2026-10-28：M3 C1 bridge blob 候选（待实机）

恢复 2026-10-27 会话后新增 UEFI DXE C1：8 KiB 桥 create 成功后，用只读 F2FS
读取 version=1 和 208 字节 keymaster_key_blob，绑定昨日 SHA256 9a57f01a…2321e；
缓存同步后页内哈希一致才打印前 32 字节，擦净后 delete。create 未知完成或 delete
失败保留页，不重试 SMC；无 TA、装钥或解密。ASan/UBSan 覆盖 inline/块文件及
分配/读取/哈希/缓存/桥调用错误路径，14 项交付检查退出 0。镜像 SHA256
20a31268b23ce12417be75593b5fff6116edb0bf4a7d2afac94f846339fdce15；UEFI 提交 4c1915205660ad340334d8466210799275737fae 的 54 个源码文件绑定通过。
候选位于 uefi-twrp-data/out/m3-bridge-blob-v1；本机实测尚待照片。

同时完成 C2 KeyMint 固定输入静态映射：convert cmd=0x311d，QCBOR label=23，
优先 keymaster64、回退 keymaster，用户态 QSEECom buffer=0xa000。
更正：keymint_set_version 发送 cmd=0x3121 的固定整数 200，不能当作 Android
release 17；configure 的参数序列化是另一路 cmd=0x2116。无 C2 invoke 实机结果。

### 2026-10-28： C1 bridge blob attempt-01 — 待照片

互斥检查未发现其他设备任务；fcde91e/fuxi/unlocked、usb-ram profile、
14 项交付检查、镜像 SHA256 20a31268b23ce12417be75593b5fff6116edb0bf4a7d2afac94f846339fdce15 与
提交 4c1915205660ad340334d8466210799275737fae 的 54 个源码文件绑定核验通过。
仅执行一次 fastboot boot，Sending/Booting 均 OKAY，命令退出 0；无 flash、擦除或 slot 更改。
命令完成 UTC：2026-10-28T04:35:11.476907+00:00。
原始 device-test.json 与辅助工具归档到 uefi-twrp-data/out/m3-bridge-blob-v1/attempt-01
及 D:/Projects/UniXNU/migration/20261028-m3-bridge-blob-v1/attempt-01。
等待照片核对 bridge create、页内 SHA/prefix32、blob result/delete/released、
UFS stop/db/tdb/run/poisoned、SMMU 和 PARK。当前不宣称 C1 实机通过或 TA/解密成功。

### 2026-10-28：C1 bridge blob attempt-01 照片

照片 SHA256 `b5c268199736a8ce9fc13ae001c5c0dd2c05f785a02d2a6e06f3d90487f31f8e`。C1 实机读取/桥清理闭环通过：
页 `phys=C6A3C000 bytes=2000`（8 KiB）；enable a0..a3=0；
create args=`C6A3C000/C6A3C006/2000/0`，a0=0、a1=5C、a2=a3=0。
`BLOB prepare=Success pack=1 feature=00000499`，文件 `bytes=208 mode=8000 inline=1`。
页内 SHA256 `9a57f01af0b8c9836cdf04f9abc2f83ad21baf94527b2c80b18b06f04ab2321e`，
`bytes=208 match=1 read=Success`。32 字节前缀已打印，开头确认为 `851a4b4d4b44`；
完整前缀保留在原图，避免长零串转录歧义，本条不猜写全部字节或封装语义。
delete handle=5C → a0=0、a1=59、a2=a3=0；`result=Success delete=Success`、
`released bytes=2000`。删除前的清零与缓存同步由交付源码顺序保证，照片不独立证明擦除效果。
UFS `stop=1 db=tdb=run=0 hcs=0000010F is=0 poisoned=0 ready=0`，stage=shutdown，PARK。
SMMU `GFSR=0 FSR=400` 残留，sid60 matches=1、slot=104、bank=68、SCTLR=0；
不宣称完整路由恢复。M2 10527 字节 SHA256 14f3d867…0991 仍一致。
镜像 `20a31268b23ce12417be75593b5fff6116edb0bf4a7d2afac94f846339fdce15`（提交 4c191520 的 54 个源码文件绑定）；14 项主机检查通过。
未执行 TA、装钥或解密。此结果只证明真实 wrapped blob 可读入注册页并完成桥删除，
不证明安全世界/TA 已读取此页。用户已重新进入 fastboot，并授权完成核对后进入 C2。

### 2026-10-28：M3 C2 root register/release 候选（待实机）

用户在 C1 照片后明确授权进入 C2。新增默认关闭 UEFI 最小对象调用：
INFO invoke availability 与 UFS 停队列后，用 8 KiB 桥运行 root register(null input,
op5/counts1100) 和远端 release(opffff/counts0)，终态/消息完整性校验后才 delete/free。
本机 qcom-scm.ko owner=32/arginfo224/type2 推出 standard SMC64 `72000600`。
未知/中断/回调/非零对象状态或损坏缓冲均保留资源驻留，无重试、无 TA、无 blob/装钥/解密。
27 个 ASan/UBSan 生命周期向量、C1 回归、固定模块审计和篡改拒绝通过；14 项交付检查退出 0。
镜像 SHA256 91775bf92a86b949488a157521304b3bb1a002cb2bf9e1195a4f612060a5dbe3；提交 041f3d1258844acebb7ff3dc7dc92627be6cd5af 的 55 个交付源码文件绑定通过。
候选位于 uefi-twrp-data/out/m3-smci-root-v1，实机待照片。

### 2026-10-28：C2 root register attempt-01 — 待照片

用户授权 C2 后，互斥、fcde91e/fuxi/unlocked、usb-ram profile、14 项交付检查、
27 个生命周期向量与固定模块审计，以及提交 041f3d1258844acebb7ff3dc7dc92627be6cd5af
的 55 个源码文件绑定核验通过。镜像 SHA256 `91775bf92a86b949488a157521304b3bb1a002cb2bf9e1195a4f612060a5dbe3`。
只执行一次 fastboot boot：Sending/Booting 均 OKAY，退出 0；无 flash、擦除或 slot 写入。
命令完成 UTC：2026-10-28T04:47:00.987854+00:00。原始记录位于
uefi-twrp-data/out/m3-smci-root-v1/attempt-01 与
D:/Projects/UniXNU/migration/20261028-m3-smci-root-v1/attempt-01。
待照片确认 INFO/SMCI bridge-create、register a0..a3/消息头/返回对象、release、
bridge-delete/released 或 retained、UFS/SMMU/PARK；目前不宣称 root invoke、TA 或解密成功。

### 2026-10-28：C2 root register attempt-01 照片 — 失败

照片 SHA256 `e67f0676e695c34315f36457bcb2f2af4fbff9d7a34dc47a10331322b0e6bdfe`。C2 root register **未通过**：
8 KiB 页 `phys=C697D000`，bridge enable a0=a1=0，create a0=0/a1=5C。
register `x0=72000600 x1=224 in=C697D000 out=C697E000 handle=1 op=5 counts=1100`
返回 `a0=FFFFFFFF80000001 a1=a2=a3=0`；可见头 `1/5/1100`、OI=0/0、OO=0/0，
输出页前四个 word 为 0。未取得对象句柄；非零 a0 时不能把其余寄存器的零解释为对象调用成功。
`retained bridge=5C object=0`、`result=Not Ready retained=1`；按失败分支未发 release/delete、
未 free 页、无自动重试。无 TA、key 或解密。
UFS `stop=1 db=tdb=run=0 hcs=0000010F is=0 poisoned=0 ready=0`，
最终 stage=m3-smci-root；SMMU `GFSR=0 FSR=400` 残留，完整恢复未证明。
报告 `INCOMPLETE dropped=1 truncated=0`，cached end 可见；照片无 PARK 行，
诊断驻留仅按代码路径推断，不写成照片已确认的 PARK/HALTED。
14 项主机检查及镜像/提交绑定仍成立：镜像 `91775bf92a86b949488a157521304b3bb1a002cb2bf9e1195a4f612060a5dbe3`，提交 041f3d12 的 55 个源码文件。
SMC 包装仅保存 raw 寄存器；当前没有准确错误码映射，不能归因权限、内存或 KeyMint。
下一步核对 legacy/object 入口选择和运行态调用约定；报告容量另需修复，避免关键尾行丢失。

### 2026-10-28：C2 object 命令 2 对照候选（待实机）

v1 的 FFFFFFFF80000001 仍未准确归类；核对公开固定参考驱动的
qcom,support-legacy_smc 选择逻辑及 kalama.dtsi 节点无该属性后，新增显式 object
模式 `72000602/224`，不在运行中 fallback/retry。未取得本机部署 DT，仍属对照实验。
沿用 root/null/register→release→bridge delete 与未知态保留；无 TA/blob/装钥/解密。
修复报告容量 96→128 行，新增实测 raw 错误回归。双模式 56 个 ASan/UBSan 向量、
C1 回归、固定模块/篡改审计通过；14 项交付检查退出 0。
镜像 SHA256 6d119c131fafaee0d30530c70f471ead075e85aca7589ed66899a7a105b07eeb；提交 69ba2ae922e32411957cb2730682345770878e41 的 55 个源码文件绑定通过。
候选位于 uefi-twrp-data/out/m3-smci-root-object-v2，实机待照片。

### 2026-10-28：C2 object root register attempt-01 — 待照片

用户确认重新进入 fastboot，命令 2 object 模式 72000602/224、互斥、fcde91e/fuxi/unlocked、usb-ram profile、14 项交付检查、
双模式 56 个生命周期向量与固定模块审计，以及提交 69ba2ae922e32411957cb2730682345770878e41
的 55 个源码文件绑定核验通过。镜像 SHA256 `6d119c131fafaee0d30530c70f471ead075e85aca7589ed66899a7a105b07eeb`。
只执行一次 fastboot boot：Sending/Booting 均 OKAY，退出 0；无 flash、擦除或 slot 写入。
命令完成 UTC：2026-10-28T04:57:39.863705+00:00。原始记录位于
uefi-twrp-data/out/m3-smci-root-object-v2/attempt-01 与
D:/Projects/UniXNU/migration/20261028-m3-smci-root-object-v2/attempt-01。
待照片确认 INFO/SMCI bridge-create、register a0..a3/消息头/返回对象、release、
bridge-delete/released 或 retained、UFS/SMMU/PARK；目前不宣称 root invoke、TA 或解密成功。

### 2026-10-28：C2 object root attempt-01 照片 — 同样失败

照片 SHA256 `52c1fd083a4504eeb939df29abeb88cf913c703066564c21eda1ee73b8d4ffbe`。object 命令 2 的 root register **仍未通过**：
`mode=object-cmd2`；8 KiB 页 `C697D000`；bridge enable a0=a1=0，create a0=0/handle=5C。
register `x0=72000602 x1=224 in=C697D000 out=C697E000 handle=1 op=5 counts=1100`
返回 `a0=FFFFFFFF80000001 a1=a2=a3=0`，可见 hdr=1/5/1100、OI=0/0、OO=0/0、out 前四 word=0。
`retained bridge=5C object=0`、`result=Not Ready retained=1`；未执行 release/delete/free，
无自动 fallback/retry，无 TA、密钥或解密。与 v1 命令 0 同样的 raw 返回，故仅换入口未解决。
UFS `stop=1 db=tdb=run=0 hcs=0000010F poisoned=0 ready=0`；SMMU GFSR=0/FSR=400
仍残留，不证明完整恢复。照片可见 PARK 与 cached report end，未见 INCOMPLETE 提示；
128 行报告容量覆盖了本次尾部，不将此推成所有分支都不会溢出。
镜像 `6d119c131fafaee0d30530c70f471ead075e85aca7589ed66899a7a105b07eeb`，提交 69ba2ae9 的 55 个交付源码文件绑定及 14 项检查有效。
raw 错误仍未准确归类，不能归因权限/内存/KeyMint；下一步核对共同调用上下文
（异常级、中断屏蔽、standard SMC 路径）与桥内存契约，保持未完成状态而不宣称 C2 通过。

### 2026-10-28：SMC context INFO-only 诊断交付（未上机）

按两次 C2 register 同样失败的证据，新增默认关闭 CTX 控制：只读 CurrentEL/DAIF，
在已有 fast INFO gate 成功后单次 standard INFO 自查询 42000601/1/02000601。
编译排除桥/对象执行，无缓冲指针、密钥或 TA；不改异常级/中断状态、不重试。
fast 与 standard 查询处于不同阶段，standard INFO 自身支持未知，不据结果泛化所有调用。
5 个 CTX 生产主机向量、既有 56 项 root 回归及 15 项完整交付检查通过；
镜像 SHA256 409a877bd1df680ac4827c3e7dd0edd8e96ba2c948d3e2c0d7b84f99335f7a7b；提交 d179b77ff7136eefd93a77ec6dcfac0b96f9d80d 的 56 个交付源码文件绑定通过。
目录 uefi-twrp-data/out/m3-smc-context-v1。本候选尚未上机；C2 仍未通过。
用户要求“完成后提交”，已按本诊断阶段完成范围提交，未将诊断交付写成 C2 修复。

### 2026-10-28：SMC context attempt-01 — INFO 控制成功，C2 未通过

用户确认重新进入 fastboot 后，互斥、fcde91e/fuxi/unlocked、usb-ram profile、15 项交付检查及
提交 `d179b77ff7136eefd93a77ec6dcfac0b96f9d80d` 的 56 个交付源码文件绑定核验通过。镜像 SHA256 `409a877bd1df680ac4827c3e7dd0edd8e96ba2c948d3e2c0d7b84f99335f7a7b`。
独立 attempt-01 仅执行一次 fastboot boot，Sending/Booting 均 OKAY，退出 0；无 flash/擦除/slot 写入。
完成时间（Asia/Shanghai）：2026-10-28T14:19:39.562847+08:00。原始命令与设备查询记录保留在 attempt-01/device-test.json。

照片 IMG_7169..JPG，SHA256 `6d09a3a33e20a0df38610b84f3001cc05f6e6f114138092ac39124bbe5953c6e`。可见：
`CTX CurrentEL=4 DAIF=200 fast-info=1`；standard INFO `x0=42000601 x1=1 subject=2000601`，
返回 `a0=0 a1=1 a2=0 a3=0`，`decode=Success available=1`；CTX END、PARK 与 cached report end 均可见。
本次只证明 UFS 停队列后的该 standard 标量 INFO 控制返回成功，排除了“此环境中所有 standard SMC 都失败”
的笼统判断；不证明 owner32 对象调用、共享页访问或此前两次运行的上下文一致。C2 仍未通过。
按交付代码与照片 END，无桥/对象/TA/密钥/解密/重试。
UFS `stop=1 db=tdb=run=0 hcs=0000010F is=0 poisoned=0 ready=0 stage=m3-smc-context`。
SMMU GFSR=0、FSR=400 仍残留，不能宣称完整恢复。M2 文件 10527 字节及 SHA256 14f3d867…0991 一致。
本次 CP version=5788EAC1、userdata 首块 SHA256=7ac3edafc83d6b29583e726f58f6e6f640d5b8cddf77477f491f68bed56233ed，
与之前候选照片不同，记录为跨启动观测变化；不据此推断变化来源，也不写成该候选写入。
后续优先静态核对 owner32 调用封装、共享内存和固件契约；暂不由 INFO 成功直接重复 root 或加载 TA。

### 2026-10-28：标准桥ABI纠正候选（待实机）

固定 qcom-scm.ko（8932f020…79a5）确认桥type=0，入口42000c1c/1e/1d；
create a0=SCM状态、a1=服务结果、a2=64位句柄。
更正旧fast调用和a1句柄解释：a0=0/a1=5C/a2=0不能证明标准桥handle5C已注册；
历史记录保留并追加本更正，文件读取/hash仍是独立观测，C2失败根因仍未确定。
本候选UEFI DXE/工具层仅测UFS/M2停队列后的8KiB标准桥闭环，无对象/blob/TA/key/解密。
固定模块审计及篡改拒绝、12个桥生命周期、C2 56向量、C1与INFO回归通过；
15项完整检查退出0。镜像SHA256 59ef12959f32ef2869f328eaf258abeeca5c6f2b1f2880c56303756f701d2ad8；提交 33e50f3aefb23346e407a25302efab7c44dfe3cc，56个交付源码绑定。
目录uefi-twrp-data/out/m3-bridge-standard-v1。VM/DT/pool全契约仍未复现，实机pending，C2仍未通过。

### 2026-10-28：标准桥attempt-01临时启动（待照片）

用户确认已进入fastboot后，互斥、fcde91e/fuxi/unlocked、usb-ram profile、15项交付检查、
标准桥固定模块审计和12个生命周期向量、提交33e50f3aefb23346e407a25302efab7c44dfe3cc的56个源码文件绑定重新核验通过。
镜像SHA256 59ef12959f32ef2869f328eaf258abeeca5c6f2b1f2880c56303756f701d2ad8。独立attempt-01只执行一次fastboot boot，
Sending/Booting均OKAY，退出0；无flash/擦除/slot写入。完成UTC：2026-10-28T06:41:15.548879+00:00。
原始记录保留于uefi-twrp-data/out/m3-bridge-standard-v1/attempt-01及
D:/Projects/UniXNU/migration/20261028-m3-bridge-standard-v1/attempt-01。
待照片核对ABI=standard64、enable/create/delete的a0..a3、64位句柄、released/retained、UFS/SMMU及PARK。
主机启动命令成功不代表桥生命周期成功；C2仍未通过，本次没有执行对象调用。

### 2026-10-28：标准桥attempt-01照片 — 创建未通过

照片IMG_7170..JPG，SHA256 8563c25f02596aed3fc1ed2752d409c406b082dde2797bafd01bb6e8cd61ff8a。标准桥生命周期未通过：
ABI=standard64；页phys=C6A40000、bytes=2000。
enable x0=42000C1C/x1=0，返回a0=a1=a2=a3=0。
create x0=42000C1E，参数C6A40000/C6A40006/2000/0，返回a0=0/a1=5C/a2=0/a3=0。
按本候选采用的固定模块ABI，没有可接受的零服务结果及非零a2句柄，decode=Not Ready。
retained handle=0 bytes=2000；照片可见无delete，按生产失败路径未擦写/释放页，无自动重试。
与旧fast桥照片同样raw create返回，故改变call-type未解决创建接受问题。
不能把5C猜作具体权限/地址/VM错误，也不能排除部署固件ABI与模块参考不同。
UFS stop=1、db=tdb=run=0、hcs=10F、is=0、poisoned=0、ready=0，stage=m3-bridge-standard。
SMMU GFSR=0、FSR=400残留，完整恢复未证明；PARK和cached report end可见。
未调用root/对象、TA、密钥或解密。15项检查、镜像59ef12959f32ef2869f328eaf258abeeca5c6f2b1f2880c56303756f701d2ad8及提交33e50f3aefb23346e407a25302efab7c44dfe3cc的56个源码绑定仍有效。
本次把未验收点定位到对象调用之前的桥创建契约；准确5C含义及VM/DT/permission/pool要求仍待静态核对。
C2仍未通过，不继续猜句柄或进入对象调用。

### 2026-10-28：显式HLOS桥对照候选（待实机）

固定模块probe/register和仓库DTB支持新增显式HLOS桥对照：一个VM、VMID3、NS/TZ权限6，
四参phys|6/phys|6/size|200/3；8KiB时size字=2200，hyp-flags=0。
DTB不是部署态证明；完整Linux DMA/pool/VM契约仍未复现，5C未准确归类。
本机probe使用普通页、DMA映射和gen_pool，追加更正早期“必须DT carveout”的说法。
UEFI DXE/工具层默认关闭开关，限定桥诊断，无对象/TA/blob/key/解密/分区写入。
24个双配置生产桥生命周期、固定模块/DTB审计及篡改拒绝、C2 56向量、C1与INFO回归通过；
15项交付检查退出0。镜像SHA256 5818cd8079bca6f3096b2f061fc4b1351b777b22699774bdddb8563629239c6c，源码提交29d85a3894a48659a0cec4393330d1c89b5c474f的56个交付源码绑定。
目录uefi-twrp-data/out/m3-bridge-hlos-v1。用户已重新进入fastboot，实机pending，C2仍未通过。

### 2026-10-28：HLOS桥attempt-01临时启动（待照片）

用户确认已进入fastboot后，互斥、fcde91e/fuxi/unlocked、usb-ram profile、15项交付检查、
HLOS固定模块/DTB审计和24个双配置生命周期向量、提交29d85a3894a48659a0cec4393330d1c89b5c474f的56个源码文件绑定重新核验通过。
镜像SHA256 5818cd8079bca6f3096b2f061fc4b1351b777b22699774bdddb8563629239c6c。独立attempt-01只执行一次fastboot boot，
Sending/Booting均OKAY，退出0；无flash/擦除/slot写入。完成UTC：2026-10-28T06:55:52.888196+00:00。
原始记录保留于uefi-twrp-data/out/m3-bridge-hlos-v1/attempt-01及
D:/Projects/UniXNU/migration/20261028-m3-bridge-hlos-v1/attempt-01。
待照片核对profile=hlos、参数phys|6/phys|6/2200/3、enable/create/delete的a0..a3、64位句柄、released/retained、UFS/SMMU及PARK。
主机启动命令成功不代表桥生命周期成功；C2仍未通过，本次没有执行对象调用。

### 2026-10-28：HLOS桥attempt-01照片 — 创建删除释放通过

照片IMG_7171..JPG，SHA256 f1898e745c651b22ff7705a964eb59bc711bc54ee18a25c32863f11f00a3e288。显式HLOS桥创建/删除/释放闭环通过：
profile=hlos，vm-count=1、vmid=3、ns-perm=6、tz-perm=6、hyp-flags=0。
8KiB页phys=C6A40000；enable x0=42000C1C返回a0=a1=a2=a3=0。
create x0=42000C1E，参数C6A40006/C6A40006/2200/3，返回a0=0/a1=0/a2=33A0E7BD/a3=0。
delete使用完整句柄33A0E7BD，返回a0=a1=a2=a3=0；result=Success、released bytes=2000。
PARK与cached report end均可见。页清零/cache操作由源码顺序保证，照片不独立证明擦除效果。
对比零VM候选的a1=5C/a2=0，本次HLOS配置给出成功服务结果和a2句柄；
这证明该配置本次被接受，不将其推成旧5C的精确错误码映射或完整固件等价。
UFS stop=1、db=tdb=run=0、hcs=10F、is=0、poisoned=0、ready=0；SMMU GFSR=0/FSR=400仍残留，
不宣称完整路由恢复。M2的10527字节文件及SHA256 14f3d867…0991仍一致。
15项检查、24个双配置桥向量及C1/C2/INFO回归，镜像5818cd8079bca6f3096b2f061fc4b1351b777b22699774bdddb8563629239c6c与提交29d85a3894a48659a0cec4393330d1c89b5c474f的56个源码绑定有效。
本次没有root/对象、TA、blob/key读取或解密；C2未测试，不能写成C2通过。
后续可在已验证HLOS桥配置上独立复测最小root register/release，并保留原未知态不重试/不free策略。

### 2026-10-28：M3并行静态收敛与HLOS C2交付（未上机）

用户要求多做后续静态分析并行推进至必须上机。本轮未查询/重启/启动手机。
三路root/TA/storage契约审计完成，固定输入/反汇编/来源绑定及变异拒绝通过；
root消息null/op5/count1100/OO+20与cmd2未发现静态矛盾，不能由此证明真实client-env权限或返回。
TA链补出lookup优先、loadFromBuffer与send counts424；40KiB用户缓冲典型封送需86016字节（21页）加callback区，
lookup不证明is64且本机keymaster分片未恢复。convert请求23/返回36不透明bstr；
ICE三索引、六参扩展区、未初始化evict可不clear，以及UFS会重建清crypto字段等约束已记录，未接入密钥/TA运行。
HLOS最小C2候选out/m3-smci-root-hlos-v3已构建：明确72000602/224，桥HLOS配置，register→release→delete，
补完整raw/参数与只读CurrentEL/DAIF日志，未知态保留不重试。
16项完整检查退出0，148个C2生产向量、24个桥向量、TA离线46/存储无秘密26向量通过。
镜像SHA256 571a652ae7036524fad7a4ecf99d544def2716ee3bb006e111afe84cb5327992；源码提交fdeefb5852b5943a2fb6d03604eb6bfc9d8bbb78的56个交付源码及14个辅助源码绑定。
独立报告out/m3-static-advance-v1；规范专题docs/fuxi-m3-static-advance-20261028.md。
下一决定性步骤必须是在已验证HLOS桥上观察真实root register/release终态；C2仍未上机/未通过，TA/装钥/解密未验证。

### 2026-10-28：root+loader 组合批处理候选（未上机）

按“一次构建覆盖一组相关验证、一次开机逐级推进”的方案，把已验证 HLOS 桥、最小 C2 与 loader open/release
串成同一镜像 out/m3-smci-loader-hlos-v2：enable→create(HLOS)→root register(op5/1100)→open UID122(op0/1001)
→release loader→release env→delete；每步独立校验缓冲区并打印 a0..a3，未知态停在 stop=<stage> 并保留页面/句柄。
loader 契约由固定 smcinvoke_dlkm.ko @1e38..1e94 与 IClientEnv_open/线格式参考固定，6 项输入篡改均被拒绝；
UID122、BI 偏移32/长度4、OO 偏移20。仍不加载 TA、不读密钥、不转换、不写 ICE、不解密、无回调与重试。
18 项完整检查退出0；78 个批处理 ASan/UBSan 生产向量、148 个仅 C2 向量、24 个桥向量通过；报表缓存提到 192 行。
镜像 SHA256 12b094bf368046687ee6b9851ff843758d5c3069f8c58420f5fcdb0af8a99c91，56 个交付源码绑定；hardware=pending，C2/C3 均未上机。
下一步需一次实机：核对桥 create、root 原始返回、loader 句柄、两个 release、delete 与 PARK；出现未知态即停止。

### 2026-10-28：root+loader批处理attempt-01 — 停在root-register

照片IMG_7176..JPG，SHA256 b56e533fbdd4172ec896b39dfa9e50d9ba73e32fa8d272c7a0daff95457a35eb。本批在root-register阶段按设计停止，loader未尝试。

- 桥：page phys=C6A3D000（8KiB）；enable x0=42000C1C返回a0..a3=0；
  create x0=42000C1E，参数C6A3D006/C6A3D006/2200/3，返回a0=0、a1=0、a2=2EC81021、a3=0。
  显式HLOS配置再次被接受，桥句柄2EC81021。
- root register：x0=72000602、x1=224、handle=1、op=5、counts=1100，in=C6A3D000、out=C6A3E000。
  原始返回a0=a1=a2=a3=0；缓冲区回读hdr=1/5/1100、oi=0/0、oo=2/0，
  输出页首两字为00010002/0000FFFD，其余打印字为0。
- 判定：探针要求输出页全零且单一OO描述符可解析，因此返回EFI_COMPROMISED_DATA，
  BATCH stop=root-register passed=0 retained=1；未做句柄判断，未open loader，
  保留桥句柄与8KiB页，未擦写、未free、未重试。a0..a3全零只通过传输元组检查，不等于对象成功。
- OO回读为offset=2/size=0不是可用对象描述符；00010002/0000FFFD的含义尚未归类，
  本轮不把FFFD写成错误码，也不据此判固件ABI错误。
- UFS stop=1、db=tdb=run=0、hcs=0000010F、is=0、poisoned=0、ready=0；
  SMMU GFSR=0、FSR=00000400、far=1BBAE7DF98DF仍残留，不宣称完整路由恢复。
  M2的10527字节与SHA256 14f3d867…0991仍一致；PARK与cached report end可见。
- 镜像12b094bf368046687ee6b9851ff843758d5c3069f8c58420f5fcdb0af8a99c91与提交a4e0fc5023c9f0316cf1b54675ba9f305b286ffd的56个源码绑定、18项检查、78/148/24向量仍有效。
  无TA加载、无密钥使用、无转换、无解密；C2未通过，C3未开始。
- 下一步是离线把输出页布局与object-cmd2的返回分层对上，再决定是否放宽/改写接受子集；
  在此之前不重复上机。

### 2026-10-28：root+loader 批处理 v3（响应解码后，未上机）

attempt-01 在 root-register 停止的原始值为 a0..a3=0、hdr=1/5/1100、oi=0/0、oo=2/0、
out=00010002/0000FFFD/0/0。固定参考下：OO 槽是 32 位远端句柄，out 页只按 piggyback
结构消费且 counts(+8)=0，参考从不要求 out 全零；因此停止来自探针自己的接受子集。
v3 只放开 out 页前 8 字节（version/op），仍要求 +8..end 全零、句柄为远端、头部回显一致，
其余未知态继续保留页面并停止；新增 nz= 记录 out 页非零字节数。
82 个批处理 ASan/UBSan 向量（每次对象调用模拟 version/op）、148 个仅 C2 向量（含
+4108/+8191 拒绝向量）、24 个桥向量通过；18 项交付检查退出0；56 个交付源码绑定。
镜像 SHA256 44660291c87e2ed5d3695998e89ab8baa61ad5ec1e19d8b5fc0bbb835507ca94；hardware=pending，C2/C3 仍未通过。
下一步一次实机：预期 register 接受句柄2 → open UID122 → 两个 release → delete，仍以 stop= 为准。

### 2026-10-28：root+loader批处理attempt-01 — stop=complete passed=1F

照片IMG_7177..JPG，SHA256 81550901836e2feda8af0c8d215f8a749f1755f6c5aef3a43e0e9e153dd0a0a3。整条组合链一次启动走完。

- 桥：page phys=C6A3D000；enable a0..a3=0；create(HLOS 1VM/VMID3/权限6)参数C6A3D006/C6A3D006/2200/3，
  返回a0=0、a1=0、a2=5D1AABBE、a3=0；delete用5D1AABBE返回全零。
- root register：handle=1、op=5、counts=1100，返回a0..a3=0；hdr=1/5/1100、oi=0/0、
  oo=00000002/00000000、out=00010002/0000FFFD/0/0（nz=4）→ 接受远端对象2。
- loader open：在对象2上op=0、counts=1001、BI offset=32 size=4 UID=122，返回a0..a3=0；
  oi=00000020/00000004、oo=0/0 → 返回远端对象3。只按UID打开，未发送TA名、分片或加载请求。
- 释放：对象3（op=FFFF/counts=0）返回全零；对象2同样返回全零。
- 结束：BATCH stop=complete passed=1F env=0 loader=0 retained=0；released bytes=2000；
  result=Success；PARK与cached report end可见。
- 每次调用out页都回读00010002/0000FFFD、nz=4、counts(+8)=0；按piggyback语义无待处理对象，
  其含义仍不解释。SMMU GFSR=0、FSR=00000400、far=1BBBE7FFD8DF仍残留，不宣称完整路由恢复。
- UFS stop=1、db=tdb=run=0、hcs=0000010F、is=0；M2的10527字节与SHA256 14f3d867…0991仍一致。
- 镜像44660291c87e2ed5d3695998e89ab8baa61ad5ec1e19d8b5fc0bbb835507ca94与提交863010bd755edfa15b1309e30f9f50c885119ca8的56个源码绑定、18项检查、82/148/24向量仍有效。
  本次只证明该通路上桥、对象注册/打开/释放与桥清理闭环；未加载TA、未使用密钥、未转换、未写ICE、未解密。

### 2026-10-28：KeyMint TA lookup 候选（未上机）

在已通过的 root/loader 批处理上加一次 loader lookup：keymaster64 → （仅对象错误时）keymaster。
固定 AppLoader 头定义 lookupTA=op2/counts1001，名字为 ASCII BI，上限 64；本机模块 @1f18..1f84
的 mov w1,#0x2 / mov w3,#0x1001 与失败后 firmware_request_from_smcinvoke 分支一致；
命中分支绕过 EI_CLASS 读取，因此 lookup 成功不证明 is64。7 份固定输入逐一篡改均被拒绝。
lookup 未命中只在 a0=a1=a3=0 且 a2≠0 时解释为对象错误并原样记录，再回退第二个名字；
两次都未命中仍完成 loader/env release 与桥删除，最终 stop=lookup-missing、result=Not Found。
11 个 lookup 向量、82 个 loader 向量、148 个 C2 向量、24 个桥向量及 20 项检查全部通过；
镜像 SHA256 e252d7d308318a64d4ba06e9fccf45ec9f14b85508fb689a984170a5e6ddbfa1，56 个交付源码绑定；hardware=pending。
下一步一次实机：读 lookup 返回的原始 a2 与句柄；命中则释放 TA 对象，未命中则保留该值用于判断。

### 2026-10-28：TA lookup attempt-01 — 两次均 a2=2，对象层拒绝

照片IMG_7178..JPG，SHA256 ef1dd1d215a187e9f7852a473628e76bd6b56a3a80a2cf68b249cc1a166c5192。root/loader 链路仍完整，但两次 lookup 都被对象层拒绝。

- 桥：create 参数C697A006/C697A006/2200/3，返回a0=0、a1=0、a2=14194A61；delete用14194A61返回全零。
- register→2、open UID122→3 均与上一轮一致；lookup 在句柄3上执行。
- lookup keymaster64（BI offset=32 size=11）：a0=0、a1=0、**a2=2**、a3=0；oo=0/0，没有返回对象。
- 回退 lookup keymaster（size=9）：a0=0、a1=0、**a2=2**、a3=0；同样没有对象。
- 收尾：loader-release(3)、object-release(2)、bridge-delete(14194A61)全部成功；
  BATCH stop=lookup-missing passed=1F env=0 loader=0 ta=0 retained=0；released bytes=2000；
  result=Not Found；PARK 与 cached report end 可见。
- 固定头把2定义为OBJECT_ERROR_INVALID（不支持/未识别的请求）；AppLoader另有10..26的具体错误，
  2不在其中。**因此不能写成“TA不存在/未加载/名字不对”**：本轮只确认对象层拒绝了该请求。
  未确定的可能包括：该名字不在元数据、null凭据使loader拒绝、部署固件的请求形状不同、
  或open UID122返回的对象在此固件上不实现lookupTA。
- UFS stop=1、db=tdb=run=0、hcs=0000010F、is=0；SMMU GFSR=0、FSR=00000400 仍残留；
  M2 的10527字节与SHA256 14f3d867…0991仍一致。
- 镜像e252d7d308318a64d4ba06e9fccf45ec9f14b85508fb689a984170a5e6ddbfa1与提交67e70d9c7b57063a661b98ca73b7fcae8dff55dd的56个源码绑定、20项检查、11/82/148/24向量有效。
  无TA加载、无sendRequest、无密钥、无解密。
- 下一步应先把“名字是否存在”和“调用形状是否被接受”分开：需要独立证据确认部署固件里
  keymaster 分片的准确名字（例如从设备只读查看 /vendor/firmware_mnt/image），
  再决定是恢复分片走 loadFromBuffer，还是先做一次可区分的接口校准实验。

### 2026-10-28：KeyMint TA 来源与名字（Android 只读取证，未上机）

设备在 Android 17 下用自带 su 做只读检查：/vendor/firmware_mnt 对 shell 拒绝访问，
改用 su 列目录与 dd 到标准输出。找到专用 keymaster 分区 /dev/block/sde13（524288 字节），
内容是 ELF64 AArch64 TA（e_phnum=9、依赖 libcmnlib.so、含 KEYMINT_BEGIN/IMPORT_WRAPPED_KEY）。
TA 元数据（0x5a000）为 keymaster64 / 1.0 / "B=true;U=true;n=keymaster64;o=100;p=...;s=..."。
/vendor/firmware_mnt/image 只有 UUID 命名的分片，没有 keymaster64.bXX；含 keymaster 字符串的
只有 fingerpr.b01 与 seccammi.b01。镜像 SHA256 2fc838a8f0ed572ec77b7db2fe39ab4e81d0aa038bfad482b88087a27bf43cc6。
这解释了 lookup 返回 2：临时启动发生在 Android 之前，loader 里没有可复用的 app；
固定实现的顺序正是 lookup 失败后 loadFromBuffer。名字不是问题。
下一步是从该分区读 ELF 走 loadFromBuffer（约 405 KiB、100 页消息），仍不发送 KeyMint 命令。

### 2026-10-28：M3 KeyMint 输入与 UFS 路径静态复核（更正，未上机）

此前条目将 lookup 的 a2=2 断言为“没有可复用 app”、排除名称问题，证据不足；
它仍是对象层拒绝，未确定名称、授权、调用形状或真实应用状态。Android 未启动
不能证明 TZ 应用表为空。分区文件中的 n=keymaster64 是名称线索，不证明 lookup 命名空间。
固定参考支持 lookup 后走 loadFromBuffer，但不证明当前部署环境的装载来源。

本轮纯静态：A17 历史报告 /metadata 为 sda17，新取证 keymaster_a 为 sde13；
现有 FnuTestUnitReady/ReadCapacity/ReadBlocks 普通 LUN 均固定 0，GPT 只选 metadata/userdata。
故只加分区名不够；sde 到 UFS LUN 必须查设备映射，不能按字母猜。历史 `_b` 不是当前槽证据，
本地只存 `_a`。原生 DMA 每次最多 131072 字节，读取 512 KiB 至少四次。
镜像 SHA256 2fc838a8f0ed572ec77b7db2fe39ab4e81d0aa038bfad482b88087a27bf43cc6；
最后 PT_NULL 声明 0x62000+0x1058=0x63058，其后至 0x80000 全零，前缀哈希
9af72dfb96687f5d85874422ac944a4a070471be552db006ae445a30a262fb62；
这不是有效加载长度或签名验证。PT_DYNAMIC/PT_LOAD 和 GNU_RELRO/PT_LOAD 有重叠，
现有合成分片保守规划器会拒绝假造分片；不等于该镜像不能由固件加载。
固定参考根据实际分片长度和偏移拼装；无法从分区段表推定真实 bXX 长度。
纯主机模型分别以 0x63058 / 0x80000 为假设，输入消息页对齐 0x64000 / 0x81000，
另加最少 0x1000 callback 输出；参考允许更大 callback 区，不能把一页视为通用充分条件。
本轮未操作设备/SMC、未加载 TA 或解密，未重跑候选交付检查。
下一步需只读取当前槽、系统身份、sde 的 sysfs UFS LUN 映射和 A/B 设备分区哈希，
然后决定多 LUN 读取及 TA 输入/接口验证；不得因名字或全零尾部直接认定加载输入。
详细复核：D:/Projects/UniXNU/migration/20261028-m3-static-review/REVIEW.md。

### 2026-10-28：M3 Android 槽位与 UFS LUN 只读取证

用户启用 ADB，序列号 fcde91e、device fuxi。Android 当前 ro.boot.slot_suffix=_a；
ro.build.version.release=17、display CP2A.260605.016；fingerprint 自身包含 16，原样记录，
不把属性不一致解释为构建已核验。kernel 5.15.220-g6513a0d19135。
metadata -> sda17；sysfs sda SCSI 0:0:0:0、sde 0:0:0:4，属于同一 1d84000.ufshc；
Android 层显示分别为 LUN 0 和 4。sde 逻辑块 4096，keymaster_a -> sde13，
keymaster_b -> sde43，sysfs start/size（512 字节单位）分别 1191984/1024、3417240/1024。
换算 4096 字节 LBA 为 148998/427155，长度均 128 块；仅作对照，不硬编码，UEFI 需独立验证容量及 GPT。
a、b 分区均 524288 字节，全分区 SHA256 分别为
2fc838a8f0ed572ec77b7db2fe39ab4e81d0aa038bfad482b88087a27bf43cc6 和
fe18a467cbbd9457cc8e2e4a05822ff056238342881cfc558a045aea6d467eac；
a 与此前保存副本一致，b 不同且未提取，原因未知。后续启动时仍需核实槽位。
未写分区、未重启、未调用 UEFI/SMC、未加载 TA/解密；未证明 UEFI 跨 LUN 成功或 load 输入正确。
原始输出和详情：D:/Projects/UniXNU/migration/20261028-m3-adb-lun-slot/。

### 2026-10-28：LUN4 keymaster 只读候选 v2（未上机）

独立 UEFI DXE 候选 m3-lun4-keymaster-v2：先核验 LUN0 的 M2 文件长度和 SHA256，
失败跳过 LUN4；LUN4 TUR/capacity、GPT CRC 和分区边界校验后，分别分块哈希
keymaster_a/b 并对照本机 Android 全分区哈希。slot=unknown；不自动选槽、不加载 TA、
不读密钥、不写分区。一次读取上限 16 块，每个分区 128 块。异常停止后续读取，
保留现有 UFS quarantine 和停队列保护。硬件和 SMMU 完整恢复仍未验证。
GUI 监视器构建完成 exit=0；v2 镜像 SHA256
22586f4458b1e8e65527e2139b3ec5e9d1aa472d152994206240c703f2ac8e93；
12 项资格检查退出0；88 个 GPT 等 fixture、UFS 核心及生产哈希路径测试通过；
56 项交付源码与 UEFI 提交 cbde65ffe97601431b9d0a99d90ec8153a36d60a 绑定一致。
GUI 修复状态文件读取/原子替换竞争，实时显示固件模块、调整 finalize 独立进度并保留旧 run；
GUI 自检、真实构建以及独立成功/失败状态测试通过。v1 因默认关闭配置辅助模块链接失败未交付。
标准单次启动入口已适配 parked 诊断、Windows fastboot 和源码/资格绑定；模拟成功、篡改拒绝、
重复启动拒绝均通过。脚本原有未提交修改保留；真实设备待 fastboot 后验证。
详情 D:/Projects/UniXNU/migration/20261028-m3-lun4-keymaster-v2/REVIEW.md。

### 2026-10-28：LUN4 keymaster v2 attempt-01 — Booting USB 读取失败，待照片

用户已进入 fastboot。互斥检查无活动任务；fcde91e、fuxi、unlocked=yes、current-slot=a，
profile=usb-ram，镜像 22586f4458b1e8e65527e2139b3ec5e9d1aa472d152994206240c703f2ac8e93。
已完成 12 项检查及 56 项源码提交绑定验证。独立 attempt-01 一次 fastboot boot，
Sending OKAY，Booting FAILED：usb_read failed（Windows device error 31），退出 1；
无 flash/擦除/slot 更改，无重试。命令失败不证明设备当前阶段或 UEFI 结果，等待照片。
原始记录 D:/Projects/UniXNU/migration/20261028-m3-lun4-keymaster-v2/attempt-01/device-test.json。

### 2026-10-28：LUN4 keymaster v2 attempt-02 临时启动（待照片）

用户明确要求再次启动；先确认 fcde91e 重新出现在 fastboot、设备互斥，再创建独立 attempt-02。
核验 fuxi、unlocked=yes、current-slot=a、usb-ram profile、12 项资格及 56 项源码/镜像绑定；
镜像 SHA256 22586f4458b1e8e65527e2139b3ec5e9d1aa472d152994206240c703f2ac8e93。
一次 fastboot boot：Sending/Booting OKAY，退出 0；UTC 2026-10-28T12:33:37.557851+00:00；
无 flash/擦除/slot 更改。主机命令成功不证明 LUN4 读取、GPT、哈希或完整 SMMU 恢复；
待照片检查 M2、FUXILUN4、UFS/SMMU 与 PARK。原始记录在候选及
D:/Projects/UniXNU/migration/20261028-m3-lun4-keymaster-v2/attempt-02/device-test.json。

### 2026-10-28：LUN4 keymaster v2 attempt-02 照片 — 两槽只读哈希通过

照片 IMG_7180..JPG，SHA256 4059c6e898239721a30e24449825abd215ff01a44c62ad4d33b8a63a538de95c。
LUN0 M2 文件 10527 字节，SHA256 14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991。
LUN4 tur/capacity/GPT=Success，last=8FBFF、block=4096；GPT a=24606..24685、b=68493..68512
（上述 LBA 为十六进制），各 128 块，与 Android 只读记录的 512B 扇区换算一致。
a 哈希 2fc838a8f0ed572ec77b7db2fe39ab4e81d0aa038bfad482b88087a27bf43cc6，
b 哈希 fe18a467cbbd9457cc8e2e4a05822ff056238342881cfc558a045aea6d467eac；
hash-a/hash-b=Success，与 Android 全分区哈希一致。slot=unknown，不自动选择；
上机前 fastboot current-slot=a 不能代替 UEFI 侧槽位证明。
UFS stop=1、db=tdb=run=0、hcs=10F、is=0、poisoned=0、ready=0；PARK、cached report end 可见。
SMMU GFSR=0、FSR=400 残留，不宣称完整恢复。镜像 22586f4458b1e8e65527e2139b3ec5e9d1aa472d152994206240c703f2ac8e93
的 12 项资格与提交 cbde65ffe97601431b9d0a99d90ec8153a36d60a 的 56 项源码绑定重新核验通过。
仅证明限定诊断的 LUN4 只读/哈希，不证明 TA 加载输入、签名、KeyMint 会话或解密；无 XNU handoff。
原图和 assessment 在候选及 D:/Projects/UniXNU/migration/20261028-m3-lun4-keymaster-v2/attempt-02/。

### 2026-10-28：KeyMint MBNv7 静态验签与 LUN4 大桥候选（未上机）

Android keymaster_a 副本全分区 SHA256 2fc838a8f0ed572ec77b7db2fe39ab4e81d0aa038bfad482b88087a27bf43cc6。
离线按 MBNv7 头解析哈希段：header 40 + common 24 + OEM metadata 224 + hash table 432 +
签名槽 104 + 证书槽 3360 = 0x1058，结束 0x63058。8 个 ELF 范围 SHA384 匹配，
hash 段对应项为零；ECDSA SHA384 镜像签名及两级证书签名数学验证通过，五处篡改被拒绝。
这不认证设备根信任或 loader 授权，不证明正确加载长度或槽位；分区尾部全零不证明可截断。

独立候选 m3-lun4-large-bridge-v1：保留 LUN0 M2 与 LUN4 a/b 哈希阶段；两槽通过且
UFS 未 poisoned、INFO 可用后，HLOS 大桥 enable/create/delete，130 页 0x82000，
size 字 0x82200、VMID3、权限6；未知态保留页、不自动重试。桥中全零，无对象调用、
TA、key、解密或分区写入。slot=unknown；不把上一轮 fastboot 槽位当 UEFI 当前槽。
GUI 构建退出0，15 项交付检查退出0；36 个桥生命周期向量包含 12 个大桥配置，
LUN4 分阶段成功/七个失败路径以及生产哈希测试通过；56 项源码绑定提交
 ee04d068f4cfc3e7f81fac973ea8db944f64fbb1。镜像 SHA256
7681d106b26714ef296859999d794b728a22dee1c52ecff2cccba16eae0ca04c。
未上机；大桥是否被接受、TA 是否可加载和 SMMU 完整恢复均未知。详情
D:/Projects/UniXNU/migration/20261028-m3-lun4-large-bridge-v1/REVIEW.md。

### 2026-10-28：LUN4 大桥 attempt-01 — Booting USB 读取错误（设备阶段未知）

用户确认进入 fastboot；互斥检查通过，fcde91e、fuxi、unlocked=yes、current-slot=a，
usb-ram profile，15 项资格检查及 56 项源码绑定通过；镜像 SHA256
7681d106b26714ef296859999d794b728a22dee1c52ecff2cccba16eae0ca04c。
独立 attempt-01 一次 fastboot boot，Sending OKAY、Booting FAILED：usb_read failed
（Windows 错误 31），退出 1；无 flash/擦除/slot 更改，不自动重试。设备实际执行阶段未知，
待屏幕照片，不能宣称大桥或 UFS 测试通过。原始记录位于候选及
D:/Projects/UniXNU/migration/20261028-m3-lun4-large-bridge-v1/attempt-01/device-test.json。

### 2026-10-28：LUN4 大桥 attempt-02 — 再次 Booting USB 读取错误（待照片）

用户要求再次启动，重新确认 fcde91e 处于 fastboot 且无其他设备任务后，建立独立 attempt-02。
校验 fuxi、unlocked=yes、current-slot=a、usb-ram profile、15 项资格及 56 项源码绑定；
镜像 SHA256 7681d106b26714ef296859999d794b728a22dee1c52ecff2cccba16eae0ca04c。
一次 fastboot boot：Sending OKAY、Booting FAILED，Windows usb_read error 31，退出 1；
无 flash/擦除/slot 更改；设备是否进入固件和桥阶段未知，不自动重试，等待屏幕照片。
原始记录保存在候选及 D:/Projects/UniXNU/migration/20261028-m3-lun4-large-bridge-v1/attempt-02/。

### 2026-10-28：LUN4 大桥 attempt-03 临时启动（待照片）

用户要求再启动，重新确认 fcde91e 在 fastboot 且设备互斥后创建独立 attempt-03；
核验 fuxi、unlocked=yes、current-slot=a、usb-ram profile、15 项资格和 56 项源码/镜像绑定。
镜像 SHA256 7681d106b26714ef296859999d794b728a22dee1c52ecff2cccba16eae0ca04c；
一次 fastboot boot Sending/Booting OKAY，退出 0；无 flash/擦除/slot 改动。
主机成功不证明 LUN4 或大桥生命周期成功，等待照片。原始记录在候选及
D:/Projects/UniXNU/migration/20261028-m3-lun4-large-bridge-v1/attempt-03/device-test.json。

### 2026-10-28：LUN4 大桥 attempt-03 照片 — 0x82000 桥闭环通过

照片 IMG_7181..JPG，SHA256 71c60b6ebbe2dea5beec1f691bff2bc9c7514b46382c7a1a1dd2b95d1098c46b。
LUN0 M2 文件 10527 字节、SHA256 14f3d8675423917e383a71c936f4d9a81a29480f34ae3c550e0f4c34e53a0991；
LUN4 TUR/capacity/GPT、a/b 两槽全分区哈希均 Success，与 Android 对照一致，slot=unknown。
HLOS bridge 1VM/VMID3、NS/TZ权限6；page phys=C64E4000、bytes=82000。
enable a0..a3=0；create 参数 C64E4006/C64E4006/82200/3，返回 a0=0、a1=0、a2=64676AA2、a3=0；
delete 用完整句柄 64676AA2，返回 a0..a3=0；result=Success、released bytes=82000。
PARK 和 cached report end 可见；清零/cache 为源码顺序结论，照片不独立证明擦除。
UFS stop=1、db=tdb=run=0、hcs=10F、is=0、poisoned=0、ready=0；
SMMU GFSR=0、FSR=400 仍残留，完整恢复未证明。15 项资格、镜像
7681d106b26714ef296859999d794b728a22dee1c52ecff2cccba16eae0ca04c 和提交
 ee04d068f4cfc3e7f81fac973ea8db944f64fbb1 的 56 项源码绑定重新核验通过。
本轮仅证明 0x82000 桥生命周期；未调用对象、加载 TA、使用密钥、解密或 XNU handoff，
不证明 TA 输入长度/信任、UEFI 槽位或 callback 行为。原图及 assessment 在候选与
D:/Projects/UniXNU/migration/20261028-m3-lun4-large-bridge-v1/attempt-03/。

### 2026-10-28：M3 大桥 root/loader 组合候选（未上机）

m3-large-loader-v1 在 LUN0 M2 文件及 LUN4 两槽哈希通过、UFS 未 poisoned、
INFO/invoke 可用后，以 130 页 0x82000 HLOS 桥执行 root register → open UID122
→ release loader → release environment → bridge delete；对象调用输入输出仍各 4096 字节。
失败或未知状态保留资源并停止。无 lookup、TA 加载、密钥操作或解密；slot=unknown。
GUI 构建退出 0，18 项资格检查退出 0；生产对象生命周期小桥、大桥分别 82 个模拟向量，
ASan/UBSan 通过。56 项固件来源与 UEFI 提交 8f9a6771 绑定验证通过；
镜像 SHA256 b0de5540f72692556694d77febca7f92439e14fdfc3c7159e414c6e55fdc704b。
初次资格检查因校验器固定要求 8192 字节失败，校验器修正并增强大桥测试后全套重跑通过。
该候选未上机，TA 输入长度、信任、加载及回调仍未验证；SMMU 旧 FSR 残留待调查。
设备当前未在 fastboot 列表；未发启动命令。
记录：D:/Projects/UniXNU/migration/20261028-m3-large-loader-v1/REVIEW.md。

### 2026-10-28：m3-large-loader-v1 attempt-01 照片 — 0x82000 桥上对象链闭环

照片 IMG_7182..JPG，SHA256 7bae77cdb56662c04c794f944eae882b21804e0c1838f84281f77e82c68d0d6c。
主机启动记录 status 此前仍为 awaiting screen evidence，本轮按 EXIF 方向 6 旋转后逐行裁切、
原尺寸判读补齐（缩略图会让 b-sha256 一类长十六进制读错，勿据缩略图归档）。

- 门槛：M2 文件 nid=12、10527 字节、SHA256 14f3d867…0991、result=Success；
  LUN4 tur/capacity/GPT 均 Success，last=8FBFF、逻辑块4096，a=24606..24685、b=68493..68512，
  a-sha256=2FC838A8…3CC6、b-sha256=FE18A467…7EAC，与 Android 侧对照一致；slot=unknown。
- 桥：in=C64DC000、out=C64DD000（输出页仍为输入页+0x1000）、bytes=82000、130页；
  enable 全零；create 返回句柄 426FF842；delete 用 426FF842 返回 a0..a3=0；released bytes=82000。
- 对象：plan=root/open122/release-loader/release-env/delete；root register 成功；
  open UID122 得远端对象3；loader-release=Success handle=3；object-release=Success handle=2；
  BATCH stop=complete passed=1F env=0 loader=0 retained=0；
  result=Success retained=0; no TA, no key, no decrypt。
- UFS：stop=1、db=tdb=run=0、hcs=0000010F、is=00000000、poisoned=0、ready=0。
  SMMU：gfsr=00000000、fsr=00000400、far=1BBBE7FF98DFF、smmu-configured=1；fsr 残留仍未解释。

判读：本轮新信息是对象链在 0x82000 桥上成立；同一条链在 8 KiB 桥上已由 IMG_7177
（m3-smci-loader-hlos-v3）证明，因此不是新功能里程碑，而是 TA 通道所需容量级别的桥被固件
接受并完成闭环。passed=1F 是探针完成掩码，不是固件返回的 TA 成功。
未做 lookup、TA 加载、KeyMint 命令、密钥、ICE 或解密；slot 仍 unknown；桥成功不证明 TA
输入长度、签名被设备接受或回调行为。原图与 assessment 在候选 attempt-01，报告
D:/Projects/UniXNU/migration/20261028-m3-large-loader-v1/REVIEW.md。

### 2026-10-28：m3-smci-load-v2 — 大桥上加一次 loadFromBuffer（未上机）

用户要求推进下一步技术动作。在已实机通过的 0x82000 桥与 root/open UID122 之上只加一步
`loadFromBuffer`，不发送 KeyMint 命令、不取密钥、不写 ICE、不解密。

- 契约：op=1、counts=0x1012（BI2/BO1/OO1）；头 12 + 4×8 描述符 = 44，载荷对齐 48；
  BI0=ELF(off 48, 0x63058)、BI1=`keymaster64`(11B)、BO=64B distName、OO=app；消息 0x630D8。
- **输出页从 in+0x1000 移到桥尾 0x81000**：原布局下 100 页输入会覆盖输出页。输入上限相应 0x81000。
- 输入取 `keymaster_a` 开头 0x63058（MBNv7 段结束/元数据/哈希/签名/证书声明端点），
  在 LUN4 队列停止前读取，并在设备内计算该区间 SHA-256 打印（`FUXIM3 TA extent bytes=... sha256=...`），
  成功或失败都擦除并释放该缓冲。
- 失败记录 v1：构建命令已带 `-D FUXI_M3_SMCI_LOAD=1`，但 `fuxi.dsc` 只转发既有宏到
  `CC_FLAGS`，新宏未加入，C 侧仍按 0 编译，镜像是旧 batch；交付检查按缺失 marker 正确拒绝。
  补上 DSC 默认值与转发后重做为 v2。这说明新增 FUXI 宏必须同时改 DSC，不能只看 `-D` 出现在命令行。
- 构建：GUI 监视器退出 0（`run-build.sh` 增加可选第 3 参数指定仓库，UEFI 候选须指向
  `uefi-twrp-data`，此前默认只找设备仓库会报 candidate not found）；19 项资格检查退出 0。
  新增 23 个 load 向量（ASan/UBSan：成功、分配失败、无镜像、loader status、描述符/偏移篡改、
  输出页脏、句柄非法、各释放失败），既有 loader 82 向量与 root 向量回归通过。
- 镜像 SHA256 3892da87e48998547cc51ac99b5ec26d9b5f660ff07c90b4562414e717e8c1df，
  1880064 字节；UEFI 提交 bcc72b8e 已推送 origin/feat/twrp-data-uefi。
- 未上机。边界不变：slot=unknown、SMMU FSR 0x400 残留；固件是否接受该输入长度、签名与
  TA 名仍是未知；`passed=7F` 是探针完成掩码，不是固件接受 TA 的证据。
  记录：D:/Projects/UniXNU/migration/20261028-m3-smci-load-v2/REVIEW.md。

### 2026-10-28：m3-smci-load-v2 attempt-01 临时启动（待照片）

设备 fcde91e / fuxi / unlocked=yes / current-slot=a，互斥检查通过；profile usb-ram。
确认镜像 SHA256 3892da87e48998547cc51ac99b5ec26d9b5f660ff07c90b4562414e717e8c1df，
19 项资格检查与 56 项源码绑定（UEFI 提交 bcc72b8e2051138f4a58414febc24bd6499f20a6）通过。
启动助手新增该镜像的精确白名单条目（op 序列含 loadfrombuffer-keymaster64、m3_smci_load=true、
m3_smci_ta_bytes=0x63058、19 项检查），未放宽其他条件。

一次 `fastboot boot`：Sending OKAY、Booting OKAY，退出 0；无 flash/擦除/slot 更改。
主机成功不证明 load 成功，等待屏幕照片判读；不自动重试。
原始记录：D:/Projects/UniXNU/migration/20261028-m3-smci-load-v2/attempt-01-device-test.json
与候选 attempt-01/。

### 2026-10-28：m3-smci-load-v2 attempt-01 照片 — 停在 TA 读取（未进入对象批处理）

照片 IMG_7183..JPG，SHA256 7fb623494b8331e1fa437b32f311fe8f337228ee39b351a6b985780ee4a70b88。

- M2 文件 Success；LUN4 tur/capacity/GPT Success，a=24606..24685、b=68493..68512，
  a-sha256=2FC838A8…3CC6、hash-a=Success；slot=unknown。
- 新步骤失败：`FUXIM3 TA read=Invalid Parameter bytes=0`。随后 `RunLun4Keys` 返回 FALSE，
  `mLun4Passed` 未置位，整段 M3 被跳过（`FUXIM3 SMCI skipped: UFS/INFO/invoke availability not ready`），
  `final stage=lun4-keymaster`。**本次没有任何对象调用，load 请求没有送达安全世界。**
- 设备收尾干净：stop=1、db=run=0、hcs=0000010F、is=0；SMMU gfsr=0、fsr=0x400 残留照旧。
- 原因：`FdpReadPartition` 限制单次 `Blocks > FDP_MAX_GPT_BYTES / BlockSize` = 16 块；
  本次一次请求 100 块（409600 字节），被该上限以 EFI_INVALID_PARAMETER 拒绝。
  既有哈希循环按 16 块调用，所以此前从未触发。
- 附带问题：读取放在 hash-a 与 hash-b 之间，失败后 b 槽哈希没有评估。
- 修复：同一区间改为 16 块分块读取，并把读取移到两槽哈希之后；不改变 load 消息与停止条件。
- 结论：这次是**探针自身的读取上限**，不是固件拒绝 TA；`a2` 仍无样本。
  原始记录与照片在候选 attempt-01；报告
  D:/Projects/UniXNU/migration/20261028-m3-smci-load-v2/REVIEW.md。

### 2026-10-28：m3-smci-load-v3 attempt-01 照片 — 加载被拒 a2=0xFFFFFFF0（-16）

照片 IMG_7184..JPG，SHA256 886e05b5ec0cf7be41a23930bc0a184d5f9e048f0aa2a9b9b630587b033a65cf。

- 分块读取修复生效：`TA read=Success bytes=63058`；两槽哈希均 Success。
  设备打印的 extent 摘要 9AF72DFB96687FD5D85874422AC9444A070471BE552DB006AE445A30A262FB62
  与本地对 keymaster-partition.img 前 0x63058 重算结果**逐字相同**，送进去的确实是审计过的字节。
- load 调用：object=3、op=1、counts=1012、in=C6478000、out=C64F9000；
  描述符 00000030/00063058、00063088/0000000B、00063098/00000040、oo=00000000。
  返回 a0=0、a1=0、**a2=FFFFFFF0**、a3=0。
- out 页全零，没有此前每次调用都出现的 00010002/0000FFFD piggyback 头 —— 在产生响应之前就被拒。
- 批处理 stop=loader-load、passed=3、env=2、loader=3、retained=1、result=Device Error；
  桥与对象按策略保留，TA 缓冲擦除释放（released bytes=64000）。
- UFS stop=1、db=run=0、hcs=0000010F、is=0、poisoned=0；SMMU gfsr=0、fsr=0x400 残留。

-16 不在对象错误表（1..4、-90..-99）、也不是 AppLoader 的 10..26，本轮不解释其含义。
与之前四次成功调用相比唯一的结构差异：那四次 out 都紧跟在 in 之后（in_len=0x1000、out=in+0x1000），
本次 out 在 in+0x81000，且 in_len=0x630D8 既非页对齐也不与 out 相邻。
下一候选只改这一项：in_len 取 0x64000（页对齐），out 移到 in+0x64000。

未验证：-16 语义、TZ 内是否留下应用状态、能否发送 KeyMint 命令。
原图与 assessment 在候选 attempt-01；报告 D:/Projects/UniXNU/migration/20261028-m3-smci-load-v3/REVIEW.md。

### 2026-10-28：M3 大桥 loader attempt-01 临时启动（待照片）

## attempt-01：临时启动成功，待屏幕证据

设备 fcde91e，product=fuxi、unlocked=yes、fastboot current-slot=a；镜像 SHA256
b0de5540f72692556694d77febca7f92439e14fdfc3c7159e414c6e55fdc704b。
18 项资格检查、56 项提交来源及镜像绑定验证通过。第一次启动助手资格检查在任何设备查询或
启动之前拒绝了不在旧白名单的组合；为本次镜像建立精确限定的独立助手后执行一次 fastboot boot，
Sending/Booting 均 OKAY，退出 0；无 flash、擦除或 slot 更改。设备结果待照片，不能把命令成功
当作对象生命周期或 SMMU 恢复成功。记录见 attempt-01/device-test.json。


### 2026-10-29：更正 v3 照片返回值及 v4 现状

IMG_7184..JPG 原图按 EXIF 方向转正放大后，loader-load 返回 a0=0、a1=0、
a2=FFFFFFFFFFFFFFA0、a3=0；下方 loader-status=FFFFFFA0。低 32 位有符号值为 -96，
不是前述 -16/FFFFFFF0，之前记录的错误码及据此排除对象错误表的结论作废。
固定参考 smcinvoke_object.h SHA256 46fcdb259980ea248a8adacae5008983628cc7018d2022ab76c7a11722b85aba
将 -96 定义为 OBJECT_ERROR_UNAVAIL（请求无法处理）；参考与本机固件并未证明完全等价，
具体失败原因和安全世界内部状态仍未知。out 页全零不能独立证明请求未到达哪一层。

v4 将输入长度改为 0x64000 并将 out 页放在其后，这是可能的兼容性试验而非已证实的修复。
镜像 SHA256 77b199d807f8139982c3781158abb4bbec270aaad20069e2ea3182cde4ceb0bd，
56 项源码与 UEFI 提交 cfded2f1c4fd2b4b8e2d2c85ea41c9ca3cb3c80b 绑定，19 项资格检查通过，
未上机。报告 D:/Projects/UniXNU/migration/20261028-m3-smci-load-v4/REVIEW.md。

此外上一条「大桥 loader attempt-01 待照片」是旧启动记录的延迟补记；IMG_7182 的照片结论
已在上方 m3-large-loader-v1 条目归档，且后续 v2/v3 已完成，不代表仍在等待该旧照片。

### 2026-10-29：m3-smci-load-v4 attempt-01 临时启动（待照片）
## attempt-01：临时启动完成，待照片

设备 fcde91e、fuxi、unlocked=yes、current-slot: a；镜像 SHA256
77b199d807f8139982c3781158abb4bbec270aaad20069e2ea3182cde4ceb0bd。
19 项资格、56 项源码和镜像绑定验证通过；一次 fastboot boot Sending/Booting OKAY，
退出 0；无 flash、擦除或槽位更改。主机结果不能证明 TA 加载或资源恢复；待照片。
原始记录 attempt-01/device-test.json。

### 2026-10-29：m3-smci-load-v4 attempt-01 照片
## attempt-01 照片：零状态与输出对象，探针在返回长度校验停止

照片 IMG_7191..JPG，SHA256 `cd1416481056e85ac1e1e1c801a9beed0cf437499735d50f5116844662f1486d`。M2 与 LUN4 a/b 哈希成功，TA 读取 bytes=63058，
extent 摘要与固定输入一致。load 输出 in=C6478000、out=C64DC000，消息长度 0x64000；
返回 a0=a1=a2=a3=0，描述符 ELF=30/63058、name=63088/B、BO=63098/8，OO=4，
out=00010002/0000FFFF、nz=4。随后 stop=loader-load passed=3 env=2 loader=3 retained=1，
result=Compromised Data。UFS stop=1、db=tdb=run=0、poisoned=0；SMMU FSR=400 仍残留。

源码要求 BO 返回长度必须等于请求容量 64；固定参考 smcinvoke.c marshal_out_invoke_req
采用安全世界返回的 BO 长度。此次 8 小于 64，过严的固定长度判断解释了可见停点（其他未打印
的校验内容不能仅凭照片完全排除）。零状态与返回对象为重要进展，但对象未由探针接受或释放，
未执行 KeyMint 命令，不能据此宣称 TA 可用或完整生命周期成功。

### 2026-10-29：m3-smci-load-v5 修正 BO 返回长度（未上机）

v4 照片显示 BO 实际输出长度 8，小于原始容量 64；固定参考会接受返回长度。
v5 接受 BO 长度 <=64，保持其他返回和缓冲检查，拟继续释放 TA、loader、env 和删除桥。
25 个加载模拟向量及 19 项资格检查退出 0；GUI 构建成功。
镜像 SHA256 ddf35cbdf5723e1fbe13e417b108c3c085be3aee9a606cca5166d5ca4ad9a658，56 项源码绑定 UEFI 提交 5b40ec3c14306b3298916f6dd4ae9392f4252aa9。
未上机，不证明 TA 命令、解密或 SMMU 完整恢复；报告 D:/Projects/UniXNU/migration/20261029-m3-smci-load-v5/REVIEW.md。

### 2026-10-29：m3-smci-load-v5 attempt-01 临时启动（待照片）
## attempt-01：临时启动完成，待照片

设备 fcde91e、fuxi、unlocked=yes、current-slot: a；镜像 SHA256
ddf35cbdf5723e1fbe13e417b108c3c085be3aee9a606cca5166d5ca4ad9a658。
19 项资格、56 项源码和镜像绑定验证通过；一次 fastboot boot Sending/Booting OKAY，
退出 0；无 flash、擦除或槽位更改。主机结果不能证明 TA 加载或资源恢复；待照片。
原始记录 attempt-01/device-test.json。

### 2026-10-29：m3-smci-load-v5 attempt-01 照片 — 加载与释放闭环
## attempt-01 照片：加载与对象释放闭环通过

照片 IMG_7192..JPG，SHA256 `9221f7284699fb67650090c2f9ed7a25b8f6df35dcd392279773db6aabd0102e`。M2 文件与 LUN4 两槽哈希成功；
TA extent 0x63058 字节读取成功、摘要与既有对照一致；slot=unknown。
loadFromBuffer 返回 a0=a1=a2=a3=0，输出 BO 长度 0x0B、对象句柄 4。
app-release handle=4、loader-release handle=3、object-release handle=2 均 Success；
bridge-delete handle=C68D0873 返回 a0..a3=0；BATCH stop=complete passed=7F、
env=0 loader=0 retained=0，result=Success；reported bridge released bytes=82000、
TA extent released bytes=64000。清零由源码流程推断，照片不单独证明内存擦除。
UFS stop=1、db=tdb=run=0、poisoned=0；SMMU FSR=0x400 仍残留，不宣称完整恢复。
19 项资格检查、56 项源码绑定及交付镜像摘要重新核验通过。

更正：v4 的 IMG_7191 原图 BO 长度同样为十六进制 0x0B（11 字节），不是此前误读的 8。
两者均小于原容量 64；v5 修复接受有界返回长度的结论不变。

这证明本轮 loader 接受输入并返回对象，且探针的对象与桥释放调用闭环；未发 KeyMint 命令，
不证明对象可执行预期命令、密钥转换、ICE、userdata 解密、UEFI 当前槽或系统交接。

### 2026-10-29：KeyMint getVersion 与命令外壳离线准备（未上机）

依用户要求延后实机验收，先推进离线协议：固定本机库和模块支持 getVersion 命令 0x200、请求 4 字节、响应容量 0x9ffc、SMCI op=0/counts=0x0424；已审计 TA 是 ELF64。固定参考中 null TZ handle 为 0。构造只在主机运行的 getVersion 消息与返回检查模型，及版本协商/配置/转换共有的命令外壳模型；无设备、无真实密钥、无生产固件接入。固定输入和指令审计退出 0，10 个主机测试方法通过。终态、piggyback、设备 ABI 和具体序列化仍未通过实机或厂商编码器验证；不宣称 KeyMint 命令执行成功。详细证据及边界见 D:/Projects/UniXNU/migration/20261029-m3-getversion-static/REVIEW.md。

补充：固定 libqtikeymint 的 serializeInteger 使用 map 和整数标签 11，结合调用点参数 200，离线模型给出 CBOR 候选 a1 0b 18 c8；尚未与厂商编码器或设备核对。扩展后的固定输入审计及 11 个主机测试方法退出 0；本条更新前述 10 项测试数，不增加实机结论。

### 2026-10-29：m3-smci-version-v2 — getVersion 受限候选（未上机）

UEFI DXE 增加默认关闭的 getVersion 调用：root/open/load 后使用 op0/counts=0424、命令 0x200；仅处理已观察的空输出页形式，异常或未知状态停止并保留桥和对象。TA 需为 ELF64/小端，不执行 callback、版本配置、密钥转换、ICE 或解密。镜像 SHA256 c244ffe7e3eed02c72e9d43af8f1e65517c61d59c1120ddb9b85a388a0e1100c；监视器构建成功，20 项主机检查退出 0，新增 40 个及加载回归 25 个模拟向量通过，56 个源码绑定提交 5aa32739c50ab123f773aa7c25ea1b927eb100b9。未上机；TA 二次读取摘要未在设备端直接对照固定值，历史 SMMU FSR 0x400 未解释。未来实机前需建立本镜像精确启动资格。详见 D:/Projects/UniXNU/migration/20261029-m3-getversion-uefi/REVIEW.md。

### 2026-10-29：m3-smci-version-v3 — 有界读取和固定摘要（上机前）

v2 及之前 TA 读取按 16 块一组读取 100 块时，最后一次仍读 16 块，可能写出 100 页缓冲；v3 修正最后一组为 4 块，且在加载前对第二次读取的 TA extent 强制校验 SHA256 9af72dfb96687f5d85874422ac944a4a070471be552db006ae445a30a262fb62。原 v3 照片条目中的摘要文字转录与固定输入实算不同；不据此推断原照片的字符。11 个实际生产读取函数模拟向量及 ASan/UBSan 通过；21 项资格检查退出 0，56 项源码与提交 90d18099823deee8ea214947cd699468401844d7 绑定，镜像 SHA256 59dbddf4ac04074771bf38df26995348d65f519e021277ed790ee0e485157df0。用户授权一次临时启动；照片前不作设备结论。见 D:/Projects/UniXNU/migration/20261029-m3-version-v3/REVIEW.md。

### 2026-10-29：m3-smci-version-v3 attempt-01 临时启动（待照片）

12:13 Asia/Shanghai，设备互斥检查通过，fcde91e、fuxi、unlocked=yes、current-slot=a；镜像 SHA256 59dbddf4ac04074771bf38df26995348d65f519e021277ed790ee0e485157df0。21 项资格检查及 56 项源码绑定通过，单次 fastboot boot Sending/Booting 均 OKAY、退出 0；无 flash、擦除或槽位修改。设备结果待照片，不能由主机成功推断 getVersion 或释放成功；不自动重试。原始记录 D:/Projects/UniXNU/migration/20261029-m3-version-v3/attempt-01/device-test.json。

### 2026-10-29：m3-smci-version-v3 attempt-01 照片 — getVersion 返回，loader 释放停住

IMG_7208..JPG SHA256 018340805948588a662a4b6cd70852201a27b08901a155420ceab294fd7e3062。M2 文件、LUN4 a/b 哈希及 TA extent 固定摘要校验成功；TA load 成功，app=4。getVersion op=0/counts=424，a0..a3=0；探针通过响应检查并打印 BO 长度 9FFC、四个原始字段 00000004/00000001/00000004/000003F4；长度不能直接等同实际有意义负载，字段语义未核验。

app-release handle=4 Success；loader-release handle=3 返回 a0=1、a1=224、a2=C6467000、a3=1000，out 全零；stop=loader-release passed=E3 env=2 loader=3 retained=1，result=Not Ready，桥 633C4CD4 保留，未释放环境或删除桥。a0=1 的含义、终态及安全恢复尚未证实，不重试。TA 缓冲报告释放，清零仅由源码推断。UFS stop=1、db=tdb=run=0、poisoned=0；SMMU FSR=400 残留。未验证配置、密钥转换、解密或系统交接。原图、assessment 和 REVIEW 在 D:/Projects/UniXNU/migration/20261029-m3-version-v3/。

### 2026-10-29：v3 loader-release a0=1 静态定位（未续调）

固定本机 A17/TWRP qcom-scm.ko SHA256 8932f0206f8a0083af352684cd64de8c9406346dd639606d045136cf27b079a5：__scm_smc_call 在 SMC 返回 x0=1 时进入继续循环，x0=1、原始 x1..x5、quirk 状态 x6、x7=0；与公开 Android Qualcomm SCM 的 QCOM_SCM_INTERRUPTED/A6 行为一致，但并未证明固件和参考完全等价。EDK2 ArmCallSmc 会保存返回 x6，v3 探针只记录 x0..x3 且未续调；原照片没有 x6，不能恢复本次调用，也不能推断最终释放成功。x0=2/3 的 waitqueue 与 SMCI callback 属独立处理。下一候选可在同一上下文有界续调并模拟测试，未知状态保留资源；不对当前设备盲目重试。静态审计退出 0，无设备操作。详情 D:/Projects/UniXNU/migration/20261029-scm-interrupt-audit/REVIEW.md。

### 2026-10-29：m3-smci-resume-v1 — 有界 SCM 中断续调（上机前）

UEFI 默认关闭的续调在对象 SMC 返回 x0=1 时，以原始 x1..x5、前次返回 x6、x7=0、x0=1 在同一调用内继续，最多 8 次；其他未知状态、waitqueue 或超过上限停止并保留资源，不实现 callback/listener。模拟生产流程 46 个向量覆盖 x6 链、上限和保留；22 项主机检查退出 0，56 项源码绑定提交 6427e44aa3017cb5cd7bc5cab3d6556691e70477。镜像 SHA256 b29467074225f3a942ec87577a7df41452a7385309c49cb5ff95f960e13f2a7c。设备成功与完整清理仍待照片；见 D:/Projects/UniXNU/migration/20261029-m3-resume-v1/REVIEW.md。

### 2026-10-29：m3-smci-resume-v1 attempt-01 临时启动（待照片）

12:26 Asia/Shanghai，互斥检查通过；fcde91e、fuxi、unlocked=yes、current-slot=a。镜像 SHA256 b29467074225f3a942ec87577a7df41452a7385309c49cb5ff95f960e13f2a7c，22 项检查与 56 项源码绑定通过。一次 fastboot boot Sending/Booting OKAY，退出 0；无 flash、擦除或槽位修改。续调和对象、桥释放结果待照片，不自动重试；记录 D:/Projects/UniXNU/migration/20261029-m3-resume-v1/attempt-01/device-test.json。

### 2026-10-29：m3-smci-resume-v1 attempt-01 照片 — 查询与释放闭环，续调未触发

IMG_7211..JPG SHA256 8e7020af36e14cf08a153bc188493b68e8fa073d411502aedc3909d28e502814。LUN4 a/b 和 TA extent 固定摘要检查成功；TA load app=4，getVersion a0..a3=0、BO 长 9FFC、原始字段 4/1/4/3F4。app=4、loader=3、环境对象=2 的 release 均 Success；bridge-delete handle=16D82C5E a0..a3=0；BATCH stop=complete passed=FF env=0 loader=0 retained=0，result=Success。桥和 TA 缓冲报告释放，清零仅为源码推断。

本次所有对象调用 resume-count=0、final-a0=0；**实机没有触发 SCM 中断续调分支**，闭环不证明中断时续调成功，也不能解释 v3 的 a0=1。UFS stop=1、db=tdb=run=0、poisoned=0；SMMU FSR=400 残留。未执行 KeyMint 配置/转换、ICE、解密或系统交接。原图、assessment 和 REVIEW 在 D:/Projects/UniXNU/migration/20261029-m3-resume-v1/。

### 2026-10-29：m3-smci-setversion-v1 — 一次 setVersion(200) 受限探针（未上机）

在 getVersion 之后、释放之前增加默认关闭的 `setVersion`：SMCI op0/counts=0424，命令 0x3121 + CBOR map `{11:200}`（字节 a1 0b 18 c8），is64=1；响应必须是已知空输出页形式且 status 为零，异常或未知状态停止并保留桥、loader 与环境对象，不重试。CBOR 字节由本机 libqtikeymint/libqcbor 反汇编推导，未执行厂商编码器。镜像 SHA256 3b9a8810c1c6fa4390ff34f3ba0844138c75be9efbeba74b2db0c47003cf6363；监视器 finalize 退出 0，23 项主机检查退出 0，setVersion 58 个向量及续调 46、查询 40、加载 25、TA 读取 11 个向量在 ASan/UBSan 下通过，56 项源码绑定提交 253b5349ed4b4a5e738e8e5015fd4f2fdeadc246。未上机，现有启动助手白名单不含本镜像；`setVersion` 是否被 TA 接受、是否改变后续 KeyMint 行为均未验证。历史 SMMU FSR=400 未解释。详见 D:/Projects/UniXNU/migration/20261029-m3-setversion-v1/REVIEW.md。

### 2026-10-29：setVersion 本机独立重建，交付检查未完成

UEFI 提交 253b5349，macOS 主机通过 Linux ARM64 Docker 独立构建，固定设备 board
1d071dae，框架 7e237de（工作树报告子模块脏）。镜像 1880064 字节，SHA256
`a4fa6e2e483fcaa2b86053ea110323a314fe393c8e62d6cb45a65ff035594c31`；与 Windows
候选不同，不能沿用其资格。改动层 UEFI，pre-USB 诊断驻留，不做系统交接。

基础镜像检查、13 项记录的主机检查、独立 F2FS mkfs/sload 样本及 setVersion 58、
续调 46、查询 40、加载 25 项 ASan/UBSan 模拟通过。容器首次模拟因 sanitizer 运行库
缺失失败，改在主机完成，后安装容器运行库；保留失败事实。固定 TWRP 的 qcom-scm.ko、
smcinvoke_dlkm.ko、固定参考源码与 keymaster 分区镜像不在本机：bridge ABI、loader
contract、TA 实际读取模拟和最终 UFS 交付检查未完成；交付检查因缺少 bridge ABI 报告
退出 1。没有精确启动资格或实机操作。补齐固定输入并完成全部绑定检查前不得上机。
独立候选记录：ramdisk-test/uefi-twrp-data/out/m3-setversion-macos-20261029/REVIEW.md。

### 2026-10-29：恢复 setVersion 候选审计输入与补检

本机 A16 TWRP 原镜像 SHA256 68f3829b…f87da5，离线提取 qcom-scm.ko 和
smcinvoke_dlkm.ko，分别与固定 A17 参考 SHA256 8932f020…79a5、831a6db7…9348
一致。LineageOS SM8550 modules 固定提交 9e897228 的七个参考文件逐项匹配记录哈希。
设备互斥检查无活动任务；ADB 确认 fcde91e / fuxi / Android 17，su 只读确认
keymaster_a -> sde13、524288 字节。只读导出到主机：原始输出多含 80 字节 dd 状态，
前 524288 字节 SHA256 2fc838a8f0ed572ec77b7db2fe39ab4e81d0aa038bfad482b88087a27bf43cc6
与固定参考一致；保留原输出并另存精确长度副本，不入 Git。无设备写入或启动。

候选 a4fa6e2e…5594c31 补齐对照模块后，19 项顺序交付检查全部退出 0；桥 ABI、
loader 合约、TA 读取 11 向量及 setVersion/续调/getVersion 模拟通过；交付镜像
56 项源码绑定与 SMC 指令检查通过。首次顺序检查因容器缺 OpenSSL 头而退出 1，安装
依赖后重跑通过，失败日志保留。框架工作树仍报告子模块脏；现有启动助手无此镜像的
精确资格，仍未实机验收，SMMU FSR=0x400 未解释。详见候选 REVIEW.md。

### 2026-10-29：本机 setVersion 候选 attempt-01 临时启动（待照片）

19:52 Asia/Shanghai，用户确认设备已进入 fastboot 并授权启动。设备互斥检查通过，
fcde91e、product=fuxi、unlocked=yes、current-slot=a；候选镜像 SHA256
`a4fa6e2e483fcaa2b86053ea110323a314fe393c8e62d6cb45a65ff035594c31`。独立助手
精确绑定此镜像、setVersion 操作序列和 19 项检查，逐项核验 56 个源码与提交
253b5349、交付绑定及 setVersion/续调/getVersion/TA 读取报告。profile usb-ram，
一次 fastboot boot：Sending OKAY、Booting OKAY，退出 0；无 flash 或分区写入。
UEFI pre-USB 诊断，不启动 XNU。照片待判读，主机成功不证明 setVersion、对象/桥释放、
UFS 停止或 SMMU 恢复，不自动重试。原始记录在候选 attempt-01/device-test.json。

### 2026-10-29：本机 setVersion attempt-01 照片——非零状态，资源保留

原图归档 attempt-01/photo-original.jpg，SHA256 96845704610e1ee2c0622a87ba2c2de860a442fe63c7ff0f69c0fff30d89360a。
M2 文件、LUN4 a/b 哈希及 TA extent 固定摘要成功；TA load app=4，getVersion 的
SMC a0..a3=0。set-version 的 SMC a0..a3=0，响应日志 status=FFFFFFD8（按 32 位有符号
值为 -40）、bytes=9FF8、payload=9FF0；状态含义及产生层尚未确定，长度字段不等于
已确认有效载荷。交付源码对非零 status 返回 EFI_DEVICE_ERROR；照片显示
BATCH stop=set-version passed=A3 env=2 loader=3 retained=1、result=Device Error。
源码路径在 app/loader/env release 和 bridge-delete 前跳至 Done；未证实这些释放。
TA extent 缓冲报告 released，擦除仅是源码推断；不重试。

UFS stop=1、db=tdb=run=0、hcs=10F、is=0、poisoned=0；SMMU gfsr=0、fsr=400
残留，不宣称完整恢复。无解密和系统交接。照片后再次通过交付镜像/56 项源码绑定；
19 项顺序检查与候选匹配。下一步离线核对厂商编码器、响应与状态语义，保留保护，
不能凭错误码猜测根因或宣称 setVersion 已成功。详细记录见候选 REVIEW.md。

### 2026-10-29：setVersion 非零状态离线核对

固定本机厂商 `libqtikeymint.so`、A16 TWRP `libqcbor.so` 和 deviceutils 在 Unicorn
隔离模拟：真实 serializeInteger(200) 返回 `a1 0b 18 c8`，与交付源码相同；三库哈希
及篡改拒绝通过。厂商 getVersion 处理字段顺序与照片 4/1/4/3F4 对应的推论下，
实际 isLegacy 函数对 (4,1) 返回 false；对照 4/0、3/0、5/0 返回 1/1/0。独立参考
消息模型的 op/counts、六项 BI/BO 描述符、消息页长及 is64 与当前源码一致；A16
libQSEEComAPI 的发送路径同样提供这些参数。均不证明设备 TA 收到的字节或上下文。

Android 公开 Keymaster 将 -40 命名为 INVALID_TAG，但设备私有固件的错误表及具体原因
未确认；不能以此推断标签错误。未改变诊断固件或放宽保护，未操作设备。新可复现
审计与边界见 UEFI docs/fuxi-m3-setversion-encoder-audit-20261029.md；提交
450bf09c、ea1c4acf 已推送 origin/feat/twrp-data-uefi。继续核对 TA 输入、上下文及
响应路径；在根因明确前不臆造修复或自动重发。

### 2026-10-29：QSEECom 用户态执行与 TA 错误指令只读核对

A16 TWRP 的固定 libQSEEComAPI SHA256 5c996c50…e65ed，Unicorn 隔离执行
QSEECom_send_cmd，模拟 object invoke 捕获参数：op0/counts424、请求8字节、响应
容量9ff8、ELF class2 的 is64=1、四个空输入对象，输出请求/响应指针与输入别名；
class1 对照为 is64=0。固定内核参考在编排消息时复制 BI/BO 为独立区间，公开参考
TZ null=0，与候选源码一致。独立线格式模型六项描述符相符；不证明设备实际调用。

SHA256 2fc838a8…43cc6 的只读 keymaster_a 镜像可执行段扫描 MOVN Wd,#39（-40）
的特定编码出现16次；可执行段可含数据，也有其他编码，尚未将任一位置与照片返回
或可达性关联，因此不能定位 -40 根因。两项脚本检查通过，未改 UEFI/未操作设备。
详见 UEFI docs/fuxi-m3-qsee-send-audit-20261029.md，提交 193bb3bc 已推送。
继续追踪实际 TA 分派与调用上下文；不得忽略非零状态或猜测修改标签。

### 2026-10-29：固定 TA 命令表与 PixelOS OTA 版本路径复核

固定 keymaster_a SHA256 2fc838a8…43cc6，独立 Unicorn 限界执行实际 TA 请求
包装/分派：在初始化桩成功前提下，0x3121 扫描89项命令表未命中，返回响应头
`d8 ff ff ff f0 9f 00 00`，与照片 status=FFFFFFD8/payload=9FF0 一致；0x200、
0x3120 分别命中第19/18项；初始化桩失败则未扫描、返回 -1000。外部函数与 PAC
均模型化，不能证明实际手机的运行控制流，也不能把 0x3120 当作 setVersion。

只读解析本地 PixelOS OTA（zip SHA256 a281cb54…01a9c0，payload 校验启用）：
OTA keymaster.img 409600字节与实机 keymaster_a 前409600字节完全一致，其余为零。
OTA libqtikeymint 与 TWRP 相关函数代码区间相同；隔离模拟厂商 configure 表明，
非 legacy 路径在 setVersion 返回 -40 后仍发 0x2116，并以该发送结果为返回值。
此为厂商流程线索，不能据此放宽本探针的未知失败保留策略。TA 的 0x2116 路径对
OS 版本/补丁参数调用写状态函数，持久性与安全世界副作用尚未充分确认。

OTA system 属性 release=17、system patch=2026-10-01，vendor patch=2026-08-01；
手机当前 vendor/system 是否与该 OTA 完全一致未核验，不能将这些值直接用于调用。
只读 Android 采集工具已准备并用模拟 ADB 验证成功/错误机型拒绝：只收非秘密属性与
四项厂商库 SHA，不写设备。当前 ADB 无设备（手机在诊断镜像），须返回 Android 才能
绑定当前版本；没有构建新上机候选。详见 UEFI docs/fuxi-m3-ta-dispatch-20261029.md，
提交 ec65e411 已推送 origin/feat/twrp-data-uefi。

### 2026-10-30：M3 单块 metadata 明文读取前置条件离线核验

B 线独立审计 `uefi-twrp-data/out/m3-metadata-read-audit-20261030/`，改动为主机
工具与文档；无新固件镜像、SMC、真实密钥输入或设备操作。最新硬件查询基线仍为
m3-date-support-v1；本轮不宣称 M3 通过、装钥/解密或免 USB 重传。

固定 recovery 9aa603b1…2e4b0 的参数流表明：retrieveOrGenerateKey 输出向量再交
wrapped-key export；此前读到的 208 字节 keymaster_key_blob 不能直接当作已恢复
storage key。retrieveKey 另读 encrypted_key/secdiscardable 并有 Keymaster 处理，
辅助流程存在 writeStringToFile；后续 UEFI 必须实现现存密钥只读恢复，拒绝生成、
升级/持久化和未知状态，不照搬 recovery 的写入补救分支。

固定 TA 2fc838a8…43cc6 真实 CBOR/版本指令的合成输入执行确认：存储表示 FBE 标签
设置 +0x1d4 时，局部版本检查提前返回；非 FBE 对照会检查版本/补丁。导出在服务入口
前停止，未伪造导出成功。此结果不证明实际 storage key 的认证属性，也不证明整个
恢复链可跳过 configure；未发送 0x2116。HWKM 本轮初始化/自有槽与清理、六参 SCM
扩展区、UFS 实际提交的 crypto context、可比较状态的明文摘要仍缺失；外层预填
UTRD 的 DUN 会被当前提交函数覆盖。

四项顺序检查退出 0：固定输入审计、6 个二进制篡改拒绝与 13 项反例/执行边界、
生产 DUN 规划器及 UFS 核心 ASan/UBSan。初次主机 Unicorn 在沙箱内初始化 SIGILL，
沙箱外通过；初次 DUN 头文件路径错误退出 1，改用既有顶层 EDK2 后通过，失败记录
保留。无模块源码修改/构建或上机；历史 SMMU FSR400 未解释。下一具体门槛为现存
storage key 只读恢复契约，而非继续追加版本查询；详见 UEFI
`docs/fuxi-m3-metadata-read-prerequisites-20261030.md` 与本轮 REVIEW。

### 2026-10-30：M3 现存 metadata 密钥恢复核心（主机阶段）

B 线 uefi-twrp-data/out/m3-key-recovery-v1：新增独立 UEFI C 库，固定四文件只读
F2FS读取、personalized SHA512/application ID、有界 begin/update/finish/abort。
绑定上下文须调用者独立核验并显式传 seed；没有生产调用入口/真实 KeyMint后端。
finish认证成功且操作关闭前不公开结果；未知完成隔离保留，不重试/清理远端归属。
不生成、升级或持久化密钥；未发送 configure，未构建启动镜像或操作设备，M3未通过。

40项 ASan/UBSan（hashlib摘要和OpenSSL合成GCM独立对照）、固定输入静态审计、
默认/query两套AARCH64模块构建及源码/EFI/编译对象依赖检查退出0。默认两个EFI
SMC数均0；query DataProbe为既有1条INFO，NativeUfs仍0。库无live安全调用。
TA表有210f/2111/2112/2113而无对应25xx；厂商选择涉及SecurityLevel/isLegacy，
命令存在不代替运行时分支/线格式验证。主机成功不是真实KeyMint恢复或解密证据。

下一步核验本机绑定seed与真实后端请求/响应、operation/共享缓冲区关闭，恢复现存
storage key后再接convert/HWKM自有槽/六参SCM/UFS提交层DUN，最后做一次有界
userdata metadata块明文哈希对照。历史SMMU FSR400仍未解释。详见UEFI
docs/fuxi-m3-key-recovery-20261030.md与本轮REVIEW；硬件查询基线仍为date-support-v1。

### 2026-10-30：M3 恢复后端 wire/SMCI 封装（主机阶段）

B线uefi-twrp-data/out/m3-recovery-backend-v1：新增M3KeyMintBackend独立C库，
CBOR编解码、op0/counts424内联报文、TA/transport分层校验及操作归属，接入恢复核心
主机调用链。未提供live交换回调/固件入口、未操作设备或发送configure，M3未通过。

沿A17实际AIDL链确认begin/update/finish发310f/3111/3112，abort经通用TEE路径2113；
固定TA中31xx与对应21xx共用handler。application ID/nonce标签按有符号32位扩展为
负数CBOR label。12请求+3响应厂商/QCBOR合成对照、53项ASan/UBSan及3个独立SMCI
完整报文对照通过；默认/query AARCH64构建、4个实际对象及源码/EFI/SMC检查退出0。
反例修正abort失败时后端未同步隔离的问题；未知完成保留共享页/请求/操作，不重试。

固定recovery绑定对象初始化空state、首次未设置时转state2；未证明实际手机现存
密钥绑定上下文，BindingChecked/SessionVerified均不自动置真。无新FD/boot image，
没有真实TA/GCM、convert/装钥或userdata明文证据。旧查询缓冲区日志不能直接用于
带秘密恢复。下一步核实本机绑定并接无秘密日志的真实交换、验证会话/共享区关闭，
再进convert/HWKM/ICE/DUN/单块明文对照。历史SMMU FSR400未解释。详见UEFI
docs/fuxi-m3-recovery-backend-20261030.md与本轮REVIEW。

### 2026-10-30：本机无seed绑定证据与无日志真实SMC适配

B线uefi-twrp-data/out/m3-secret-exchange-v1。用户确认Android/TWRP后ADB恢复，实际
Android17/fuxi/fcde91e/slot_a；无活动传输。只读取得当前vold（SHA9c20de9c…ed418）并
固定指令布局，只读取两次全局指针/28字节绑定头（总72字节），不跟随seed/vector指针。
PID/starttime/maps稳定，state2且vector空；/data=dm-47/F2FS。空seed选择现在有本机
运行时上下文依据。四包装文件stat大小仍1/16384/208/276，没有读取内容/摘要或秘密。

新增默认关闭M3SecretExchange：真实ArmCallSmc/cache适配、同一SMC最多8次续接、
19命令预算、未知完成保留；已知共享页CPU擦除须WriteBackInvalidate后才撤销权限。
它不释放app/bridge。24项绑定采集器合成、25项Linux ASan/UBSan及默认关闭检查通过；
默认/query/显式enabled三套AARCH64构建、6个EFI与4恢复对象/3模式交换对象绑定通过。
启用对象仅ArmCallSmc及两缓存依赖，关闭对象无依赖，无日志或分配API。

无固件恢复入口/已注册会话调用者，无新FD/boot image、UEFI上机或真实TA/GCM；未重启、
未flash/改slot/写分区命令、未configure，M3未通过。下一步接四文件读取/停队列后的
同次启动恢复入口及会话收尾，再进convert/HWKM/ICE/DUN和单块明文对照。历史SMMU
FSR400未解释。实机非秘密证据docs/evidence/fuxi-m3-android-binding-20261030.json，
接口与限制见UEFI docs/fuxi-m3-secret-exchange-20261030.md及本轮REVIEW。

### 2026-10-30：现存metadata密钥恢复入口 attempt-01（失败，已隔离）

B线uefi-twrp-data/out/m3-key-recovery-live-v1，UEFI恢复/收尾入口及主机工具；
源码35ed8bd1ece7bd5b892dc35028397f4a960bc9d2，已推origin/feat/twrp-data-uefi。
启动镜像SHA256 eb181da061317d1d04eaa4cf6d2812bcd0bd0197c3bb751670bc68585748c2e0，
1884160字节。固定本机state2证据启用空seed；四文件私有读取后，UFS停队列才进
HLOS root/loader/load/version/现存恢复，结果应立即擦除且会话完整关闭。无configure、
setVersion、生成/升级/持久化或ICE/convert/userdata读取。默认关闭恢复开关。

默认/query模块顺序构建、26项完整资格检查全部exit0（15入口/25交换/53后端模拟、
5固定绑定pin反例、既有M0/M1/M2/USB/SMMU/bridge/loader/version/resume及镜像检查）；
75源码与提交/交付镜像绑定，附加实际对象依赖/linked map核验exit0。初次额外map检查
要求已LTO内联函数仍有独立符号而断言失败；改为真实对象引用+保留符号/状态验证，
未改生产逻辑。启用FSE对象仅SMC及两cache依赖。主机模拟不是真实认证证据。

用户进入fastboot后互斥ps无活动任务；fcde91e/product fuxi/unlocked yes/current-slot a。
03:46:02Z以attempt-01/boot-ram-window-once.py --profile usb-ram临时boot固定SHA，
发送/Booting均OKAY，exit0，总2.033秒。无flash/改slot/分区写或USB数据传输。
用户照片归档attempt-01/screen-01.jpg与screen-observation.json；已回填REVIEW。

照片：M2文件/四文件prepare/LUN4双槽hash Success，binding=F2E5A8B4，
UFS stop1/db0/run0/tdb0；getVersion 4/1/4/1012、续接1次。第一条恢复begin即
Not Ready/state5/stage1/km0/bytes0/retained1，fault7/exchange1/commands1/resumes0；
BATCH stop=key-recovery/passed=A3/env2/loader3/retained1。SMCI保留注册页和私有work，
不再调用/擦除/delete/free，最终PARK；poisoned0/ready0。FSR仍400，未解释。

fault7只说明响应边界验证后TaStatus非零，且不是负数或Payload非零；未记录具体
status/长度，km0是未解析默认值，不能说认证成功或推成缺configure。未取得operation
handle/输出，无update/finish/abort或会话清理。下一步只读核对固定vendor/TA错误
envelope，并准备只打印非秘密status/length的新候选；未知状态保持隔离，不复用本会话。
M3/M4/M5均未通过。详见UEFI docs/fuxi-m3-key-recovery-live-20261030.md。

### 2026-10-30：恢复首条begin非秘密状态诊断 v2（资格通过，未上机）

B线out/m3-key-recovery-status-v2，镜像SHA256
c32884767117277ff89beb68b2fe2c4eedb6b7e675805ad0eafaf908320fc83e，1884160字节。
源码f17f2196704dc8b6eb7c14d34b4a977eca5a74f9，76文件与提交/镜像逐项绑定exit0。
在v1 fault7失败后，只增加经过既有边界/envelope校验的公开status/length日志，
不打印payload/密钥/摘要/operation handle，不改变故障分类或隔离/预算/清理门槛。
固定vendor sendCmd在非零status直接返回，只有零status取payload长度；此证据不能
代替v1具体返回码或证明operation已关闭，因此未放宽fault7，也不归因缺configure。

默认/query模块和完整固件顺序构建exit0；27项资格全部exit0，含新增17项生产入口、
55项后端、25项交换ASan/UBSan及实际6对象/链接map/默认关闭依赖检查。新增负status
带payload与正status两组反例保持第一调用后隔离，共享页快照不变、无明文、无后续
调用。模拟仍不是TA/GCM认证。无新设备命令/SMC、configure、flash或分区写。

v1照片及失败观察已回填其REVIEW；新候选REVIEW、独立attempt-01固定SHA/ops/27项
检查/76源码启动脚本均准备好。当前尚未上机，需要人工离开诊断驻留并回fastboot，
重新核对设备身份/解锁/slot_a后临时boot；不复用v1保留会话、不自动重试。
下一实机读取response-header/status/payload-bytes/wire-bytes，才可确定错误码及
后续最小必要条件。历史FSR400未解释，M3/M4/M5均未通过。
