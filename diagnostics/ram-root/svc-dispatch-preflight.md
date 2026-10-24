# v39 上机前静态预检：SVC 派发后的 dyld 库解析链（2026-10-25）

以v38实机PASS为起点、v39候选（progress模式）为对象，沿"事后取证
前移"方向做的静态推演。所有路径均对照21H461内核源码、原始dyld与
主机侧原生挂载实测，未动设备。

## 1. v39 实机结果预测（静态结论）

v38的RAM_NATIVE_ROOT_USER PASS只证明到"dyld已映射并开始执行"：
首个SVC（mprotect, x16=74, svc #0x80）可能发生在dyld加载任何库之前
——pc=0x100904fb4相对launchd文件大小（0x89300）偏移关系不能区分
落在launchd还是库映射，库依赖是否解析成功未获证明。

v39放行SVC后的静态推演链：

1. launchd依赖libSystem.B.dylib/libobjc.A.dylib/libsandbox.1.dylib/
   swift库（otool -L实测），全部仅存在于dyld共享缓存。
2. dyld（原始二进制，strings实测）的缓存搜索路径：
   /System/Library/Caches/com.apple.dyld/、
   /private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld、
   /System/Cryptexes/OS/System/Library/Caches/com.apple.dyld。
3. 内核vm_unix.c对iOS同表（scdir_path），scdir_enforce=0不设限。
4. 切换后的OS根（主机挂载实测）：无/System/Library/Caches/com.apple.dyld
   （Caches仅有apticket/factorydata/kernelcaches）、无/usr/lib/libSystem.B.dylib
   （usr/lib仅19项，含dyld本体）、/private/preboot存在但为空，
   /private/preboot/Cryptexes不存在（只读卷无法mkdir），
   /System/Cryptexes/OS符号链接指向上述缺失路径（悬空）。
5. cryptex卷（/mnt2）内部有共享缓存：System/Library/Caches/
   com.apple.dyld/dyld_shared_cache_arm64[.01…]分片共约6.1GB，
   但其usr/lib无libSystem等核心dylib（仅杂项trampolines/stdc++）。
6. 结论：**pivot后dyld找不到缓存（三条路径全ENOENT）、
   /usr/lib/libSystem.B.dylib也ENOENT → dyld致命退出 → init死亡 →
   内核panic**。预期照片特征：极少量SVC（mprotect后一两个），
   progress快照last_write可能带dyld错误文本（写/dev/console经
   write记录），随后init死亡panic。此为决定性证据：把"库来源"
   与v34以来的一切exec问题清晰分层。

该预测不改变v39价值：它正是必需的真机一步，用实机区分
"dyld库解析失败"与其它可能，并为v40提供基线。

## 2. v40 设计（静态完备，全部有上游先例）

- 挂载：pivot成功后把cryptex卷（UUID 8846FE78-2910-4297-AF97-
  D59BB6F8FC16，与上游补丁XNU_OWNER_CACHE_VOLUME_UUID默认值一致，
  与本机cryptex卷实测UUID一致）只读挂到 /private/preboot
  （OS卷上存在的空目录）。对齐验证：DYLD路径第二项
  /private/preboot/System/Library/Caches/com.apple.dyld 恰为
  <cryptexroot>/System/Library/Caches/com.apple.dyld ✓。
- 库解析：给init注入env `DYLD_SHARED_CACHE_DIR=/private/preboot/
  System/Library/Caches/com.apple.dyld`。通道上游已有：
  patches/xnu/offline-owner-systemos-mount.patch的
  native_owner_cache_prepare构造envp并经load_init_program_at_path
  （当前树签名已带envp参数）传入execve；cryptex授权门可用v36实测
  的原生rootauth=0替代VirtIO块授权。补丁的mountpoint默认
  /private/preboot与本设计一致。
- 不需要：cryptex usr/lib真实dylib（已证其无核心库，缓存即库源）；
  不动dyld；不改AMFI/信任类别/判定。
- 备选：XNU_OWNER_CRYPTEX_FILES的DYLD_FALLBACK_LIBRARY_PATH、
  XNU_OWNER_UNION_SYSTEMOS的原生union graft（FSIOC_GRAFT路径，
  vfs_syscalls.c graft_secureboot_cryptex），均留作后续。

## 3. 静态分析终点（必须真机回答）

1. dyld库解析失败的实机表现与错误文本（v39交付）。
2. 内核/AMFI是否接受从/private/preboot映射该DSC（卷rootauth=0
   为卷级证据；文件级映射校验是独立环节，静态无法裁定）。
3. dyld对DYLD_SHARED_CACHE_DIR指向分片缓存的处理（原始dyld行为，
   无源码；owner时代先例表明可行）。
4. launchd在只读根上的实际运行行为（EROFS处理路径、523个
   LaunchDaemons的spawn时序与首批子进程集合）。
5. 高频SVC下探针观察器的时序竞争——只有实机能暴露。

以上之外，v39/v40路径上的全部代码级前置（切根身份、MNT_ROOTFS、
安置目录、devfs、launchd字节、缓存路径对齐、envp通道签名）均已
静态闭合或实机证实。
