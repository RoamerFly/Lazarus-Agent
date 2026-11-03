"""Serve the provisioned, offline ReDroid ARM VM on its reserved loopback ADB port."""
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_device import ADB, ARM_LAB, ARM_PAIRING, LAB

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SSH = ["wsl", "-d", "Ubuntu-New", "--exec", "ssh", "-T", "-p", "19237",
       "-i", "/home/fly/reviver-lab/lab-key", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
       "-o", "UserKnownHostsFile=/home/fly/reviver-lab/arm64-known-hosts",
       "-o", "StrictHostKeyChecking=yes", "tester@127.0.0.1"]
BOOT = """set -eu
q=$HOME/reviver-lab/qemu
export LD_LIBRARY_PATH=$q/usr/lib/x86_64-linux-gnu
export QEMU_MODULE_DIR=$q/usr/lib/x86_64-linux-gnu/qemu
exec "$q/usr/bin/qemu-system-aarch64" -name reviver-redroid-arm64 -machine virt,gic-version=3 \
 -accel tcg,thread=multi -cpu cortex-a72 -smp 4 -m 4096 -L "$q/usr/share/qemu" \
 -bios "$q/usr/share/qemu-efi-aarch64/QEMU_EFI.fd" \
 -drive if=virtio,file=guest-arm64-overlay.qcow2,format=qcow2 \
 -drive if=virtio,file=seed.iso,format=raw,readonly=on \
 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:19237-:22 -device virtio-net-device,netdev=n1 \
 -device virtio-rng-device -display none -serial file:arm64-serial.log -monitor none -no-reboot
"""
BRIDGE = """import os,socket,sys,threading
sock=socket.create_connection(('127.0.0.1',5555))
def send_to_android():
    try:
        while data:=os.read(0,65536):
            sock.sendall(data)
    finally:
        sock.shutdown(socket.SHUT_WR)
threading.Thread(target=send_to_android,daemon=True).start()
while data:=sock.recv(65536):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
"""


def guest(script, timeout):
    return subprocess.run(SSH + ["sudo sh -s"], input=script.encode(), capture_output=True, timeout=timeout, creationflags=NO_WINDOW)


def prepare_guest():
    lab = LAB / "redroid16"
    listeners = subprocess.check_output(["wsl","-d","Ubuntu-New","--exec","ss","-H","-ltn","sport","=",":19237"],
                                        timeout=30, creationflags=NO_WINDOW)
    if not listeners.strip():
        if not all((lab/name).is_file() for name in ("guest-arm64-overlay.qcow2","debian-12-generic-arm64.qcow2","seed.iso")):
            raise RuntimeError("ARM 实验室磁盘尚未配置")
        with (lab/"workbench-vm.log").open("ab") as log:
            subprocess.Popen(["wsl","-d","Ubuntu-New","--cd",str(lab),"--exec","sh","-c",BOOT],
                             stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=NO_WINDOW)
    for _ in range(75):
        if guest("test \"$(uname -m)\" = aarch64", 15).returncode == 0:
            break
        time.sleep(4)
    else:
        raise RuntimeError("无法使用配对的 SSH 主机密钥连接 ARM 虚拟机")
    setup = """set -eu
systemctl start docker
test "$(docker inspect -f '{{.HostConfig.NetworkMode}} {{.Config.Image}}' reviver16-arm64)" = 'none reviver-arm64:36'
modprobe binder_linux devices=binder,hwbinder,vndbinder
test -c /dev/ashmem || insmod "/var/lib/reviver/ashmem/$(uname -r).ko"
for name in binder hwbinder vndbinder ashmem; do test -c "/dev/$name"; chmod a+rw "/dev/$name"; done
if [ "$(docker inspect -f '{{.State.Running}}' reviver16-arm64)" != true ]; then docker start reviver16-arm64 >/dev/null; fi
docker inspect -f '{{.Id}}' reviver16-arm64
"""
    result = guest(setup, 240)
    identity = result.stdout.decode().strip()
    if result.returncode or not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise RuntimeError("ARM 容器或驱动未就绪")
    marker = ARM_PAIRING.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{64}", marker):
        raise RuntimeError("ARM 配对标记无效")
    for _ in range(110):
        # The existing data volume is paired out of band via the pinned SSH guest.
        props = guest(f"""docker exec {identity} /system/bin/sh -c '
test "$(cat /data/local/tmp/.reviver-instance)" = {marker} || exit 1
test "$(getprop ro.product.device)" = redroid_arm64 || exit 1
test -n "$(getprop ro.hw_timeout_multiplier)" || setprop ro.hw_timeout_multiplier 10
setprop audio.timecheck.timeout_duration_ms 60000
test "$(getprop init.svc.adbd)" = running'""", 60)
        if props.returncode == 0:
            return identity
        time.sleep(4)
    raise RuntimeError("配对的 Android 系统未启动 adbd")


def forward(connection, identity):
    remote = ("set -eu; test \"$(docker inspect -f '{{.HostConfig.NetworkMode}}' "+identity+")\" = none; "
              "p=$(docker inspect -f '{{.State.Pid}}' "+identity+"); test \"$p\" -gt 1; "
              "exec nsenter -t \"$p\" -n python3 -c "+shlex.quote(BRIDGE))
    with connection, subprocess.Popen(SSH + ["sudo sh -c "+shlex.quote(remote)],
                                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                      bufsize=0,creationflags=NO_WINDOW) as proc:
        def upload():
            try:
                while data := connection.recv(65536):
                    pending = memoryview(data)
                    while pending:
                        count = proc.stdin.write(pending)
                        if not count:
                            return
                        pending = pending[count:]
            except (ValueError,OSError):
                pass
            finally:
                proc.stdin.close()

        threading.Thread(target=upload,daemon=True).start()
        try:
            while data := proc.stdout.read(65536):
                connection.sendall(data)
        except OSError:
            pass
        finally:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def serve():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        server.bind(("127.0.0.1",19636))
        identity = prepare_guest()
        server.listen(3)
        subprocess.Popen([str(ADB),"connect",ARM_LAB],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=NO_WINDOW)
        while True:
            connection, _ = server.accept()
            threading.Thread(target=forward,args=(connection,identity),daemon=True).start()


if __name__=="__main__":
    serve()
