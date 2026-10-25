# 扩大 follow 观测窗口（v43 设计）

依据：v42 attempt-01 照片（IMG_6991）。PT_DENY_ATTACH 壁垒消除后，
系统无封锁停机、无故障，在 svc=131073 处触及探针 131072 SVC 观测
上限被 deadline_expired 收窗（RamLaunchdProbe.c:147）。exec=95、
unix=87745 仍在推进：PID1 连续 spawn xpcproxy，PID90 处于新进程
libSystem 初始化序列。观测窗口本身成为前沿。

## 改动

仅扩大有界观测参数，不改变任何放行/封锁策略与采集逻辑：

- 期限 pin `progress_deadline_seconds` 300→900；允许值 (90, 300,
  900)，300/900 均要求 system cache + native_cs_hash_cache 完整性
  模式（沿 v41 先例，覆盖 3.3GB 签名预读的最坏时长）。
- 新增 pin `progress_svc_limit`=1048576（缺省 131072 保持旧行为）；
  探针 `count > RAM_ROOT_PROGRESS_SVC_LIMIT` 由生成头驱动，
  `#ifndef` 缺省 131072U。非 progress 档案拒绝提高该上限。
- build-ram-root-probe.py 生成 `RAM_ROOT_PROGRESS_SVC_LIMIT %dU`；
  check-ram-system-cache.py 断言期限/SVC pin 与生成头一致（允许值
  集合内）；guards 模型同一定义并以 svc_count=<limit> 触发上限
  路径（93例不变）。

不改动：封锁集与 tmpfs/PT_DENY_ATTACH 例外、快照格式与环大小、
内存/时钟（照片 footer 与 write 缓冲已分别给出 free 页与 1970
时间戳证据，下一轮照片继续按片记录）。900 秒与 1048576 SVC 哪个
先到取决于负载，两者皆为设计内收窗。

## 验收

实机照片判据：越过 v42 的停点形态——关注 exec/unix 增量、守护
进程矩阵（spawn 路径多样性）、write 缓冲内容、free 页趋势、
以及是否出现新的自然停点（EROFS 类写失败、内存耗尽、时钟依赖
失败）或 RAM_FOLLOW 封锁（ptrace 以外请求/物理挂载仍应拒绝）。
