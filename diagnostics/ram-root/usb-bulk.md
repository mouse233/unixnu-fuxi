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
