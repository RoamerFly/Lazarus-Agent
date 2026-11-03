"""Build a team-owned APK that proves the audited embedded-native rule end to end.

The source APK contains a valid but crashing JNI proxy at ``lib/<abi>`` and a
same-name, symbol-compatible payload at ``assets/libs/<abi>``.  It does not use
any game code or third-party assets.  Its crash happens only after pressing the
button; the control/patched APK reaches the ordinary success UI instead.
"""
import os
from pathlib import Path
import shutil
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bench_device import SDK, TOOLS
from reviver import run, test_key
from asset_native_repair import replacement_map

SRC=ROOT/"fixtures/native-proxy"
PACKAGE="org.ggbond.nativeproxy"
ACTIVITY="org.ggbond.nativeproxy.NativeProxyActivity"
ABIS={"x86_64":"x86_64-linux-android21", "arm64-v8a":"aarch64-linux-android21"}
PROXY=r'''extern "C" __attribute__((visibility("default"))) int Java_org_ggbond_nativeproxy_NativeProxyActivity_nativeRun(void*,void*) {
  volatile int *null_pointer=(int*)0; *null_pointer=1; return -1;
}
extern "C" __attribute__((visibility("default"))) int nativeproxy_marker_one(){ return 1; }
extern "C" __attribute__((visibility("default"))) int nativeproxy_marker_two(){ return 2; }
__attribute__((used)) static const unsigned char fixture_padding[4096]={1};
'''
PAYLOAD=r'''extern "C" __attribute__((visibility("default"))) int Java_org_ggbond_nativeproxy_NativeProxyActivity_nativeRun(void*,void*) { return 7; }
extern "C" __attribute__((visibility("default"))) int nativeproxy_marker_one(){ return 1; }
extern "C" __attribute__((visibility("default"))) int nativeproxy_marker_two(){ return 2; }
__attribute__((used)) static const unsigned char fixture_padding[4096]={1};
'''

def android_jar():
    jars=sorted(SDK.glob("platforms/android-*/android.jar"),reverse=True)
    if not jars: raise RuntimeError("Android SDK platform android.jar is unavailable")
    return jars[0]

def compiler(target):
    ndk=next(iter(sorted(SDK.glob("ndk/*/toolchains/llvm/prebuilt/windows-x86_64/bin"))),None)
    if ndk:
        found=next(iter(ndk.glob(target+"-clang*")),None)
        if found:return found
    found=shutil.which("clang")
    if not found: raise RuntimeError("No Android NDK compiler or clang is available")
    return Path(found)

def native(source,output,target,soname,temp,env):
    code=temp/(output.stem+"-"+target+".cpp"); code.write_text(source,encoding="utf-8")
    cc=compiler(target)
    command=[cc]
    # The fallback clang path has no NDK sysroot.  These self-contained JNI
    # functions deliberately use no libc/Bionic symbols, so a freestanding
    # Android ELF is sufficient and makes the fixture reproducible here.
    if cc.name.lower().startswith(target.lower()): command += ["-fuse-ld=lld"]
    else: command += ["--target="+target,"-fuse-ld=lld"]
    command += ["-shared","-nostdlib","-fno-stack-protector","-fvisibility=hidden",
              "-Wl,-z,defs","-Wl,-z,norelro","-Wl,-soname,"+soname,"-o",output,code]
    run(command,env)
    data=output.read_bytes()
    if not data.startswith(b"\x7fELF"): raise RuntimeError("native compiler did not produce ELF")

def assemble(base,classes,libs,assets,output,temp,env):
    unsigned=temp/(output.stem+"-unsigned.apk")
    with ZipFile(unsigned,"w",ZIP_DEFLATED) as z:
        with ZipFile(base) as original:
            for entry in original.infolist(): z.writestr(entry,original.read(entry.filename))
        z.write(classes,"classes.dex")
        for abi,path in libs.items(): z.write(path,f"lib/{abi}/libnativeproxy.so")
        for abi,path in assets.items(): z.write(path,f"assets/libs/{abi}/libnativeproxy.so")
    aligned=temp/(output.stem+"-aligned.apk")
    run([TOOLS/"zipalign.exe","-f","4",unsigned,aligned],env)
    run(["java","-jar",TOOLS/"lib/apksigner.jar","sign","--ks",test_key(env),"--ks-pass","env:REVIVER_KEYPASS",
         "--v4-signing-enabled","false","--out",output,aligned],env)

def build():
    env=os.environ.copy(); jar=android_jar()
    with tempfile.TemporaryDirectory() as work:
        tmp=Path(work); classes=tmp/"classes"; classes.mkdir()
        run(["javac","-encoding","UTF-8","-source","8","-target","8","-classpath",jar,"-d",classes,SRC/"NativeProxyActivity.java"],env)
        run([TOOLS/"d8.bat","--min-api","21","--lib",jar,"--output",tmp,*classes.rglob("*.class")],env)
        base=tmp/"base.apk"
        run([TOOLS/"aapt2.exe","link","-I",jar,"--manifest",SRC/"AndroidManifest.xml","-o",base],env)
        proxies={}; payloads={}
        for abi,target in ABIS.items():
            proxy=tmp/(abi+"-proxy.so"); payload=tmp/(abi+"-payload.so")
            marker=f'__attribute__((used)) static const char embedded_path[]="/assets/libs/{abi}/libnativeproxy.so";'
            native(PROXY+marker,proxy,target,"libnativeproxy_proxy.so",tmp,env)
            native(PAYLOAD,payload,target,"libnativeproxy.so",tmp,env)
            proxies[abi]=proxy; payloads[abi]=payload
        broken=SRC/"native-proxy-broken.apk"; control=SRC/"native-proxy-control.apk"
        assemble(base,tmp/"classes.dex",proxies,payloads,broken,tmp,env)
        assemble(base,tmp/"classes.dex",payloads,payloads,control,tmp,env)
        mapped=replacement_map(broken)
        expected={f"lib/{abi}/libnativeproxy.so":f"assets/libs/{abi}/libnativeproxy.so" for abi in ABIS}
        if mapped != expected: raise RuntimeError("fixture did not meet audited replacement-map contract: "+repr(mapped))
    print(broken); print(control)

if __name__=="__main__": build()
