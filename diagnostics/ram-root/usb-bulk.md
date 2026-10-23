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

主机侧仍看不到设备，说明卡在枚举阶段。`speed` 在 2/3 之间跳变
（3 对应 DSTS.CONNECTSPD=4，即超速编码，对 USB2-only 链路不合理），
更像是链路速率上报不稳，而不是真正的 SS 连接。

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

判读方式：出现 `FUXI EP0: arm` 且随后出现 `FUXI EP0: evt=4 …`（XFERCOMPLETE
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
随后 EP0 IN 的数据阶段也完成了（`evt=3 ... state=1`）。
这是枚举第一次真正进入控制传输阶段。

但每次都在**状态阶段**失败：

```
DWC3: STARTTRANSFER failed on Physical EP 0, TrbType 0x40 Len 0 RscIdx 0 Ep0State 2
```

`status 1` 对应 `DEPEVT_TRANSFER_NO_RESOURCE`。原因在 v17 的改动里：
reset 分支只调用 `Dwc3Ep0OutStart()`，没有重新执行 `Dwc3StartConfig()`
（`DEPSTARTCFG` + `SETTRANSFRESOURCE`）——这两步只在
`Dwc3EnableEndpoint(..., Modify=FALSE)` 时发生，而 bus reset 会丢弃
端点的 transfer resource，所以状态阶段拿不到资源。

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
