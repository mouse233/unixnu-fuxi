#!/usr/bin/env python3
"""Exercise production restore-module loading, exact bytes and refusal behavior."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--module',type=Path,required=True)
a=p.parse_args();root=Path(__file__).resolve().parents[1];raw=a.module.read_bytes()
assert len(raw)==5898 and hashlib.sha256(raw).hexdigest()=='54e80085f2de19d76f870aa63366ef520a905913dc0208381010864f7d1fbb09'
header='static uint8_t ram_restore_original[5898] = {'+','.join(hex(x) for x in raw)+'};\n'
header+='static const uint8_t ram_restore_sha256[32] = {'+','.join(hex(x) for x in hashlib.sha256(raw).digest())+'};\n'
api=r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <setjmp.h>
#include <CommonCrypto/CommonDigest.h>
#define SHA256_CTX CC_SHA256_CTX
#define SHA256_Init CC_SHA256_Init
#define SHA256_Final CC_SHA256_Final
#define SHA256_Update(c,p,n) CC_SHA256_Update(c,p,(CC_LONG)(n))
#define Z_WAITOK_ZERO 1
#define kTCTypeStatic 0
#define kTCQueryTypeAll 0
#define kTCReturnSuccess 0
typedef struct {int allowEngineeringTC;} TrustCacheRuntime_t;
typedef struct {int unused;} TrustCache_t;
typedef struct {int unused;} TrustCacheQueryToken_t;
typedef struct {unsigned component,error,uniqueError;} TCReturn_t;
static int selector=4,load_error,query_error,loads,queries;
static uintptr_t loaded_module;
static size_t loaded_size;
static char message[1024];static jmp_buf caught;
static void panic(const char *fmt,...) __attribute__((noreturn));
static void panic(const char *fmt,...) {va_list ap;va_start(ap,fmt);vsnprintf(message,sizeof(message),fmt,ap);va_end(ap);longjmp(caught,1);}
static int PE_parse_boot_argn(const char *key,void *out,size_t n) {if(strcmp(key,"ram-root-probe")||n!=4) abort();memcpy(out,&selector,n);return 1;}
static TCReturn_t load_module(TrustCacheRuntime_t *rt,int type,TrustCache_t *cache,uintptr_t module,size_t size) {
    if(!rt || !cache || type!=0 || size!=5898) abort();
    const uint8_t *p=(const uint8_t *)module;if(p[0]!=1 || p[20]!=11 || p[21]!=1) abort();
    loads++;loaded_module=module;loaded_size=size;return (TCReturn_t){.error=load_error};
}
static TCReturn_t query_module(TrustCacheRuntime_t *rt,int type,const uint8_t *hash,TrustCacheQueryToken_t *token) {
    if(!rt || type || !token || loaded_size!=5898) abort();queries++;
    const uint8_t *module=(const uint8_t *)loaded_module;
    if(query_error) return (TCReturn_t){.error=3};
    for(unsigned i=0;i<267;i++) if(!memcmp(hash,module+24+22*i,20)) return (TCReturn_t){0};
    return (TCReturn_t){.error=3};
}
static struct {
    struct {unsigned version;TCReturn_t (*loadModule)(TrustCacheRuntime_t *,int,TrustCache_t *,uintptr_t,size_t);TCReturn_t (*query)(TrustCacheRuntime_t *,int,const uint8_t *,TrustCacheQueryToken_t *);} TrustCache;
} implementation={{2,load_module,query_module}},*amfi=&implementation;
'''
body='\n'.join(line for line in (root/'package/RamRestoreTrust.c').read_text().splitlines() if not line.startswith('#include'))
tests=r'''
#define REQUIRE(x) do {if(!(x)) {fprintf(stderr,"assertion %d\n",__LINE__);exit(1);}}while(0)
static int cases;
#define REFUSE(text,expr) do {message[0]=0;if(!setjmp(caught)) {expr;REQUIRE(0);} REQUIRE(strstr(message,text));cases++;}while(0)
int main(void) {
    TrustCacheRuntime_t runtime={1};TrustCache_t wrapper={0};
    selector=3;REFUSE("refused runtime/profile",fuxi_ram_restore_trust_load(&runtime,&wrapper));selector=4;
    fuxi_ram_restore_trust_loaded=267;REFUSE("refused runtime/profile",fuxi_ram_restore_trust_load(&runtime,&wrapper));fuxi_ram_restore_trust_loaded=0;
    REFUSE("refused runtime/profile",fuxi_ram_restore_trust_load(NULL,&wrapper));
    runtime.allowEngineeringTC=0;REFUSE("refused runtime/profile",fuxi_ram_restore_trust_load(&runtime,&wrapper));runtime.allowEngineeringTC=1;
    amfi=NULL;REFUSE("refused runtime/profile",fuxi_ram_restore_trust_load(&runtime,&wrapper));amfi=&implementation;
    amfi->TrustCache.version=1;REFUSE("refused runtime/profile",fuxi_ram_restore_trust_load(&runtime,&wrapper));amfi->TrustCache.version=2;
    ram_restore_original[0]^=1;REFUSE("SHA256 mismatch",fuxi_ram_restore_trust_load(&runtime,&wrapper));ram_restore_original[0]^=1;
    ram_restore_original[5897]^=1;REFUSE("SHA256 mismatch",fuxi_ram_restore_trust_load(&runtime,&wrapper));ram_restore_original[5897]^=1;
    REQUIRE(loads==0 && queries==0 && fuxi_ram_restore_trust_loaded==0);
    REFUSE("refused runtime/profile",fuxi_ram_restore_trust_load(&runtime,NULL));
    load_error=7;REFUSE("original module rejected",fuxi_ram_restore_trust_load(&runtime,&wrapper));load_error=0;
    REQUIRE(fuxi_ram_restore_trust_loaded==0);
    query_error=1;REFUSE("entry query failed",fuxi_ram_restore_trust_load(&runtime,&wrapper));query_error=0;
    REQUIRE(fuxi_ram_restore_trust_loaded==0);
    loads=queries=0;fuxi_ram_restore_trust_load(&runtime,&wrapper);
    REQUIRE(fuxi_ram_restore_trust_loaded==267 && loads==1 && queries==267);cases++;
    REFUSE("refused runtime/profile",fuxi_ram_restore_trust_load(&runtime,&wrapper));
    printf("PASS: production restore loader, unchanged v1 bytes, all 267 queries, %d guard/behavior cases\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='ram-restore-trust-') as temporary:
    work=Path(temporary);(work/'test.c').write_text(api+header+body+tests)
    subprocess.run(['clang','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations',str(work/'test.c'),'-o',str(work/'test')],check=True)
    result=subprocess.run([str(work/'test')],check=True,capture_output=True,text=True)
    out=root/'out/ram-progress';out.mkdir(parents=True,exist_ok=True);(out/'restore-trust-tests.log').write_text(result.stdout)
    print(result.stdout,end='')
