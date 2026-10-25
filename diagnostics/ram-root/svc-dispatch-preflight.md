# SVC 与共享缓存预检：v39 审计修复、v40（2026-10-25）

原报告“静态分析已到终点、v40 静态完备、owner 先例证明可行”撤回。
原始会话及 usb-bulk.md 历史条目保留；v39 镜像保留作历史对照，
不再作为完整门禁通过的待启动候选。

## 探针和检查修复

原环掩码修复未解决同 PID 多线程 spawn 结果串行。现在 syscall
入口保存 64 位调用 token，返回携带同一 token；事件与 spawn 均检查
 token、thread、code。入口丢弃返回 0，不得回填旧调用；环覆盖后的
返回只能丢弃。新槽完整初始化，不沿用旧结果。

目标 XNU posix_spawn 把子 PID 写到用户指针，retval[0]=error，成功
应为 e=0 rv=0，旧判据 r=pid 错误。现在保存 PID 输出地址，成功后
在锁外读取，再核对 token 后回填 child 与读取错误 pe；NULL 指针、
读取失败、记录丢失均保持 child=-1。child>0 pe=0 是该用户指针的
观察值，仍可能被其他线程修改；它不是内核创建事件的不可变 PID。
创建成功不证明子进程已到 EL0；运行证据另看后续带 pid 的事件。

套件从实际档案推导 main/progress/native，拒绝 boot selector 不符。
profile 4 必跑 policy 与 --system-entry --native-root --progress
 guards。policy 检查 kern_exec 两个宏和真实引用，以及 sleh/
 systemcalls 的主探针宏与调用。每轮日志、套件记录位于独立
 check-runs/<UTC>/，失败不被重跑覆盖；新候选清除父检查历史。

## 已实现缓存链

v38 的 EL0 首次 mprotect 不能证明缓存解析成功。OS 根缺真实核心
 dylib，标准缓存路径缺失；RAM cryptex 已挂 /mnt2。原生 pivot
只搬移固定 preserved mounts，/mnt2 随 md0 移至 /Developer/mnt2。
v40 复用此挂载，不重复挂载，不创建只读根目录。

RamSystemCache.c 接收 pivot 前捕获的原 /mnt2 mount 身份，切根后
核对 /Developer/mnt2 同一只读 APFS 次级挂载和目录，实际发原生
 FSIOC_KERNEL_ROOTAUTH，非 0 如实 panic。PID1 exec 前再核对上下文
和 mount，读取并 SHA256 校验 OS 根的原始 dyld，向 private scratch
注入 DYLD_SHARED_CACHE_DIR=/Developer/mnt2/System/Library/Caches/com.apple.dyld。

同时设置 dyld_flags|=8 并更新真实 commpage，保留其他 flags。
原始 CacheFinder 在 env 目录成功打开后仍可能调用 ignition；历史
 check-owner-systemos-mount-checkpoints.py:81–89 已记录过目录 fd 被
 ignition 清理、openat 返回 EBADF，旧报告漏读了后续 CACHE_STARTUP
的 bit 3 机制。仅对齐路径和 envp 不足以闭合链路。

未修改 dyld、AMFI/APFS/IOStorage 原始执行字节、信任缓存或认证返回。
新钩子仅在诊断路径通过 weak_import 绑定，未启用 --system-cache
的档案不执行缓存准备。只读缓存卷 auth 必须真实为 0；OS 根 auth=80
仍为原有记录项，不改成成功。

## 本轮已执行的主机验证

独立 out/usb-system-mount-v40：主机 XNU → Docker 固件 → FUXIPAYL，
16 项套件退出 0，绑定镜像、档案和编译对象。

- production root/main C：87 例，ASan/UBSan，含同 PID 两线程 spawn
  交错、槽 4–7、环覆盖、入口丢失、PID 输出读取失败和缺失，以及
  native 根身份、原始 argv/main 字节与 syscall 封锁。
- root-switch：43 个真实 C 用例、3 项突变拒绝。
- cache bind/prepare：23 个真实 C 用例、3 项突变拒绝；检查真实原始
  dyld 哈希、copyout/envp 布局、原错误、flags 与 commpage 更新。
- Unicorn 执行未修改的原始 CacheFinder 构造/析构函数，5 个输入；
  外部 getenv/open/ignite/close 由桩提供。flags=8 不调用 ignite，
  fd 0 到析构才关闭；flags=0/4 进入 ignite；目录打开失败如实失败。
  没有模拟完整 ignition、AMFI 或整个 dyld。
- 按 userdata manifest 切出两个窗口并重算 SHA256，与传输输入匹配；
  macOS 只读挂载。OS /usr/lib/dyld 为 937056 字节，SHA256
  f8df3c58fae6e409db740df22c98aa699aee110b73523541a63f1bd5e23cf705，
  与模型原件一致。
- check-ram-cache-files.py 用 Apple dyld-1122.1 头推导格式偏移；
  53 个原始缓存文件、201083 个完整签名块哈希匹配，映射均按 4K
  对齐，四个 4K 子块分别破坏均被拒绝。没有校验 CMS 信任链，未将
  文件哈希检查写成内核实际映射成功。

镜像、报告、命令与失败日志见 v40 REVIEW。Unicorn 使用本机已有
 Python 3.12 环境并需要沙箱外 JIT 权限；沙箱首次失败已保留，随后
在相同镜像完成完整成功重跑。

## 仍待实机

v40 尚未上机。实际路径查找/rootauth、内核/AMFI 接受缓存映射、PID1
到 LC_MAIN、只读根运行和子进程实际执行，须按新 attempt 照片判断。
历史离线 PMGR 错误是后续线索，不预判 fuxi 必然复现，也不声称当前
模型已解决。发现新的可离线分析项应继续处理，不再笼统宣布静态终点。
