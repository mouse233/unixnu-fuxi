# v30：只读 RAM APFS 第一卷的设备名

日期：2026-10-24。父候选 v29 的 IMG_6967.HEIC 已确认：APFS 成功打开
/dev/md1、读取 512 字节设备几何和 xid=418 的稳定 checkpoint，最终由
nx_is_mount_valid:2306 以 invalid fsindex: -1 返回 ENOENT。
因此本次修复依据为实机日志和固定哈希原始解析器，非模拟器的块号假故障。

功能改动在 XNU：新增 zz-ram-system-volume-nodes.patch。
fuxi_mdev_add_ro 只对 md1/md2，在设置只读和精确范围后发布
devfs 硬链接 md1s1/md2s1。链接共用原 devnode/设备号和生命周期；
不增加物理设备，不修改 I/O 或几何，不创建符号链接。发布失败返回 NODEV，
原探针立即停止。原 devfs_remove 会移除该节点的所有链接。

专用挂载 SPI 的外部许可仍为 /dev/md1→/mnt1、/dev/md2→/mnt2，内部
请求改用相应 s1 名称。VFS 的 namei 路径保留到 f_mntfromname；原始
APFS 从中解析出容器 mdN 和卷索引 0。两个真实容器块零均只有卷表 index 0
有效，oid=0x402。保留 kernel_task、只读 APFS、禁止更新挂载、固定配对，
以及原有 VFS/APFS 检查、错误清理和 witness。

APFS 原始可执行段 SHA256 仍为
3192441dae74abc0d82e9069447194934598c1c63d2dacba0fb8599eef9996d7。
UEFI/DarwinBoot 源码及 USB 布局不改；交付组合镜像包含新 XNU，固件在指定
Linux 容器构建后由 package-fuxi-xnu-boot.py 重新打包。

交付 out/usb-system-mount-v30/Mu-fuxi-usb-ram.img，203,190,272 字节，SHA256
3b3a181c5ad47087e4e08a5fe63eadfdce5c224ff2b02715a999fe5a10316c1b。
prelink b30b118ef3df6527dfcea760c72a33130fedbb7bb668a4bec2057fe5ffd58df9。
FUXIPAYL、实际最低段 0xfffffff00427c000 与 skew 0x7c000 重新核验通过。

四项必需主机检查全部退出 0。payload、KMEM（七项、VA 余量下界
662,548,480 字节）、SPI、trace、参数、witness、卷节点和启动门也全部通过。
新检查执行真实旧/新注册 C（ASan/UBSan），覆盖只读发布时序、两个名称、
地址/长度边界、重复注册、分配和链接失败，并绑定实际编译记录及交付内核。
交付 prelink 的原始解析器八组输入通过，两盘 s1 名称均得到正确容器和 index 0。
启动门针对带卷名的 payload 要求本镜像/内核的卷节点报告；九种 mock 场景通过，
不合格情形未执行任何设备命令。历史 SPI/参数检查继续兼容旧候选。

用户已明确授权 fastboot 临时启动。attempt-01 已开始；最终传输和屏幕结果
须追加回填本日志入口 usb-bulk.md 与候选 REVIEW.md。完整命令和报告见 REVIEW。
本节仅说明主机验证，不宣称 APFS 挂载、RAM_SYSTEM PASS 或用户态启动成功。
