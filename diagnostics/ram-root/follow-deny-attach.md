# follow 模式放行 PT_DENY_ATTACH（v42 设计）

依据：v41 attempt-01 照片（IMG_6990）。stage=10 main=PASS、map=1
err=0 rv=0、exec=65 之后，系统停在 PID57 的 `ptrace(26)`，a0=0x1f=31
即 `PT_DENY_ATTACH`（bsd/sys/ptrace.h），触发 follow 模式封锁集在
RamLaunchdProbe.c:387 派发前 panic。这是守护进程标准反调试自保护，
不是对其他进程的跟踪请求。

## 改动

只放行 `ptrace` 的单个请求 PT_DENY_ATTACH=31；其余 ptrace 请求与
mount/unmount/__mac_mount/fmount/reboot 全部维持派发前封锁。
例外位于 fuxi_ram_progress_unix（与 tmpfs 例外同层），按编译位
`RAM_ROOT_FOLLOW_DENY_ATTACH` 生效，由候选 pins.json 的
`follow_ptrace_deny_attach` 驱动 build-ram-root-probe.py 生成：

- pins 缺省/False：行为与 v41 完全一致（例外代码不编译进路径）。
- True 要求 progress 档案，ram_probe_policy.validate_stage_policy
  拒绝在非 progress 模式置位；check-ram-probe-policy 对四个模式
  样本校验，并对非 progress+置位组合断言拒绝。
- guards 模型按 pins 值生成同一定义：置位时 1 例放行
  （SYS_ptrace,31,0 返回非零 token）加 2 例拒绝（request 0 与 7）；
  未置位时 PT_DENY_ATTACH 仍拒绝（v41 行为回归）。
- fuxi_ram_main_unix(SYS_ptrace) 本体仍无条件拒绝：例外在调用它
  之前返回，内核侧不存在无参放行路径。

request 号 31 以 `RAM_ROOT_PT_DENY_ATTACH` 常量写入探针源文件，
注释指向 bsd/sys/ptrace.h；不引入运行时读用户内存，arg0 直接取自
systemcalls.c 已解析的 syscall 首参（间接形式已由内核解析）。

## 写入封锁粒度复核（本轮结论，无代码改动）

- follow 封闭集里没有 write/open 类 syscall；v41 照片 `write=, pid=63`
  是派发前采集，系统随后继续运行到 PID57 的 ptrace，证明 write 实际
  派发。写入失败来自挂载标志：切根后 OS 卷 `apfs readonly rootfs=1`，
  载体 tmpfs 只读封存；EROFS/ENOENT 由 VFS 原生返回。
- 用户态可自挂内存 tmpfs（既有例外）获得可写存储；不新增内核侧
  可写区。若下一停点显示守护进程因缺可写 /private/var 大量死亡，
  再评估带配额的可写 tmpfs（RAM 配额与 free 页观察一并核算），
  本候选不预写。
- panic 尾部 pages=0xac664 free=0x3a2 仅按片记录；free 接近
  free_target 的稳态与真实耗尽无法从单帧区分，不据此调整
  vm_compression_limit 或窗口。

## 验收

实机照片判据沿用 v41：carrier/cache/map/stage/main/exec 各字段；
新增关注点为越过 PT_DENY_ATTACH 后的下一个停点（预期：更多守护
进程 spawn、EROFS 类失败、或 deadline/SVC 上限到期）。
RAM_FOLLOW panic 若再现 syscall=26，需核对 a0 是否仍为 31（即
例外未生效，构建绑定问题）；若为其他请求号则属预期拒绝。
