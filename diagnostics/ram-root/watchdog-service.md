# v46：原始 IOWatchdog 服务链与软件计时后端

基于独立v45副本，保留1MiB HELLO协商分块。改动层为XNU设备包与
构建/检查脚本；重新构建固件并组合打包，原始Apple用户态、信任缓存、
AMFI及关键退出策略不改。v44照片与原始watchdogd指令的匹配仍是强
线索，不冒充已经采集到原生IORegistry查找返回。

## 注册/匹配链的缺口

同一21H461 kernelcache中，AppleARMWatchdogTimer personality要求：

- IOProviderClass=AppleARMIODevice；
- IONameMatch=wdt,s5l8960x；
- IOClass=AppleARMWatchdogTimer。

fuxi的ADT/ArmDTPlatform未提供这一Apple ARM IO设备，不能因为kext
已预链接就断言服务已注册。原硬件start还映射Apple寄存器、依赖其
平台属性；把名称补进fuxi ADT会给SM8550执行错误MMIO，不是修复。

同一模块还包含通用抽象IOWatchdog（248字节）、IOWatchdogUserClient
（232字节）及原始权限、独占打开、心跳、开关和超时路径。原始类在
IOService的168槽后只有3个硬件后端抽象槽。此边界可独立接入平台实现。

## 实现与启动顺序

1. 原cache准备钩子在PID1 exec前，按独立pin/boot arg调用
   fuxi_ram_watchdog_prepare；错误立即按errno panic，不继续宣称准备成功。
2. 原加载器加载AppleARMWatchdogTimer模块，实际核对IOWatchdog及
   客户端metaclass、父类身份和对象大小；检查现存匹配结果为空，避免
   覆盖真正已工作的服务。
3. 在preModLoad/postModLoad范围内初始化本机RamWatchdog子类。编译
   对象的自动mod_init已移除，执行代码逐字节不变，确保父类先就绪。
4. 创建真实子类、分配真实thread_call、attach到IOResources，然后
   调用未修改的原始基类初始化。保留原基类的registerService、
   内核监测、root/entitlement/独占和客户端派发。
5. 用真实serviceMatching("IOWatchdog")再次枚举，要求恰好一个服务且
   对象就是本实例；成功才置wdt=3。这不是只写一个IOClass字符串。

软件后端通过原始IOSimpleLock及相同的中断保护读取原始enabled、
heartbeat sequence和absolute deadline；不写这些私有字段。原始
初始化设置600秒宽限，客户端心跳或重新启用更新为90秒，停用按原
协议清理状态。后端每秒检查一次：启用时deadline到期（或无效）会
真实panic；停用时不误报，重新启用后仍检查。stop/free取消定时器。

RamWatchdog::newUserClient完整委托原实现，仅对成功且非空的客户端
增加观察计数。它没有返回虚假连接、放开权限、伪造心跳或修改原始
异常返回；原用户客户端的全部10项dispatch表保持原样。

此后端明确标识software-original-deadline和HardwareWatchdogImplemented=false。
它能够检测内核运行时的用户态心跳超时，不能替代SM8550独立硬件WDT，
不能覆盖CPU/调度器完全停摆。也未映射Apple或Snapdragon WDT寄存器。
这是RAM bring-up的功能后端，后续硬件支持需单独实现、测量和验收。

## 构建及绑定

build-ram-watchdog.py限定原kernelcache SHA256 f18b4df8…de7，解析
模块、原始metaclass构造参数、171槽vtable和心跳指令。基类头保留
精确opaque存储；本机子类所有继承槽与原IOService导出逐一比较，
只允许明确声明的自身生命周期/启动/观察和3个后端槽覆盖。

5项原始绑定为父类C2、D2、metaclass、vtable及初始化函数；编译器
通过原vtable调用stop/newUserClient。地址按本候选固定prelink slide
-0x1000000从原cache推导，并与父v45各原模块段的位置核对；不把
这个slide或私有布局推广到其他固件。原AppleARMWatchdogTimer执行
段哈希不变。归档进libRamRootProbe.a，现有内核链接消费同一档案。

新增pin system_watchdog=true、ram-system-watchdog=1，主探针显示：

```
wdt=3 clients=<原客户端成功打开次数> polls=<定时观察次数>
beats=<原始心跳sequence> enabled=<原始开关>
```

快照仍1024字节，Unix尾部减到3条为wdt行留空间，关联环保持8槽。
原fault记录的全局覆盖、锁丢弃边界未在此候选解决：无fault不排除brk，
有fault也须关联PID/TID/映像和退出，不使用旧的错误否定判据。

## 主机验证与实机边界

check-ram-watchdog.py编译生产C++，ASan/UBSan下26例覆盖注册/匹配/
启动/权限转交、停用/重新启用、心跳推进、到期/无效deadline及清理；
3项行为突变拒绝（不再检测到期、吞原始拒绝、忽略已有服务）。IOKit
和原始函数在该层是显式mock，不将它写成手机注册通过。

ram_watchdog_original.py另执行未改的原始IOWatchdog指令，9例覆盖
错误type、已有client、非root、无/false entitlement、正常打开与
原客户端start、heartbeat/disable/enable字段更新；只对外部KPI提供
明确桩，原root和entitlement分支都实际执行。privilege名为root，
entitlement为com.apple.private.iowatchdog.user-access。

缓存准备的真实C模型另检查watchdog错误传播和不继续env/flags准备；
完整套件增加watchdog为第18项。检查档案成员白名单、编译对象引用、
链接符号、生成宏和源码/构建脚本副本，以及最终交付镜像绑定。

设备验收要区分：wdt=3只证明发布/匹配/启动；clients增加才证明原
权限和客户端打开，beats推进才证明真实心跳。越过旧lookup brk后还
可能遇到其他平台/文件系统依赖，不以取消关键退出panic代替修复。
最终哈希、完整退出码和新attempt见v46 REVIEW及usb-bulk追加日志。
