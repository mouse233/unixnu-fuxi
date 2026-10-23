#!/usr/bin/env python3
"""Compile the actual patched XNU exec-release block and verify callback ordering."""
import argparse
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--exec-source', type=Path, required=True)
args = parser.parse_args()
source = args.exec_source.read_text()
release = source.index('/* wake up the new exec thread */')
start = source.rfind('#if defined(XNU_RAM_USER_ENTRY)', 0, release)
end = source.index('thread_deallocate(imgp->ip_new_thread);', release) + len('thread_deallocate(imgp->ip_new_thread);')
assert start > source.index('int\n__mac_execve(')
block = source[start:end]
assert 'fuxi_ram_user_exec_ready(imgp->ip_new_thread, proc_getpid(p));' in block
assert block.index('fuxi_ram_user_exec_ready') < block.index('task_clear_return_wait') < block.index('thread_deallocate')
harness = r'''
#include <assert.h>
#include <stdio.h>
typedef void *thread_t;
static int waiting, called, dropped, expected;
static thread_t replacement=(void *)3;
static int proc_getpid(int *p) {return *p;}
void fuxi_ram_user_exec_ready(thread_t target,int pid) {
    assert(waiting && target==replacement && pid==1 && !called && !dropped);called++;
}
static thread_t get_threadtask(thread_t t) {assert(t==replacement);return t;}
static void task_clear_return_wait(thread_t t,unsigned flags) {
    assert(t==replacement && flags==0x20 && waiting && called==expected);waiting=0;
}
static void thread_deallocate(thread_t t) {assert(t==replacement && !waiting);dropped++;}
static void release_exec(int error,int exec_done,int *p) {
    struct {thread_t ip_new_thread;} image={replacement},*imgp=&image;
    unsigned clearwait_flags=0x20;
    (void)error;(void)exec_done;(void)p;
BLOCK
}
int main(void) {
    int scenarios[][3]={{0,1,1},{13,1,1},{0,0,1},{0,1,2},{13,0,1}};
    for(unsigned i=0;i<sizeof(scenarios)/sizeof(scenarios[0]);i++) {
        int error=scenarios[i][0],done=scenarios[i][1],pid=scenarios[i][2];
        waiting=1;called=dropped=0;
        expected=XNU_RAM_USER_ENTRY && !error && done && pid==1;
        release_exec(error,done,&pid);
        assert(called==expected && !waiting && dropped==1);
    }
    puts("PASS: actual XNU release block, 5 handoff/error scenarios, before FINAL_WAIT release");
}
'''.replace('BLOCK', block)
with tempfile.TemporaryDirectory(prefix='ram-exec-handoff-') as temporary:
    work = Path(temporary)
    (work/'test.c').write_text(harness)
    for enabled in (0,1):
        subprocess.run(['clang','-Wall','-Wextra','-Werror','-Wno-unused-function',
            '-DXNU_RAM_USER_ENTRY='+str(enabled),str(work/'test.c'),'-o',str(work/'test')],check=True)
        subprocess.run([str(work/'test')],check=True)
