# fuxi UEFI USB Bulk RAM receiver

2026-10-23：接收器原型已实现；主机测试通过，手机 USB 初始化仍在排查。
**尚未完成手机枚举或 1 MiB 传输。当前镜像只停留在 UEFI，不启动 XNU。**

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
接收成功只证明 RAM 中对象完整，尚无 DarwinBoot/XNU 交接功能。

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
