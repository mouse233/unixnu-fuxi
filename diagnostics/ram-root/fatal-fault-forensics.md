# v44：致命 brk 故障取证（设计）

依据：v43 attempt-01 照片（IMG_6993）。watchdogd 死于 SIGTRAP
（OS_REASON_SIGNAL ns=2 subcode=5），内核按 kern_exit.c 关键退出策略
panic；本帧没有 faulting PC/ESR，SIGTRAP 直接原因未证实。arm64 上
SIGTRAP 的典型来源是 EL0 `brk` 指令（ESR_EC_BRK_AARCH64 →
handle_breakpoint → EXC_BREAKPOINT，无处理者即 SIGTRAP）——
Apple 断言/__builtin_trap 均编译为 brk，ESR 的 imm16 可区分断言类。

## 改动

纯观测取证，不改变任何异常语义与策略：

- 内核补丁 zzzzz-ram-launchd-main.patch：ESR_EC_BRK_AARCH64 的
  EL0 分支在 handle_breakpoint 之前调用
  `fuxi_ram_progress_fault(pc, cpsr, esr, far)`（XNU_RAM_MAIN_PROBE
  门控）；内核态分支与 HW 断点/单步/watchpoint 路径不动。
- 探针新增 sticky 故障记录（fault_pc/far/esr/pid/count）与快照行
  `fault=%u pid=%d esr=%x pc=%llx far=%llx`（仅在有记录时显示，
  正常运行零字节开销）。函数不调用 require_observed_thread、不做
  copyin、不 panic——取证钩子在任何上下文都不改变行为。
- ram_probe_policy 的 sleh.o 符号绑定增加 _fuxi_ram_progress_fault；
  guards 模型 +3 例（无故障行缺失、记录与覆盖语义、锁竞争丢弃）。

不改：封锁集与例外、观测窗口参数（900s/1048576）、快照其余部分。
若下一帧 fault= 行出现，则 SIGTRAP 来自 brk，pc/far/esr（含 imm16）
给出断言位置与类别；若仍无 fault= 行而守护进程再死于 SIGTRAP，
则来源在 brk 之外（EXC_GUARD 等），本身即是否证信息。

## 验收

实机照片判据：完整 RAM_PROGRESS 快照含 fault= 行（若有）与最终
panic 行。pc 反查 dyld 缓存符号、esr imm16 对照 Apple 断言编码，
在主机端用交付缓存审计工具完成。
