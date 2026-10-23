# Command Code 会话审计与下一步（2026-10-23）

审计对象：`189ecc19-576d-40c0-9f30-6fb45bc8117c`，690 行 JSONL。
来源路径、记录与照片哈希见 [commandcode-evidence.json](commandcode-evidence.json)。
实际工作区是 `ramdisk-test/`，三仓库基点分别为设备 `e322244`、
UniXNU `3eb5c93`、UEFI `94d5c3af`；审计开始时均干净。

## 路线判断

**继续完整 OS/SystemOS RAM 根盘路线合理，先验证更大窗口也合理。**
恢复盘已证明 md0/APFS、原始签名信任链、dyld、launchd main 和子进程能够运行。
`restored_external` 等待 AppleNANDStatus 是恢复用户态依赖的边界，继续补这个
服务并不等于启动普通系统。Apple ANS/NVMe 驱动也不是高通 UFS 控制器驱动。
不引入物理存储驱动、保留 RAM 根盘与调用保护，符合这条实验路线。

P0 的两个窗口大小与 SHA256 已重新从本地 SHNGROOT 镜像核验，匹配原报告：
OS 6,077,580,800 字节，SystemOS 3,303,014,400 字节，合计 8.736 GiB。
本轮没有重新挂载卷，卷 UUID/目录结构仍引用上一轮的只读挂载记录。

## 需要修正的结论

| 严重度 | 发现 | 证据与影响 |
| --- | --- | --- |
| P1 | 把 6 GiB 启动外推为整个 15.43 GiB bank 可用 | JSONL 第 675 行的原照片支持 `main=PASS`、`pages=0x16b07b` 及 free 计数。分配器要求真实 conventional 连续段，因此可支持从高位段分配了 6 GiB 的推断；没有整段测试或完整地址覆盖证据。不能据此宣布全 bank 通过。 |
| P1 | P1 没实现最初提出的读写校验 | 实际改动只有窗口参数、内存表和构建修复。页数是启动证据，不是全内存读写或地址别名测试。 |
| P1 | 10.274 GiB 是预算，不是完整 OS 已能启动的证明 | 1 GiB Data 和 5% 开销是估算；传输暂存、解压、驱动复制与正常用户态峰值尚未计实。12 GiB 候选提供约 1.726 GiB 估算余量，但仍须实测。 |
| P2 | 证据和验证报告混用 | `mem6g-verification.json` 的硬件描述仍说 v5 pending；旧 high boot helper 只核对封装 JSON，未消费完整回读报告。新候选必须产生独立、绑定镜像哈希的验证记录。 |
| P2 | 复现链依赖未提交的构建脚本 | OS 版本/补丁日期通过 out 目录脚本临时追加到 `MKBOOTIMG_FLAGS`，并未进入之前的 bootimg 修复。现已补入实际模块；DarwinBoot 改动另存可应用补丁，避免只留在嵌套仓库中。 |
| P2 | 照片被误标为未归档 | 第 675 行原图仍在 JSONL 中，现已提取到 `out/ram-progress/attempt-07/photo-original.jpg` 并登记哈希。照片 pid 为 6，旧转录为 5；exec 行的 `e=-9999` 是未记录完成结果，不能解读为成功或 ENOENT。 |

固件表“把 bank 放最后就能保证固件池在低地址”的注释也不是分配器保证；
本轮不依赖该假设，只探测 `AllocatePages` 已成功分配并归 loader 独占的范围。

## 已执行的下一步

1. DarwinBoot 与 fuxi ADT/CLI/检查器的解析上限统一到 16 GiB。
   上限只控制输入合法性；实际仍必须申请一个足够大的 conventional 连续段。
   不添加预留区，不扩大已发布的高位 bank。
2. 对超过旧 8 GiB 上限的请求，在装入内核前强制抽样校验。
   每 2 MiB 一个 64 位字，并检查窗口末尾；12 GiB 共 6,145 个位置。
   先全部写地址标签、再全部读回，再做反码一轮，并恢复原值。
   每次读写经 cache clean/invalidate，错配拒绝交接。
   屏幕与日志输出实际 PA、size、失败地址或 PASS。
   **这是抽样检查，不是逐页或全量硬件压力测试。**
3. 构建独立 `out/ram-mem12g/` 候选，保留所有旧镜像。
   最终 FAT 中的内核（含渲染器）、恢复盘、原始信任缓存必须与已测 6 GiB 镜像逐字节一致。
4. 从交付 boot 镜像解出 FD，再解开 LZMA DXE 卷，核对实际 DarwinBoot 可执行文件；
   核对 ADT 12 GiB、stock OS 元数据、所有 RAM 根盘参数与精确镜像哈希。
   报告的硬件结果保持 pending，不继承旧版本的通过结论。

候选：`out/ram-mem12g/Mu-fuxi-xnu-ram-mem12g.img`。
主机核验后，设备重新进入 fastboot，已完成本候选的临时启动命令；
仍待完整屏幕证据，不能据命令成功宣布 12 GiB 硬件通过。

## 复现与验收

DarwinBoot 基点为 `d099354d0eab34df5088ca1ba89c9c88bc3260bb`，
可用同目录 `large-window-darwinboot.patch` 重现 loader 改动。

在已有 arm64 Linux 构建容器中，工作目录为设备仓库：

```sh
python3 scripts/build-ram-window-candidate.py --phase firmware \
  --base ../unixnu-source --uefi ../uefi-fuxi \
  --edk2 /work/uefi-fuxi/Mu_Basecore \
  --baseline out/ram-progress --output out/ram-mem12g
```

在 Mac 上使用与旧版渲染器一致的 Homebrew LLVM（Apple clang 的输出超出代码洞）：

```sh
PATH=/opt/homebrew/opt/llvm/bin:$PATH python3 scripts/build-ram-window-candidate.py \
  --phase payload --base ../unixnu-source --uefi ../uefi-fuxi \
  --baseline out/ram-progress --output out/ram-mem12g --unique-chip-id <原有种子>
python3 ../uefi-fuxi/tools/package_fuxi_xnu_boot.py \
  --boot out/ram-mem12g/base.img --payload out/ram-mem12g/work/payload/payload.img \
  --size-mib 192 --output out/ram-mem12g/Mu-fuxi-xnu-ram-mem12g.img
python3 scripts/check-ram-window-candidate.py \
  --image out/ram-mem12g/Mu-fuxi-xnu-ram-mem12g.img \
  --baseline-image out/ram-progress/Mu-fuxi-xnu-ram-mem6g-high.img \
  --base ../unixnu-source
```

真机必须同时满足：本候选的临时启动记录、无 MEM FAIL、XNU/launchd 继续运行，
页数达到约 12 GiB 窗口对应规模。若停在 MEM TEST/MEM FAIL/NO WINDOW，保留完整照片；
黑屏或重启仍是不确定结果，不能直接宣布高位 RAM 不可用。

通过之后优先处理 P2：查清 `max-download-size`，确定无需写 userdata 的镜像传输路径，
并计入传输暂存峰值；再实现 RAM 块设备和只读 OS/SystemOS 挂载。
现在尚未解决 8.74 GiB 镜像传输、cryptex 挂载或普通系统启动。

## 候选核验结果

- SHA256：`1c1885ecd267345f3ddcb0617b962b12cd47ad6e3fcb6eb884a9ef000092a3fa`，203,169,792 字节。
- 固件、DXE 解压与实际 loader 一致性、FAT 回读及旧内核/恢复盘/信任缓存一致性均通过。
- 主机边界测试及带 ASan/UBSan 的正常/别名/固定比特/恢复测试通过。
- 核验记录：`out/ram-mem12g/verification.json`，硬件状态为 pending。
- 启动入口：`python3 out/ram-mem12g/boot-once.py`；实现位于受版本控制的
  `scripts/boot-ram-window-once.py`，读取本镜像的验证报告并固定哈希，且只调用临时 boot。

## 本轮设备命令记录

2026-10-23 16:36（Asia/Shanghai），`fcde91e`，slot b：发送 OKAY 4.213 秒、
Booting OKAY 1.646 秒，总计 5.938 秒。没有 flash、擦除或切换槽位。
完整记录在 `out/ram-mem12g/device-test.json`，硬件结论仍为 pending。

P2 新证据：`max-download-size: 805306368`，即 **768 MiB**。当前 stock fastboot
单次下载无法装入 8.74 GiB 的原始窗口；单纯放宽 ADT 或 boot v3 字段并不能
解决这个限制。需先落实可用的分块 RAM 传输、USB/外部只读介质等路径，再做完整 OS。

原图的 free 低位数字没有作为本审计独立认证的数据；`0x1635ab` 保留为旧转录。
启动与窗口规模判断使用更清晰的 pages 行、已交付 ADT 与分配代码。

## attempt-08 补充：已进入 XNU，早期页表容量不足

新照片出现绿/蓝条，没有紫条；LockCapsuleInterface 是退出 Boot Services 的残留日志。
12 GiB 尚未通过 XNU 初始化。已确认旧 16 页 bootstrap pool 无法容纳本轮需要的
27 页，源码改为 36 页且真实 ARM64 汇编宏的六个回归场景通过；修复尚未重建进
候选内核。记录与 USB 传输方案见 [uefi-ram-transfer.md](uefi-ram-transfer.md)。
