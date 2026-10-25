#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdarg>
#include <cstdio>
#include <string>
#include <vector>
#include <stdexcept>
using UInt32=unsigned;using task_t=void*;using kern_return_t=int;using IOReturn=int;
constexpr int kIOReturnSuccess=0,kIOReturnNotPrivileged=0xe00002c1,kIOReturnBadArgument=0xe00002c2;
constexpr unsigned NSEC_PER_SEC=1000000000;
struct Panic:std::runtime_error{using std::runtime_error::runtime_error;};
static void panic(const char *fmt,...) {throw Panic(fmt);}
static void IOLog(const char *,...) {}
struct IOSimpleLock {bool held=false;};
static IOSimpleLock originalLock;
static void IOSimpleLockLock(IOSimpleLock *l){assert(!l->held);l->held=true;}
static void IOSimpleLockUnlock(IOSimpleLock *l){assert(l->held);l->held=false;}
static bool interrupts=true;
static bool ml_set_interrupts_enabled(bool b){bool old=interrupts;interrupts=b;return old;}
static uint64_t now=1000000000;
static uint64_t mach_absolute_time(){return now;}
using thread_call_param_t=void*;
struct Timer{void(*fn)(void*,void*);void *parameter;bool active=false;uint64_t when=0;};
using thread_call_t=Timer*;
static Timer *lastTimer;
static bool timerFail;
static thread_call_t thread_call_allocate(void(*fn)(void*,void*),void *p){if(timerFail)return nullptr;return lastTimer=new Timer{fn,p};}
static void clock_interval_to_deadline(unsigned n,unsigned scale,uint64_t *t){assert(scale==NSEC_PER_SEC);*t=now+(uint64_t)n*scale;}
static void thread_call_enter_delayed(Timer *t,uint64_t when){assert(t);t->active=true;t->when=when;}
static void thread_call_cancel(Timer *t){t->active=false;}
static void thread_call_cancel_wait(Timer *t){t->active=false;}
static void thread_call_free(Timer *t){if(lastTimer==t)lastTimer=nullptr;delete t;}
class OSObject {public:virtual ~OSObject()=default;virtual void release(){delete this;}};
class OSSymbol:public OSObject {public:std::string text;explicit OSSymbol(const char *t):text(t){}static const OSSymbol *withCString(const char*t){return new OSSymbol(t);}};
static bool badBase,badClient,preFail,postFail,iteratorInvalid,initFail,attachFail,startFail,privileged=true;
static int loadError,bootArg=1,originalOpenCalls;
class OSMetaClass {unsigned size;public:explicit OSMetaClass(unsigned n):size(n){}unsigned getClassSize()const{return size;}static const OSMetaClass *getMetaClassWithName(const OSSymbol*);static void *preModLoad(const char*){return preFail?nullptr:(void*)1;}static int postModLoad(void*p){assert(p);return postFail?1:0;}};
class OSDictionary:public OSObject{};
class IOUserClient:public OSObject{};
static IOUserClient originalClient;
class IOService;
static std::vector<IOService*> services;
class OSIterator:public OSObject {unsigned at=0;public:OSObject *getNextObject();bool isValid(){return !iteratorInvalid;}};
class IOService:public OSObject {
 char opaque[128]{};
public:
 virtual bool init(){return !initFail;}
 virtual bool start(IOService*){return !startFail;}
 virtual void stop(IOService*){}
 virtual void free(){delete this;}
 virtual IOReturn newUserClient(task_t,void*,UInt32,OSDictionary*,IOUserClient**){return -1;}
 bool attach(IOService*){return !attachFail;}
 void registerService(){services.push_back(this);}
 void setProperty(const char*,const char*){}
 void setProperty(const char*,bool){}
 static IOService *getResourceService(){static IOService resource;return &resource;}
 static OSDictionary *serviceMatching(const char *name){assert(!strcmp(name,"IOWatchdog"));return new OSDictionary;}
 static OSIterator *getMatchingServices(OSDictionary*){return new OSIterator;}
};
static_assert(sizeof(IOService)==136);
OSObject *OSIterator::getNextObject(){return at<services.size()?services[at++]:nullptr;}
class IOWatchdog:public IOService {
 char originalPrivate[112]{};
public:
 static OSMetaClass gMetaClass;
 void stop(IOService*)override {disableBackend();}
 IOReturn newUserClient(task_t,void*,UInt32 type,OSDictionary*,IOUserClient**client)override {
  originalOpenCalls++;if(!privileged)return kIOReturnNotPrivileged;if(type!=1)return kIOReturnBadArgument;*client=&originalClient;return 0;
 }
 virtual void enableBackend()=0;virtual void disableBackend()=0;virtual void restartBackend()=0;
};
OSMetaClass IOWatchdog::gMetaClass{248};
static_assert(sizeof(IOWatchdog)==248);
const OSMetaClass *OSMetaClass::getMetaClassWithName(const OSSymbol *s){static OSMetaClass client{232},bad{247};if(s->text=="IOWatchdog")return badBase?&bad:&IOWatchdog::gMetaClass;return badClient?&bad:&client;}
#define OSSafeReleaseNULL(p) do {delete p;p=nullptr;}while(0)
#define OSDeclareDefaultStructors(T) public:static OSMetaClass gMetaClass;
#define OSDefineMetaClassAndStructors(T,B) OSMetaClass T::gMetaClass{sizeof(T)};
struct kmod_info_t{const char *name;};
extern "C" kmod_info_t g_kernel_kmod_info={"kernel"};
extern "C" int OSKextLoadKextWithIdentifier(const char *s){assert(!strcmp(s,"com.apple.driver.AppleARMWatchdogTimer"));return loadError;}
static bool PE_parse_boot_argn(const char *name,void *out,int n){assert(!strcmp(name,"ram-system-watchdog")&&n==4);memcpy(out,&bootArg,4);return true;}
static void setField(IOWatchdog *w,unsigned offset,uint64_t v){memcpy((char*)w+offset,&v,8);}
extern "C" bool fuxi_original_watchdog_start(IOWatchdog *w,IOService *provider,unsigned unused,unsigned flags){
 assert(!unused&&!flags&&provider);if(startFail)return false;
 IOSimpleLock *lock=&originalLock;memcpy((char*)w+0xa0,&lock,sizeof(lock));*((char*)w+0xa8)=1;
 setField(w,0xb8,0);setField(w,0xc8,now+600ULL*NSEC_PER_SEC);w->enableBackend();w->registerService();return true;
}
