# v29：保留 APFS 日志与文件打开路径证据

日期：2026-10-24。v28 的 IMG_6959.HEIC 已归档，显示首次挂载
errno=2、at=apfs-mount、path=/mnt1、dev=3000001、opens=0、spec=4、
apfs=2、covered=84800，位置 @vfs_syscalls.c:424。
这确认 APFS 返回 ENOENT；/mnt1 失败，/mnt2 未尝试，未达到 RAM_SYSTEM PASS。
不能直接把 ENOENT 解释成 /mnt1 或 /dev/md1 不存在，因为 VFS 前置查找已通过。

只读检查原始 APFS 的普通卷路径发现：它会处理设备名、再次 vnode_open，
然后查找容器/卷及 IOMedia。尚未取得确切缺失对象的运行时证据，不能直接认定
需要增加 IOMedia 发布，更不能跳过原有资格检查。v29 是诊断候选，不是挂载修复。

## 改动（XNU）

新增 zz-ram-system-mount-witness.patch，在 v28 参数补丁后应用：

- 仅 RAM 专用 VFS_MOUNT 前后记录内核 legacy msgbuf 的写入位置，保存最多
  640 字节尾部；读环形缓冲区时使用原 bsd_log 锁，校验 magic、指针、尺寸与索引。
  不改 printf、vprintf、os_log 或原始 APFS。原 APFS 的日志包装器调用 vprintf，
  XNU 的现有日志路径会写入 msgbuf。
- 回调期间用原子 owner 指针限定为挂载线程，记录 vnode_open 返回的路径、错误
  和次数；优先保留失败结果，后续成功不覆盖错误。其他线程和回调外不记录。
  vnode_open 原控制流、输入、错误码和引用清理均保留。
- 失败 panic 增加 `open=<errno> count=<n> <path>` 与 `LOG tail (bounded)`。
  仅复制标量和字符串，回调结束就清除 owner；不保留 vnode 引用。

保留 v28 的只读 APFS mode=1、设备/路径限制、kernel_task 限制和所有 VFS/APFS
检查；挂载错误清理与 v28 字节一致。UEFI、DarwinBoot、USB、对象布局与 APFS
原始可执行区不变。未增加物理存储访问或更改挂载语义。

诊断边界：legacy 日志是共享缓冲区，可能含其他线程消息、较早行可能被覆盖，
日志也可能缺失。恰好整圈覆盖与零输出无法仅由写指针区分。超过尾部容量时可能
从一行中间开始。open=-1/count=0 表示此线程未观察到 vnode_open 调用，不表示
APFS 没有通过其他接口查找对象。路径最多保留 79 字节。完整新屏幕才是下一证据。

## 交付与主机验证

- 输出：out/usb-system-mount-v29，由 v28 克隆独立缓存。
- 镜像：Mu-fuxi-usb-ram.img，203,190,272 字节。
- SHA256：f9068392e3a1539f1506a6b777dd64c3e9bef6f8f60ee81abd403060445257ef。
- prelink：ca42c99f8ab30cf5bb2dbdef99fb06619c145f9e85b1c54e923a7ee9c29fa7b2。
- FUXIPAYL、kernel base 0xfffffff00427c000 与 skew 0x7c000 已重新核验。

内核在 macOS/Xcode 构建，固件在指定 Linux Docker 容器内构建，再由 packager
封装交付。四项必需检查全部退出 0；payload、KMEM、SPI、trace、APFS 参数和
八种启动门情况继续通过，KMEM 余量下界 662,548,480 字节。

新增 check-usb-system-mount-witness.py 在 ASan/UBSan 下执行真实抽取的采集代码：
测试回调范围、线程隔离、先失败后成功、环形回绕、640 字节截断、NUL 结束、
控制字符过滤、格式串按普通数据保存、缓冲区缺失/变化、普通挂载与前置失败隔离。
核对 vnode_open 去掉唯一记录调用后与 v28 完全一致；核对 begin/end 紧邻原始
VFS_MOUNT，两个实际编译记录属于本候选，交付 prelink 含诊断格式串。
报告绑定本次镜像和内核。这些是主机诊断机制检查，不执行 APFS 挂载。

```sh
python3 scripts/check-usb-system-mount-witness.py \
  --output out/usb-system-mount-v29 --base ../unixnu-source \
  --parent out/usb-system-mount-v28
```

报告为 mount-witness-tests.json。SPI/trace/参数检查共用临时副本撤销后续补丁的
检查函数，已构建源码不改。参数负对照仍明确使用 v27（新增 --baseline 可覆盖），
不能误把已经修正的 v28 当作空参数负对照。准备脚本清除继承的 witness 报告。

## 实机验收（待测试）

等待手机进入 fastboot；再次确认无活动传输、fcde91e、镜像与七份交付报告
（verification/payload/KMEM/SPI/trace/volume-args/witness）的 SHA256 绑定。
仅执行一次临时启动，不写分区：

```sh
DYLD_LIBRARY_PATH=/opt/homebrew/lib PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/test-usb-ram-full.py \
  --image out/usb-system-mount-v29/Mu-fuxi-usb-ram.img \
  --expected-sha256 f9068392e3a1539f1506a6b777dd64c3e9bef6f8f60ee81abd403060445257ef \
  --research-image /Volumes/External/VSCode/UniXNU/unixnu-source/out/fuxi/userdata/userdata-root.img \
  --boot-profile usb-system --output out/usb-system-mount-v29/attempt-01
```

双对象 END RAM 校验后，手动选择 fuxi RAM diagnostic: system-mount。
拍完整故障屏幕，保留 at/dev/opens/spec/apfs/covered，尤其新增 open 与 LOG tail。
据实际路径/原始日志决定后续修复。两卷只读挂载和 RAM_SYSTEM PASS 仍待实机，
控制器停机三标记同样须照片；本候选目前不宣称解决 ENOENT。
