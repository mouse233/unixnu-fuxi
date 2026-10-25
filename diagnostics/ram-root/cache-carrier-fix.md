# v41：原生缓存载体命名空间与完整签名块验证（2026-10-25）

v40 实机 cache=2 flags=8、70 次 SVC 后仍在 main 前报 no dyld cache。
这只证明准备完成，没有保留最初 open/签名/映射错误。源码预检现在
明确区分两项阻塞，不能把实机错误倒推成一个未经采集的 errno。

## 两项原有门槛

1. 交付 ubc_subr.o 的 XNU_NATIVE_CS_HASH_CACHE=0，实际
   cs_validate_codedirectory 的页大小门拒绝 16K 签名块。缓存的53份
   原始文件使用16K签名块，而内核是4K软件页；这个拒绝早于映射。
2. CONFIG_CSR=0 的共享缓存映射还要求缓存文件位于进程根卷，或与
   /private/preboot/Cryptexes 解析出的 vnode 同一 mount。目录约束
   scdir_enforce=0 不关闭此独立检查。环境变量不建立这个身份。

## 实现

沿用 v40 已经实测通过的只读 /Developer/mnt2 mount/rootauth 和
原始 dyld 字节绑定。PID1 exec 前，确认现存 /private/preboot 在OS
根卷且尚未覆盖，Cryptexes 不存在，然后通过原始加载器载入同一
kernelcache 的 com.apple.filesystems.tmpfs，使用普通 mount/symlink
接口完成以下 RAM 布局：

```
OS root (read-only APFS)
  /private/preboot  -> original tmpfs, final read-only
    Cryptexes      -> /Developer/mnt2  (real read-only APFS cache mount)
```

原始 tmpfs 参数是24字节，pages=256、nodes=32、options=0，容量上限
1MiB（native4K），禁止执行、设备、suid。只在RAM中创建一个符号链接；
随后通过原始 MNT_UPDATE|MNT_RDONLY 接口封存 tmpfs。重新解析
/private/preboot/Cryptexes，要求 vnode_mount 等于切根前捕获并已认证的
cache mount，且确是其卷根。没有改挂载身份返回值、没有把 tmpfs
当缓存卷、没有重挂载 APFS，也没有向只读 OS/SystemOS 写入。

shared_region 原始检查仍原样执行，名称空间让它通过真实 VFS 解析
拿到同一个APFS mount。所有加载/挂载/链接/封存失败以原状态或errno
panic，绝不失败后继续放行。此 namespace 并不等于完整原生 Preboot
启动生态，后续其他路径需求仍须另验。

CONFIG_XNU_NATIVE_CS_HASH_CACHE=y 启用仓库已有的完整签名块适配：
先对同一缓冲区的完整16K块逐块匹配原 CodeDirectory 哈希，全部通过后
才派生4K页哈希，保护为只读，再发布到cs_blob。原始文件、签名、CDHash、
信任缓存和AMFI/APFS执行字节不改；不是把16K签名拆成未经认证的4K签名。

新诊断固定保留缓存目录/文件 open/openat 和 shared_region_map_and_slide_2
的原始返回，后续 console 输出不能覆盖；未观察保持 -9999。新增
carrier、sigblobs/blocks/pagechecks 字段。仍为1024字节屏幕快照，保留
最近4条Unix事件、最近2条spawn（新到旧）；完整关联环仍为8槽。

首次完整签名预读约3.3GB，将本候选有界期限改为300秒，SVC上限仍
131072，mount/unmount/reboot/ptrace封锁及既有tmpfs例外保留。

## 主机证据

check-ram-cache-admission.py 直接编译候选工作树中的实际代码：

- CodeDirectory页大小门8组合：旧4K/cache-off拒绝16K，新模式接受；
  这个门通过不单独等同完整签名通过。
- cs_native4k_prepare 24例，真实SHA256校验合成原始签名块、逐4K派生
  哈希；八个子块分别破坏、I/O/内存/保护失败均拒绝，失败不发布。
  使用真实mprotect作只读发布；完整块比较与保护失败传播两种突变被拒。
- 原始非CSR载体分支5例：根卷、路径不存在、同mount、错误mount和
  lookup失败，原来的EPERM不改。
- 未修改的原始 tmpfs 指令5例：新挂载读24字节参数（停在copyin入口，
  未仿真完整节点分配）、copyin失败原返回、只读更新、reload拒绝和
  非root拒绝。KPI由明确桩提供，不把模型写成真实完整挂载成功。

同时核验 ubc_subr.o 实际定义开关1及验证函数、vm_unix实际保持
XNU_OWNER_CACHE_CARRIER=0、CSR=0，原始载体源码与v40逐字节相同；
原始 tmpfs 已预链接，AMFI/APFS执行字节仍与父候选一致。

生产 root/main guards 90例；cache bind/prepare 43例与4项突变，涵盖
加载、挂载、链接、封存、真实身份及每次copyout失败。既有原始 dyld
CacheFinder 5输入继续运行。缓存53份文件/201083完整签名块另由只读
窗口审计绑定，不把这些主机模型写成完整硬件启动成功。

最终镜像、完整17项套件退出码、哈希和实机边界见独立v41 REVIEW。
