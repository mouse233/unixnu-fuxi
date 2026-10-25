extern "C" void fuxi_watchdog_static_init(void) {}
static unsigned cases;
static void reset(){
 if(watchdog){watchdog->free();watchdog=nullptr;}services.clear();
 badBase=badClient=preFail=postFail=iteratorInvalid=initFail=attachFail=startFail=timerFail=false;
 privileged=true;loadError=0;bootArg=1;now=NSEC_PER_SEC;originalLock.held=false;interrupts=true;
 memset((void*)fuxi_ram_watchdog_state,0,sizeof(fuxi_ram_watchdog_state));
}
static void poll(){assert(lastTimer&&lastTimer->active);auto *t=lastTimer;t->active=false;t->fn(t->parameter,nullptr);}
int main(){
 reset();bootArg=0;assert(fuxi_ram_watchdog_prepare()==EINVAL);cases++;
 reset();loadError=1;assert(fuxi_ram_watchdog_prepare()==ENXIO);cases++;
 reset();badBase=true;assert(fuxi_ram_watchdog_prepare()==ENOEXEC);cases++;
 reset();badClient=true;assert(fuxi_ram_watchdog_prepare()==ENOEXEC);cases++;
 reset();services.push_back(IOService::getResourceService());assert(fuxi_ram_watchdog_prepare()==EEXIST);cases++;
 reset();iteratorInvalid=true;assert(fuxi_ram_watchdog_prepare()==EAGAIN);cases++;
 reset();preFail=true;assert(fuxi_ram_watchdog_prepare()==ENOMEM);cases++;
 reset();postFail=true;assert(fuxi_ram_watchdog_prepare()==ENOEXEC);cases++;
 reset();initFail=true;assert(fuxi_ram_watchdog_prepare()==ENOMEM);cases++;
 reset();timerFail=true;assert(fuxi_ram_watchdog_prepare()==ENOMEM);cases++;
 reset();attachFail=true;assert(fuxi_ram_watchdog_prepare()==ENXIO);cases++;
 reset();startFail=true;assert(fuxi_ram_watchdog_prepare()==ENXIO);cases++;
 reset();assert(!fuxi_ram_watchdog_prepare());assert(fuxi_ram_watchdog_state[0]==3&&countServices(watchdog)==1);cases++;
 assert(fuxi_ram_watchdog_prepare()==EINVAL);cases++;
 IOUserClient *client=nullptr;privileged=false;assert(watchdog->newUserClient(nullptr,nullptr,1,nullptr,&client)==kIOReturnNotPrivileged&&!client);cases++;
 privileged=true;assert(watchdog->newUserClient(nullptr,nullptr,2,nullptr,&client)==kIOReturnBadArgument&&!client);cases++;
 assert(!watchdog->newUserClient(nullptr,nullptr,1,nullptr,&client)&&client==&originalClient&&fuxi_ram_watchdog_state[1]==1);cases++;
 assert(originalOpenCalls>=3);cases++;
 poll();assert(lastTimer->active&&fuxi_ram_watchdog_state[2]==1&&fuxi_ram_watchdog_state[4]==1);cases++;
 setField(watchdog,0xb8,17);setField(watchdog,0xc8,now+90ULL*NSEC_PER_SEC);now+=NSEC_PER_SEC;poll();assert(fuxi_ram_watchdog_state[3]==17);cases++;
 *((char*)watchdog+0xa8)=0;now+=1000ULL*NSEC_PER_SEC;poll();assert(lastTimer->active&&fuxi_ram_watchdog_state[4]==0);cases++;
 *((char*)watchdog+0xa8)=1;setField(watchdog,0xc8,now+90ULL*NSEC_PER_SEC);poll();assert(lastTimer->active);cases++;
 now+=91ULL*NSEC_PER_SEC;bool caught=false;try{poll();}catch(const Panic &p){caught=strstr(p.what(),"software deadline expired");}assert(caught&&!originalLock.held&&interrupts);cases++;
 reset();assert(!fuxi_ram_watchdog_prepare());setField(watchdog,0xc8,0);caught=false;try{poll();}catch(const Panic&){caught=true;}assert(caught);cases++;
 reset();assert(!fuxi_ram_watchdog_prepare());watchdog->stop(IOService::getResourceService());assert(!lastTimer->active&&fuxi_ram_watchdog_state[7]==1);cases++;
 reset();assert(!lastTimer);cases++;
 printf("PASS %u actual C++ watchdog lifecycle/deadline/refusal cases; original APIs explicitly mocked\n",cases);
}
