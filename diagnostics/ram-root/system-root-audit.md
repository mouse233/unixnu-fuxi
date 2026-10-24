# fuxi 系统根建立审计（21H461，2026-10-24）

## 已确认的边界

v35/IMG_6984实机确认/mnt1/sbin/launchd对应卷flags=0x14909001、rootfs=0，
AMFI的现有授权查询0x80084118返回EAUTH80，类别2拒绝保留。根仍md0，
进入EL0/main未通过。原始AMFI类别2只要求授权auth-APFS卷或系统根卷；
这不能推导其它exec检查都已通过。

## 本次源码审计

以v35交付对应work/tree/.cache/xnu-work为源码依据：

1. bsd/kern/bsd_init.c：setconf选择根设备→vfs_mountroot→IOSecureBSDRoot→
   给真实根挂载设置MNT_ROOTFS→VFS_ROOT/set_rootvnode→根认证→挂/devfs→
   fuxi_ram_system_probe→siginit/bsd_utaskbootstrap。当前系统探针在md0根与
   /dev已建立后才发布RAM媒体并挂载/mnt1、/mnt2，故只是次级挂载。
2. bsd_rooted_ramdisk判断来自rd引导参数前缀；当前rd=md0使原生根认证
   分支跳过，这对应RestoreRamDisk根，并未认证两个后挂载的系统卷。
   将媒体提前发布并改rootdev却仍保留rd=md0，会留下认证分支不匹配问题，
   不能把此组合当原生系统根流程。
3. 非MacOS原生根认证调用是
   `VNOP_IOCTL(rootvnode, FSIOC_KERNEL_ROOTAUTH, NULL, 0, vfs_context_kernel())`。
   bsd/sys/fsctl.h定义该请求为_IOW('h',51,uint32_t)，即0x80046833。
   它与AMFI叶的0x80084118不同。v35文件授权查询非零，不足以断言卷根上
   原生根认证请求的返回码，更不足以定位APFS内部具体失败条件。
4. bsd/vfs/vfs_subr.c原生vfs_switch_root并不只是补root位：先验证incoming
   真正卷根、旧根安置目录以及已挂载的保留目录，然后搬移覆盖点、保留
   /dev等挂载、交换根身份、清名字缓存、更换rootvnode并调整挂载路径。
   函数明确在预检完成后进入不可中途退出的修改阶段，不能忽略目录或引用。
5. bsd/kern/imageboot.c现有pivot路径在vfs_switch_root之前执行认证；其
   BaseSystem专用data=1及rooted/chunklist/skip例外不适用于本次iOS系统根。
   不复制这些例外来使失败变成功。vfs_switch_root也不会替调用者完成全部
   镜像/根认证，不能用AMFI只看root位来代替原生根建立职责。

## 下一验证点：v36原生认证预检

对两个已验证、只读的RAM APFS卷根分别执行一次原生iOS根认证请求，并
保存原返回码及前后flags；查看dev、private/var、System/Volumes/Restore、
System/Volumes/BaseSystem的同卷目录身份。此请求主动调用认证，区别于
v35被动观察，不应把二者混称为只读查询。存储介质仍硬件报告只读、无UFS
或持久化写入。结果无论成功失败均在PID1之前停止，不执行任何切根操作。

编译保留md0 root/user-entry是到达既有system mount checkpoint的前提；
system-user-entry/main/progress关闭，预检调用noreturn。mount-only的init
接口必须有强制停止实现，以满足实际kern_exec.o引用，并防止意外回退md0
init。编译对象/档案/镜像绑定必须核验，不能仅检查宏或补丁文本。

次级卷上的根认证成功仍不等于原生系统根建立成功；失败也可能受当前
挂载模式/启动上下文影响。根据真实返回、卷角色和目录布局选择后续原生
mountroot或pivot路线，再检查媒体发布时机、认证、/dev、rootdev/rootvp、
rootvnode及进程目录引用。不得通过伪造root位、改trust类别、改AMFI结果或
压制认证失败把次级挂载包装成完整系统根。

构建与实机记录以usb-bulk.md及候选REVIEW.md为准。
