# v28：补齐 APFS 附加只读卷挂载参数

日期：2026-10-24。父候选 v27 的 IMG_6958.HEIC 明确显示：

```text
RAM_SYSTEM mount failed errno=16
at=apfs-mount path=/mnt1
dev=3000001 opens=0 spec=4
apfs=16 covered=84800 @vfs_syscalls.c:394
```

APFS VFS_MOUNT 回调确实返回 EBUSY，前置 VFS 检查已通过。opens=0 是打开设备
前的 vcount，spec=4 是 SI_MOUNTING；不能当成设备已被其他挂载占用。
/mnt1 失败，/mnt2 未尝试，照片不显示 RAM_SYSTEM PASS。
原图、PNG、photo-observation.json 已归档到 v27/attempt-01。

## 已确认的参数错误与证据边界

v27 的 RAM 专用处理把 fsmountargs 设为 USER_ADDR_NULL。这份 21H461 APFS
遇到 NULL 会创建默认参数，mount_mode 为 0，进入启动根卷路径；当前已有 md0 根卷。
这与请求附加的 /mnt1、/mnt2 只读挂载不符。

对实际交付内核中的原始 APFS 做只读审计：apfs_vfsop_mount 位于
0xfffffff0056c5fac，非空参数时通过 copyin 读取 fspec 后的 0x144（324）字节；
mode 在这段参数的偏移 8，uid/gid 在 0x13c/0x140。模式跳转表确认
mode 0→0xfffffff0056c67dc（启动根卷），mode 1→0xfffffff0056c66f0（普通卷）。
根卷路径调用 container_rootmount；其容器资格拒绝包含返回 EBUSY 的分支。
以上是原始代码的静态证据，未在实机取得 APFS 内部断点，不能断言已经覆盖其
全部 EBUSY 来源，也不能用这些主机结果宣称 v28 挂载成功。

原始输入 kernelcache SHA256 为
f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7；
APFS __TEXT_EXEC SHA256 为
3192441dae74abc0d82e9069447194934598c1c63d2dacba0fb8599eef9996d7。
审计地址仅适用于该版本及当前 -16 MiB slide，不能推广到别的 APFS。

## 修正范围（XNU）

新增 zz-ram-system-mount-volume-args.patch，在 v27 trace 补丁后应用：

- RAM 入口构造 fspec 后的固定 324 字节 APFS 参数，mount_flags=MNT_RDONLY、
  mode=1、uid/gid=99，其他可选字段全为零。静态断言检查大小与偏移。
- 解析设备路径后，fsmountargs 前进一个指针，保留后面的参数，不再置空。
- 专用入口要求 current_task()==kernel_task，继续严格配对 md1/mnt1、md2/mnt2。
  ARM64 copyin 本身仍核对当前线程 map；仅 kernel pmap 走原 EXDEV→copyin_kern
  分支。没有改 copyin、切换 map，或让普通用户态读取内核地址。

原 VFS 占用、权限、只读检查和错误清理不变；APFS 原始代码与资格检查不变。
保留 v25 的 compressor 修正、v26 SPI 和 v27 阶段诊断。UEFI、DarwinBoot、
USB 协议与对象布局不改，固件按规定在 Linux Docker 重建和封装。

## 交付与主机检查

- 目录：out/usb-system-mount-v28，由 v27 克隆独立缓存后构建。
- 镜像：Mu-fuxi-usb-ram.img，203,190,272 字节。
- SHA256：836bd4bb609dafdda7a0347ea46078459143de6c5b0c1fe7506393c5354b16b3。
- prelink：9c1a1f44085953d138a08fe9d7aa5299a15a2b546870e328c4d962d52f4957ac。
- FUXIPAYL、kernel base 0xfffffff00427c000 与 skew 0x7c000 已重新核验。

四项必需检查全部退出 0；payload/ADT、KMEM、挂载 SPI、阶段诊断、八种启动门
情况也通过。KMEM 余量下界仍为 662,548,480 字节。13 个阶段故障注入和
成功/普通挂载隔离继续通过，错误清理代码与 v27 字节一致。

新增 check-usb-system-mount-volume-args.py，ASan/UBSan 下执行真实 XNU
wrapper、RAM 参数解析、ARM64 copy_validate/copyin/copyin_kern 的抽取 C：
核对两组设备/路径的真实参数长度、mode/只读/uid/gid/保留字节；父候选真实
解析块作为负对照，复现将 APFS 参数置空；验证 kernel map 复制及用户 map
越界拒绝、非 kernel_task 与交叉路径拒绝。系统调用和地址空间环境用主机 stub，
不是完整内核运行。另按固定哈希核验 APFS 原始可执行区未变，验证参数长度指令、
mode 读取指令和实际跳转表。报告绑定本次镜像与内核。

```sh
python3 scripts/check-usb-system-mount-volume-args.py \
  --output out/usb-system-mount-v28 --base ../unixnu-source
```

报告为 mount-volume-args-tests.json。SPI/trace 检查只在临时副本中依序撤销
后续补丁来检查对应补丁，不改已构建源码；仍执行当前版本的真实入口。
候选准备脚本会清除继承的参数检查报告。

## 实机验收（待测试）

主机检查完成后 fastboot 列表为空，没有运行中的传输脚本。等待用户把
已 panic 的手机重启进入 fastboot；不盲目重试启动，不向分区写入。
确认 fcde91e 后执行一次：

```sh
DYLD_LIBRARY_PATH=/opt/homebrew/lib PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/test-usb-ram-full.py \
  --image out/usb-system-mount-v28/Mu-fuxi-usb-ram.img \
  --expected-sha256 836bd4bb609dafdda7a0347ea46078459143de6c5b0c1fe7506393c5354b16b3 \
  --research-image /Volumes/External/VSCode/UniXNU/unixnu-source/out/fuxi/userdata/userdata-root.img \
  --boot-profile usb-system --output out/usb-system-mount-v28/attempt-01
```

启动前还需检查 mount-volume-args-tests.json 与当前 boot/kernel SHA256 匹配。
双对象 END RAM 校验后，手动选 fuxi RAM diagnostic: system-mount。
拍摄最终完整屏幕；若失败，保留 at/dev/opens/spec/apfs/covered。
验收目标仍为两个卷只读挂载后的 RAM_SYSTEM PASS，停在用户态之前。
最终结论须追加回填本文件、REVIEW.md 和 usb-bulk.md；当前尚未实机。
