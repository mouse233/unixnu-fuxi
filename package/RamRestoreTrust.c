/* Load the exact original restore module through AMFI; never change its entries. */
#include <sys/types.h>
#include <sys/systm.h>
#include <pexpert/pexpert.h>
#include <libkern/crypto/sha2.h>
#include <libkern/amfi/amfi.h>
#include <TrustCache/API.h>
#include "RamRestoreTrustPins.h"

unsigned fuxi_ram_restore_trust_loaded;
void fuxi_ram_restore_trust_load(TrustCacheRuntime_t *, TrustCache_t *);

void fuxi_ram_restore_trust_load(TrustCacheRuntime_t *runtime, TrustCache_t *cache)
{
    unsigned selector = 0;
    uint8_t digest[32];
    SHA256_CTX hash;
    if (!PE_parse_boot_argn("ram-root-probe", &selector, sizeof(selector)) || selector != 4 ||
        fuxi_ram_restore_trust_loaded || !runtime || !cache || !runtime->allowEngineeringTC ||
        !amfi || amfi->TrustCache.version < 2)
        panic("RAM_RESTORE_TRUST refused runtime/profile");
    SHA256_Init(&hash);
    SHA256_Update(&hash, ram_restore_original, sizeof(ram_restore_original));
    SHA256_Final(digest, &hash);
    if (memcmp(digest, ram_restore_sha256, sizeof(digest)))
        panic("RAM_RESTORE_TRUST original SHA256 mismatch");
    /* Match XNU restore boot semantics: the original restore module is the
     * first static cache. The caller supplies the existing read-only-late
     * wrapper before its external v2 modules are loaded. Never convert or
     * manufacture entries, change policy, or retry with another cache type. */
    TCReturn_t result = amfi->TrustCache.loadModule(runtime, kTCTypeStatic,
        cache, (uintptr_t)ram_restore_original, sizeof(ram_restore_original));
    if (result.error != kTCReturnSuccess)
        panic("RAM_RESTORE_TRUST original module rejected: component=%u error=%u unique=%u",
            result.component, result.error, result.uniqueError);
    for (unsigned i = 0; i < 267; i++) {
        TrustCacheQueryToken_t token = {0};
        result = amfi->TrustCache.query(runtime, kTCQueryTypeAll, ram_restore_original + 24 + i * 22, &token);
        if (result.error != kTCReturnSuccess)
            panic("RAM_RESTORE_TRUST entry query failed: entry=%u error=%u", i, result.error);
    }
    fuxi_ram_restore_trust_loaded = 267;
}
