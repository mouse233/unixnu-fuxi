# v27：定位 RAM 挂载 EBUSY 的阶段诊断

日期：2026-10-24。父候选 v26 的 IMG_6957.HEIC 显示首次挂载
`RAM_SYSTEM APFS mount failed id=1 errno=16`；仅凭 errno 不能判断是否进入 APFS。
本候选用于取得具体失败阶段，尚未认定根因或宣称挂载修复。

## 改动

新增 XNU 补丁 `zz-ram-system-mount-trace.patch`，在 v26 的 SPI 补丁之后应用。
仅 RAM 专用调用更新诊断状态；内核启动探针串行执行，普通挂载不更新该状态。
记录挂载点查找/准备、设备查找/授权/引用、setmounting、vcount、flush、open、
APFS 回调及挂载提交阶段。有效的 RAM 专用调用失败时，先完成 kernel_mount
原有清理，再由 fuxi_ram_mount_ro 停在带诊断的 panic；原探针本来也会在错误时 panic。
不保留 vnode 指针，不跳过占用或授权检查，不改清理、只读条件或文件系统回调参数。

故障屏幕格式：

```text
RAM_SYSTEM mount failed errno=16
at=<stage> path=/mnt1
dev=<hex> opens=<count> spec=<hex>
apfs=<result> covered=<hex>
```

- `at=covered-busy`：挂载点竞争检查返回；covered 是当时的 vnode flags。
- `at=device-setmounting`：设备或别名已挂载检查返回；spec 是清理前的设备 flags。
- `at=device-vcount`：原占用判断返回；opens 是原 vcount 的一次实际计算结果，包含别名。
- `at=apfs-mount` 且 `apfs=16`：APFS 的 VFS_MOUNT 调用确实返回 EBUSY。
- `at=mount-list-add` 且 `apfs=0`：APFS 已返回成功，后续提交失败。

opens=-1 表示尚未执行 vcount；apfs=-1 表示尚未调用 APFS。
dev=ffffffff 表示设备号尚未取得。spec 的 SI_MOUNTEDON=1、SI_ALIASED=2、
SI_MOUNTING=4；快照仅提供观测，不代表已列出所有别名。
其他阶段直接按名称定位源码。该诊断不提供 APFS 内部详细错误栈。

本次修改在 XNU。UEFI 源码、USB 协议、对象布局、DarwinBoot 交接和只读盘
实现不变；固件仍按规定在 Linux Docker 中重建和封装。

## 交付镜像与主机验证

- 输出：`out/usb-system-mount-v27/`，由 v26 APFS clone 创建独立缓存。
- 镜像：`Mu-fuxi-usb-ram.img`，203,190,272 字节。
- SHA256：`d48f32848b9f1da0b469b001dbbe2365430061ad27409ed7f30d1ef362d6328d`。
- prelink：`cd451c0c6dd4099ce21101e6d6541155aebad79d4bde1c8d7a33a097610103b1`。
- FUXIPAYL、kernel base `0xfffffff00427c000` 与 skew `0x7c000` 已验证。

四项必需检查退出码均为 0：SMMU audit 14 项、route 20 项、RAM 协议 21 项、
交付镜像/模块检查。payload/ADT/kernel/trust/data 检查、KMEM 七项回归、
挂载 SPI 回归和八种启动门情况也通过；KMEM 余量下界仍为 662,548,480 字节。

新增 check-usb-system-mount-trace.py：从真实 vfs_syscalls.c 抽取挂载点准备、
设备 preflight、APFS 调用点和提交检查，在 ASan/UBSan 下运行 13 个故障注入，
另验证成功路径和普通挂载隔离。故意清除设备 flags 并释放 vnode 后，再验证
panic 仍保留清理前的设备号、计数、flags、APFS 返回值和正确阶段。
检查完整错误清理代码与 v26 字节一致，补丁可反向核验，VFS 编译记录来自本候选，
交付 prelink 包含诊断格式串和关键阶段字符串。报告绑定镜像和内核 SHA256。
这是主机局部 C 验证，未模拟 APFS，也不代替真机验收。

```sh
python3 scripts/check-usb-system-mount-trace.py \
  --output out/usb-system-mount-v27 --base ../unixnu-source \
  --parent out/usb-system-mount-v26
```

check-usb-system-mount-spi.py 在临时副本中撤销后续 trace 补丁，再核验 SPI 补丁，
并继续测试实际 v27 的入口保护与地址空间选择；不会改动已构建的候选源码。
候选准备脚本也会清除父候选的 mount-trace-tests.json，避免继承过期结果。

## 实机验收

尚未上机。再次核对无传输任务与 fcde91e 后，只做一次临时启动：

```sh
DYLD_LIBRARY_PATH=/opt/homebrew/lib PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/test-usb-ram-full.py \
  --image out/usb-system-mount-v27/Mu-fuxi-usb-ram.img \
  --expected-sha256 d48f32848b9f1da0b469b001dbbe2365430061ad27409ed7f30d1ef362d6328d \
  --research-image /Volumes/External/VSCode/UniXNU/unixnu-source/out/fuxi/userdata/userdata-root.img \
  --boot-profile usb-system --output out/usb-system-mount-v27/attempt-01
```

双对象 END RAM 校验后，手动选 `fuxi RAM diagnostic: system-mount`。
取得完整故障屏幕（尤其 at/dev/opens/spec/apfs/covered）并归档，之后才按阶段修复。
若显示 RAM_SYSTEM PASS，同样归档；不能把传输通过当作两个 APFS 卷挂载通过。
控制器停机三标记仍须照片，normal_controller_stop_verified 不能仅凭 USB 消失改为 true。
