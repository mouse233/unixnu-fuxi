# APFS RAM 离线模型校准（2026-10-24）

本轮改动位于主机分析工具，不是 UEFI、DarwinBoot、XNU 或打包层修复。
未操作手机，未构建 v30。v29 交付镜像重新计算的 SHA256 仍为
`f9068392e3a1539f1506a6b777dd64c3e9bef6f8f60ee81abd403060445257ef`。
v28 实机的 APFS ENOENT 尚未定位；以下结果不能替代 v29 实机 witness。

## 已修正的模型错误

1. `analyze-apfs-ram-mount.py` 默认 `--container-header=0`。
   超级块从容器块零开始，前 32 字节为标准 `obj_phys_t`，`nx_magic` 在
   `+0x20`。这不是额外的镜像构建头。OS 的设备起点仍为窗口 +17408，
   SystemOS 为窗口 +0。`--container-header=0x20` 仅保留为损坏负对照。
2. 使用完整 ioctl 命令（含方向和数据宽度）匹配 v29 `mdevioctl`：
   `0x40046418` 是 GETBLOCKSIZE（初始 512），`0x40086419` 是
   GETBLOCKCOUNT（容量 / 当前扇区大小，向上取整）。此前把前者当作
   ISVIRTUAL 返回 1、后者返回字节容量，导致 APFS 按 1 字节块寻址。
   `0x40046448` 才是 ISVIRTUAL，实际 md 驱动不支持，返回 ENOTTY，
   不写输出。旧模型在这项 4 字节查询中写入了 12 字节错误几何信息。
   只读属性、SETBLOCKSIZE、四个最大 blocks/segments 查询也与真实代码对齐。
3. VBLK `buf_getblk` 的块号原样保存在 `b_blkno`，md strategy 按
   `b_blkno * mdSecsize` 寻址。修正旧模型的 `块号 * 本次传输长度`。
   报告分别记录 block_number、sector_size、byte_offset、size。
4. Darwin arm64 `va_list` 是栈参数指针，APFS 原始日志包装器在
   `0xfffffff0056882ac` 把 `x29+0x10` 传入 vprintf。旧模型套用了
   非 Darwin 的寄存器保存区结构；修正后能读取原始 dev_init/nx_mount 日志。
5. `kern_apfs_reflock_wait_for_unlock` 缺少状态/调度模型，现在明确停止，
   不再把它作为 no-op 引发循环。未完成报告为 `completed=false`、
   `return_value=null`，保留 stopped_x0 并退出 2，不能将中途寄存器当 errno。
   报告记录分析脚本、基类脚本、内核及相关 XNU 源码 SHA256；写入使用临时
   文件再替换，默认只打印简报。`mdXsY` 对照明确标记是假设节点。

格式参考：[Apple File System Reference](https://developer.apple.com/support/downloads/Apple-File-System-Reference.pdf)
第 10、27 页（obj_phys_t / nx_superblock_t）。

## 独立接口验证

`scripts/check-apfs-ram-model.py` 从本次 v29 的实际构建树
`out/usb-system-mount-v29/work/tree/.cache/xnu-work` 抽取原始 `mdevioctl`、
`mdevbytes`、结构及 ioctl 定义，使用主机 clang + ASan/UBSan 编译。
以完整输出缓冲区和两侧 canary 比较真实 C 与实际 Python VNOP_IOCTL hook，
覆盖两个盘容量、512/4096 扇区、SETBLOCKSIZE 成功/拒绝、错误长度/组号，
共 68 组，全部通过。另执行实际 getblk hook，验证同一块号在改变读取长度后
起点不变，以及 512/4096 扇区的等价地址；检查越界拒绝、Darwin 日志解码和
两个真实容器的 object header、xid、容量。该检查不执行 APFS 挂载。

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/unicorn-env/bin/python3 \
  scripts/check-apfs-ram-model.py --output out/usb-system-mount-v29 \
  --image /Volumes/External/VSCode/UniXNU/unixnu-source/out/fuxi/userdata/userdata-root.img \
  --report out/analysis/codex-takeover/model-tests.json
```

Unicorn 2.1.4 的 JIT 在 Codex macOS 沙箱内分配模拟内存时退出 132；
纯 Python/C 对照不受影响。以下原始 ARM64 执行经沙箱外运行验证，不访问手机。

## 原始 APFS 的有界对照

固定原始 APFS 可执行段 SHA256：
`3192441dae74abc0d82e9069447194934598c1c63d2dacba0fb8599eef9996d7`。
完整 JSON 位于 `out/analysis/codex-takeover/`：

| 报告 | 观察 | 边界 |
|---|---|---|
| md1.json | 完成 58 次有界元数据读取 | 停在未建模 reflock wait，退出 2 |
| md1s1.json | 假设别名，完成同样 58 次读取 | 同一模型边界，不能证明别名修复有效 |
| md2.json | 完成 38 次有界元数据读取 | 同一模型边界，退出 2 |
| md1-bias20.json | 只读块零后回调返回 EFTYPE(79) | 人为损坏的负对照，退出 0 表示模拟完成 |
| parser.json | 原始设备名解析器的 7 组输入 | 仅 strlen/strlcpy 模型，不执行挂载 |

复现一次标准 md1 对照：

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/unicorn-env/bin/python3 \
  scripts/analyze-apfs-ram-mount.py \
  --kernel out/usb-system-mount-v29/work/kernel/kernel.macho \
  --base ../unixnu-source \
  --image /Volumes/External/VSCode/UniXNU/unixnu-source/out/fuxi/userdata/userdata-root.img \
  --device /dev/md1 --report out/analysis/codex-takeover/md1.json
```

md1 第二次读取现在为设备块号 `0x1ee80`、512 字节扇区，字节位置
`0x3dd0000`（即 APFS checkpoint 块 `0x3dd0 * 4096`），读取 4096 字节。
旧模型将 `0x3dd0000` 再乘读取长度，制造 `0x3dd0000000` 越界。
这解释的是离线模拟器的假故障，不解释实机 ENOENT。

两个真实块零按标准对象布局解析：OS oid=1、xid=0x1a2，容量
6,077,546,496 字节；SystemOS oid=1、xid=0xe，容量 3,303,014,400 字节。
加 0x20 后校验器读取的 magic 实际为 `0x00000002`，不是 NXSB。

## 下一步及未验证边界

设备名解析器的 `/dev/md1 -> -1`、`/dev/md1s1 -> 0` 仍是线索。
本次两个名称都在容器初始化期间遇到相同模型边界，尚未验证最终卷选择。
不能据此新增真实别名、绕过检查或宣称修复 ENOENT。

锁、分配器、线程、IOKit 与 buffer cache 仍是近似模型；hw.memsize 等值是
分析假设，不是实机测量。`completed=true` 仅表示原始回调在模型中返回，
不是实机挂载完成。本轮未继续扩展整个内核的模拟环境。

下一项能区分实际失败路径的证据是原 v29 的 open/count/path 与 LOG tail。
按其 REVIEW.md 检查设备互斥、镜像/验证报告绑定、fastboot serial 后，
只临时启动一次并收集完整屏幕，再决定是否需要新的 XNU 候选。
UEFI、DarwinBoot、APFS 原始可执行区和交付镜像均未改；主机四项交付检查
无需因纯分析工具更改重新生成，但任何后续上机仍必须通过既有启动门。
