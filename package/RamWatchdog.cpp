// SPDX-License-Identifier: MIT
// RAM bring-up backend for the unchanged 21H461 IOWatchdog/client protocol.
// This is a software deadline observer, not a claim of Snapdragon hardware WDT.
#include "OriginalWatchdogABI.h"
#include <IOKit/IOLib.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/c++/OSSymbol.h>
#include <mach/kmod.h>
#include <kern/clock.h>
#include <kern/thread_call.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <sys/errno.h>

extern "C" {
extern kmod_info_t g_kernel_kmod_info;
kern_return_t OSKextLoadKextWithIdentifier(const char *);
void fuxi_watchdog_static_init(void) __asm__("__GLOBAL__sub_I_RamWatchdog.cpp");
bool fuxi_original_watchdog_start(IOWatchdog *, IOService *, unsigned, unsigned);
// stage, accepted clients, polls, original heartbeat sequence, enabled, deadline,
// backend enable count, backend disable count. RAM only, never a disk log.
volatile uint64_t fuxi_ram_watchdog_state[8];
int fuxi_ram_watchdog_prepare(void);
}

class RamWatchdog : public IOWatchdog {
    OSDeclareDefaultStructors(RamWatchdog)
    thread_call_t timer = nullptr;
    unsigned armed = 0;
    static void expired(thread_call_param_t parameter, thread_call_param_t) {
        auto *self = static_cast<RamWatchdog *>(parameter);
        if (!__atomic_load_n(&self->armed, __ATOMIC_ACQUIRE)) return;
        // These fields and their lock are qualified from the original methods
        // that enable/disable/pet the watchdog. Never write original state here.
        auto *lock = *reinterpret_cast<IOSimpleLock **>(reinterpret_cast<char *>(self) + 0xa0);
        if (!lock) panic("RAM_WATCHDOG missing original heartbeat lock");
        bool interrupts = ml_set_interrupts_enabled(false);
        IOSimpleLockLock(lock);
        bool enabled = *reinterpret_cast<unsigned char *>(reinterpret_cast<char *>(self) + 0xa8);
        uint64_t sequence = *reinterpret_cast<uint64_t *>(reinterpret_cast<char *>(self) + 0xb8);
        uint64_t deadline = *reinterpret_cast<uint64_t *>(reinterpret_cast<char *>(self) + 0xc8);
        uint64_t now = mach_absolute_time();
        IOSimpleLockUnlock(lock);
        ml_set_interrupts_enabled(interrupts);
        __atomic_add_fetch(&fuxi_ram_watchdog_state[2], 1, __ATOMIC_RELAXED);
        fuxi_ram_watchdog_state[3] = sequence;
        fuxi_ram_watchdog_state[4] = enabled;
        fuxi_ram_watchdog_state[5] = deadline;
        if (enabled && (!deadline || now >= deadline))
            panic("RAM_WATCHDOG software deadline expired: beats=%llu now=%llx deadline=%llx",
                sequence, now, deadline);
        if (__atomic_load_n(&self->armed, __ATOMIC_ACQUIRE)) self->schedule();
    }
    void schedule() {
        uint64_t next;
        clock_interval_to_deadline(1, NSEC_PER_SEC, &next);
        thread_call_enter_delayed(timer, next);
    }
public:
    bool prepareTimer() {
        timer = thread_call_allocate(expired, this);
        return timer != nullptr;
    }
    bool start(IOService *provider) override {
        // Same option arguments as the original hardware subclass's zero-flag
        // path; no boot-arg change to the original monitoring/permission policy.
        return timer && fuxi_original_watchdog_start(this, provider, 0, 0);
    }
    void stop(IOService *provider) override {
        IOWatchdog::stop(provider);
    }
    void free() override {
        __atomic_store_n(&armed, 0, __ATOMIC_RELEASE);
        if (timer) { thread_call_cancel_wait(timer); thread_call_free(timer); timer = nullptr; }
        IOService::free();
    }
    IOReturn newUserClient(task_t task, void *security, UInt32 type,
                          OSDictionary *properties, IOUserClient **client) override {
        IOReturn result = IOWatchdog::newUserClient(task, security, type, properties, client);
        if (result == kIOReturnSuccess && client && *client)
            __atomic_add_fetch(&fuxi_ram_watchdog_state[1], 1, __ATOMIC_RELAXED);
        return result;
    }
    // Exact three abstract slots of the original IOWatchdog, after IOService.
    // The deadline is owned by its original heartbeat/enable/disable handlers.
    void enableBackend() override {
        __atomic_store_n(&armed, 1, __ATOMIC_RELEASE);
        __atomic_add_fetch(&fuxi_ram_watchdog_state[6], 1, __ATOMIC_RELAXED);
        schedule();
    }
    void disableBackend() override {
        __atomic_store_n(&armed, 0, __ATOMIC_RELEASE);
        __atomic_add_fetch(&fuxi_ram_watchdog_state[7], 1, __ATOMIC_RELAXED);
        if (timer) thread_call_cancel(timer);
    }
    void restartBackend() override {
        // Re-evaluate the actual original deadline; do not manufacture a pet.
        if (__atomic_load_n(&armed, __ATOMIC_ACQUIRE)) schedule();
    }
};
OSDefineMetaClassAndStructors(RamWatchdog, IOWatchdog)

static RamWatchdog *watchdog; // Published service retained for this RAM boot.
static const OSMetaClass *lookup(const char *name) {
    const OSSymbol *key = OSSymbol::withCString(name);
    const OSMetaClass *meta = key ? OSMetaClass::getMetaClassWithName(key) : nullptr;
    OSSafeReleaseNULL(key);
    return meta;
}
static int countServices(IOService *expected) {
    OSDictionary *match = IOService::serviceMatching("IOWatchdog");
    if (!match) return -ENOMEM;
    OSIterator *iterator = IOService::getMatchingServices(match);
    match->release();
    if (!iterator) return 0;
    int count = 0;
    bool owns = expected == nullptr;
    while (OSObject *object = iterator->getNextObject()) {
        ++count;
        if (object == expected) owns = true;
    }
    bool valid = iterator->isValid();
    iterator->release();
    if (!valid) return -EAGAIN;
    return owns ? count : -ENXIO;
}
extern "C" int fuxi_ram_watchdog_prepare(void) {
    unsigned enabled = 0;
    if (!PE_parse_boot_argn("ram-system-watchdog", &enabled, sizeof(enabled)) || enabled != 1 || watchdog)
        return EINVAL;
    if (OSKextLoadKextWithIdentifier("com.apple.driver.AppleARMWatchdogTimer")) return ENXIO;
    fuxi_ram_watchdog_state[0] = 1;
    const OSMetaClass *base = lookup("IOWatchdog"), *client = lookup("IOWatchdogUserClient");
    if (!base || base != &IOWatchdog::gMetaClass || base->getClassSize() != 0xf8 ||
        !client || client->getClassSize() != 0xe8) return ENOEXEC;
    int before = countServices(nullptr);
    if (before != 0) return before < 0 ? -before : EEXIST;
    void *handle = OSMetaClass::preModLoad(g_kernel_kmod_info.name);
    if (!handle) return ENOMEM;
    fuxi_watchdog_static_init();
    if (OSMetaClass::postModLoad(handle)) return ENOEXEC;
    IOService *provider = IOService::getResourceService();
    watchdog = new RamWatchdog;
    if (!watchdog || !provider || !watchdog->init() || !watchdog->prepareTimer()) return ENOMEM;
    watchdog->setProperty("UniXNUWatchdogBackend", "software-original-deadline");
    watchdog->setProperty("HardwareWatchdogImplemented", false);
    if (!watchdog->attach(provider)) return ENXIO;
    if (!watchdog->start(provider)) return ENXIO;
    fuxi_ram_watchdog_state[0] = 2;
    if (countServices(watchdog) != 1) return ENXIO;
    fuxi_ram_watchdog_state[0] = 3;
    IOLog("RAM_WATCHDOG registered matched started: original IOWatchdog/client; software deadline backend\n");
    return 0;
}
