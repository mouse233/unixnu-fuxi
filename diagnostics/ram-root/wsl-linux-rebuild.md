# WSL Linux 独立重建准备（2026-10-26）

有效工作区 /home/mouse/work/UniXNU；Windows D:\Projects\UniXNU 保留工具和报告。
本轮补齐交接缺失输入，候选为 out/usb-system-mount-v48-linux。
父 v47 只提供配置记录，没有交付镜像、work/cache；新候选不继承历史通过状态。

## 已恢复输入

完整 IPSW 6,835,124,840 字节，SHA256
c84ad24b6981a859eec499b1802be9ac593acfd3dc0a185a76d06e4d767a1451。
全 ZIP CRC、Apple BuildManifest SHA384、既定 restore/trust SHA256 均通过。
原始 launchd/dyld 位于 ../inputs/original-executables-21H461，APFS 只读提取，
哈希与 v47 pins 相同，未挂载或执行。

RAM OS/SystemOS 载荷在根 build-support/linux-system-carrier/fuxi/userdata/，
9,382,658,048 字节，SHA256
fd43ee7035ce185ce4215ac93fdc006fd784a54b3b7442e21457c226cbe03093。
两窗口哈希与历史记录相同；没有访问设备或写入分区。

## 选择性上游整合

实验 source 分支从 a89eaa1 引入 6dc20eb、b0fe495、c8057bb、0f85bc2，
本地对应 f69ea94、2c1615e、5b362b3、01e3168，已推送 mouse233 fork。
不需要整合整个 main：本地 RAM-root/user-entry/main-entry 编译宏与对象保护保留。
DarwinBoot 恢复补丁与整合前完整 diff 逐字节一致。UDIF 六项测试通过。
Linux xcrun 另补实验 ABI 审查需要的 dwarfdump 名称映射，已用真实 ARM64 iOS
Mach-O 的类布局及虚方法 DWARF 验证。两个交付检查器使用现有 llvm_tool 解析器，
不再硬编码 Homebrew 工具路径；实际交付对象检查待新镜像完成。

## 独立构建与边界

另建 out/usb-system-reference-linux-v1 作为新编译的存储 ABI 参考，替代缺失的
v30 内核输入；不称其为历史 v30 重现。两组新工作树已准备，固定 Apple refs 未变。
根 build-support/linux-source-controls 下回归对照由固定 XNU commit 与补丁快照
1bcb2fb 重建，并显式记录 feature exclusions。它们仅含源码、未编译、无实机结论。
早期补丁提交时缺漏基础 plumbing，不能仅按早期 HEAD 冒充当时完整工作树。

UEFI/DarwinBoot 已在既有 Docker 容器独立试构建成功，输出在根
migration/20261026/firmware-smoke；含 dummy ramdisk，不能作为系统载荷交付。
完整 EDK2 包解析还需要 mipisyst 370b5944 和 libspdm f55cf6d4 的头文件，已补齐。
工具链安装到 /opt/unixnu-linux；当下仍在推进剩余工具与完整内核构建。
新候选必须重新推导窗口 skew、正规 FUXIPAYL 打包并跑完 18 项检查。
当前没有新交付镜像或新实机结论；v47 待测和 v46 watchdog prepare errno=6 边界保留。

## 存储故障恢复

宿主 D 盘耗尽导致 WSL I/O/Bus error；释放空间并重启 Ubuntu 后 EXT4 日志恢复，
根目录 rw、账号解析正常，dpkg --audit/apt-get check/dpkg -V apt 均通过。
没有重装发行版或执行离线全盘 fsck。旧 journal 已轮换，不声称逐块文件系统检查完成。
大任务同时监测 /mnt/d 宿主余量，低于 12 GiB 停止自身任务，不能只读 ext4 虚拟容量。
原压缩包保留且另存 C 盘校验相同副本。根 WSL-STORAGE-RECOVERY-20261026.md
和 migration/20261026 保留详细恢复证据与失败构建日志。
