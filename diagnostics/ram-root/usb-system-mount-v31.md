# v31：只读 RAM 系统存储的 IOKit 发布

父候选 v30 IMG_6968 已越过 invalid fsindex，但原 APFS 因不存在对应
IOMedia/BSD 身份而拒绝挂载（EPERM）。本轮修改 XNU 的存储发布与私有
挂载入口，原 APFS/IOStorageFamily 可执行区保持不变。

独立 NativeRamSystemBlock 在实际 RAM SHA256 校验通过后发布两个只读
APFS slice。原生存储链路负责创建卷节点；挂载入口只接受固定 md1/mnt1、
md2/mnt2 对，内部查找真实 AppleAPFSVolume，要求只读、有效 UUID、BSD
major/minor、唯一 diskNs1 和 provider 链归属于该 RAM 设备实例。30 秒超时
附读取和节点统计，歧义/坏身份停止。原 APFS 资格检查继续执行。

新驱动仅借用 RAM，拒绝写入、格式化和启用缓存；构建开关排除物理 UFS、
可写 NativeRamBlock 和 Virtio block。探针仍在用户态之前停止，只有两个
只读 APFS 挂载都成功才输出 RAM_SYSTEM PASS。现有 md 范围校验与诊断
设备保留；本轮的挂载实际使用原生卷节点。

独立候选 out/usb-system-mount-v31 的镜像 203190272 字节，SHA256：
`9a623fcd52e1d2e0bf26745a8598bc2f17b95a9d339e49501cc40f7b7baa716e`。
prelink：`8ea05e09ff61e89efd11f3353fb61b486f8124e12fd20ff2115649c7978d3b8c`。
FUXIPAYL、kernel base 0xfffffff00427c000 和重新推导的 skew 0x7c000 通过。
UEFI/DarwinBoot 功能未改，固件和组合镜像按容器流程重新构建、打包。

四项必需主机检查与 payload/KMEM/SPI/trace/args/witness/IOKit/启动门均退出
0。新驱动真实内核 ABI 编译通过，全 190 槽、23 个覆盖与原始导入通过显式
静态 bring-up 审计；这不是实机 metaclass/匹配/I/O 通过。相同 C++ 在 IOKit
依赖替身和 ASan/UBSan 下验证 >4GiB 末扇区、短读、写入拒绝、越界/溢出、
回调次数、归属、身份、歧义、失效迭代和超时；17 个 mock fastboot 门禁通过。
报告绑定实际源/头文件、对象、archive、链接摘要以及交付内核/镜像。

SPI 检查会在临时目录逐层回退新 IOKit/卷链接补丁，再验证旧补丁；始终根据
回退后的实际源码识别下一层。测试旧候选的原始设备名仍保持兼容。

attempt-01：serial/product/unlocked 门禁通过，镜像发送 OKAY，但 Booting
返回 usb_read failed e00002ed；两个对象协议字节均为 0。只读 USB/fastboot
枚举当时均不可见，未据此宣称停机、XNU 拒绝或自动重传。用户随后确认在
fastboot 并要求再次进入；重新识别 fcde91e、核对无占用后开始 attempt-02。
原始失败和后续结果分别归档，详细命令与实机回填见候选 REVIEW.md 和
规范日志 usb-bulk.md。当前尚无 v31 挂载照片，不宣称实机修复已通过。


attempt-02 最终传输结果：临时启动成功，两个对象 END 实际 RAM SHA256
均通过，560.733 秒，退出 0，未写分区。等待正常停止证据、用户手选
system-mount 及最终照片；这尚不能证明 XNU 挂载成功。
