#!/usr/bin/env python3
"""Audit original RAM-disk Mach-O page hashes and original trust-cache coverage."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True)
p.add_argument('--trust-cache',type=Path,action='append',required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();root=a.root.resolve();trusted={};modules=[]
for path in a.trust_cache:
    raw=path.read_bytes();version,count=struct.unpack_from('<I',raw)[0],struct.unpack_from('<I',raw,20)[0]
    stride={1:22,2:24}[version];assert len(raw)==24+stride*count
    for i in range(count):
        at=24+i*stride;key=(raw[at:at+20].hex(),raw[at+20]);trusted.setdefault(key,[]).append(path.name)
    modules.append(dict(path=str(path),version=version,entries=count,sha256=hashlib.sha256(raw).hexdigest()))
files=[];failures=[]
for path in sorted(root.rglob('*')):
    if path.is_symlink() or not path.is_file():continue
    with path.open('rb') as stream:magic=stream.read(4)
    if magic!=b'\xcf\xfa\xed\xfe':continue
    raw=path.read_bytes();cpu,subtype,filetype,ncmds,sizeofcmds=struct.unpack_from('<5I',raw,4)
    assert cpu==0x100000c and 32+sizeofcmds<=len(raw)
    at=32;signature=None;dependencies=[]
    for i in range(ncmds):
        cmd,size=struct.unpack_from('<II',raw,at);assert size>=8 and at+size<=32+sizeofcmds
        if cmd==0x1d:signature=struct.unpack_from('<II',raw,at+8)
        if cmd in (0xc,0x80000018,0x8000001f,0x80000023):
            offset=struct.unpack_from('<I',raw,at+8)[0];dependencies.append(raw[at+offset:at+size].split(b'\0',1)[0].decode())
        at+=size
    relative='/'+str(path.relative_to(root));row=dict(path=relative,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),subtype=subtype,filetype=filetype,dependencies=dependencies)
    if signature is None:
        failures.append(dict(path=relative,reason='no embedded signature'));continue
    offset,size=signature;sig=raw[offset:offset+size];assert len(sig)==size
    magic,length,count=struct.unpack_from('>III',sig);assert magic==0xfade0cc0 and length<=size
    cds=[]
    for i in range(count):
        slot,start=struct.unpack_from('>II',sig,12+8*i);cmagic,clen=struct.unpack_from('>II',sig,start)
        if cmagic!=0xfade0c02:continue
        cd=sig[start:start+clen];assert len(cd)==clen
        hash_offset,nslots,limit=struct.unpack_from('>I',cd,16)[0],struct.unpack_from('>I',cd,28)[0],struct.unpack_from('>I',cd,32)[0]
        hash_size,hash_type,page_shift=cd[36],cd[37],cd[39]
        algo={1:'sha1',2:'sha256',3:'sha256',4:'sha384'}[hash_type]
        cdhash=hashlib.new(algo,cd).digest()[:20].hex();page_size=1<<page_shift
        assert 0<page_shift<=20 and limit<=len(raw) and nslots==(limit+page_size-1)//page_size
        assert hash_offset+nslots*hash_size<=len(cd)
        for page in range(nslots):
            expected=cd[hash_offset+page*hash_size:hash_offset+(page+1)*hash_size]
            actual=hashlib.new(algo,raw[page*page_size:min((page+1)*page_size,limit)]).digest()[:hash_size]
            if actual!=expected:raise ValueError(f'Page hash mismatch: {relative} page {page}')
        cds.append(dict(slot=slot,hash_type=hash_type,cdhash=cdhash,page_shift=page_shift,code_slots=nslots,page_hashes_valid=True,trust_cache_matches=trusted.get((cdhash,hash_type),[])))
    row.update(signature_offset=offset,signature_bytes=size,code_directories=cds)
    if not cds or not any(cd['trust_cache_matches'] for cd in cds):failures.append(dict(path=relative,reason='not covered by original modules'))
    files.append(row)
report=dict(root=str(root),modules=modules,files=files,macho_files=len(files),page_hashes_valid=True,failures=failures,all_covered=not failures)
a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(macho_files=len(files),page_hashes_valid=True,failures=failures,all_covered=not failures),indent=2))
if failures:raise SystemExit(1)
