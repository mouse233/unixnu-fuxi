#!/usr/bin/env python3
"""Compile the actual kernel's restore/static selection blocks for both modes."""
import argparse
from pathlib import Path
import subprocess
import tempfile

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--trust-source',type=Path,required=True)
a=p.parse_args();source=a.trust_source.read_text()
start=source.index('#if defined(XNU_RAM_RESTORE_TRUST)')
end=source.index('#endif',start)+len('#endif');hook=source[start:end]
start=source.index('#if !defined(XNU_RAM_RESTORE_TRUST)');end=source.index('#endif',start)+len('#endif');selection=source[start:end]
assert source.index(hook)<source.index('for (uint32_t i = 0; i < tc_offsets->num_caches; i++)')
api=r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
typedef struct {int loaded;} TrustCache_t;
typedef struct {int unused;} TrustCacheRuntime_t;
typedef int TCType_t;
#define kTCTypeStatic 0
#define kTCTypeEngineering 1
static TrustCache_t trust_cache_static0;
static TrustCacheRuntime_t runtime,*trust_cache_rt=&runtime;
void fuxi_ram_restore_trust_load(TrustCacheRuntime_t *rt,TrustCache_t *cache) {
    assert(rt==&runtime && cache==&trust_cache_static0 && !cache->loaded);cache->loaded=267;
}
static void before_external_modules(void) {
    (void)trust_cache_rt;
HOOK
}
static void classify(uint32_t i) {
    TCType_t tc_type=kTCTypeEngineering;TrustCache_t *trust_cache=NULL;
SELECTION
    if(XNU_RAM_RESTORE_TRUST) assert(tc_type==kTCTypeEngineering && trust_cache==NULL);
    else if(i==0) assert(tc_type==kTCTypeStatic && trust_cache==&trust_cache_static0);
    else assert(tc_type==kTCTypeEngineering && trust_cache==NULL);
}
int main(void) {
    before_external_modules();assert(trust_cache_static0.loaded==(XNU_RAM_RESTORE_TRUST?267:0));
    classify(0);classify(1);
    puts("PASS: actual kernel blocks, original restore owns first static wrapper; both external modules retained; normal mode preserved");
}
'''.replace('HOOK',hook).replace('SELECTION',selection)
with tempfile.TemporaryDirectory(prefix='ram-trust-handoff-') as temporary:
    work=Path(temporary);(work/'test.c').write_text(api)
    for enabled in (0,1):
        subprocess.run(['clang','-Wall','-Wextra','-Werror','-DXNU_RAM_RESTORE_TRUST='+str(enabled),str(work/'test.c'),'-o',str(work/'test')],check=True)
        subprocess.run([str(work/'test')],check=True)
