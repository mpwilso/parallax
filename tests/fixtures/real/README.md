# Real sandbox start failures

Each file is what bwrap or srt printed when a sandbox couldn't start, byte for byte. Nothing here is written by hand. `parallax/sandboxfail.py` names the file behind each pattern it matches.

Machine: WSL2, kernel 6.18.33.2-microsoft-standard-WSL2, bubblewrap 0.9.0, srt 1.0.0. Captured 2026-10-05. Each command exited 1 with nothing on stdout; the `.stderr` files are stderr as written.

| File | Command |
|---|---|
| `m1-command-1-bwrap.txt` | Matt's hand check M1, command 1, as his terminal showed it: `bwrap --dev-bind / / --unshare-user --disable-userns -- bwrap --dev-bind / / --unshare-user true; echo "exit $?"`. The `exit 1` line is from the echo. |
| `m1-command-2-srt.txt` | M1, command 2, the same with `srt -c true` inside. The `exit 1` line is from the echo. |
| `bwrap-0.9.0-disable-userns.stderr` | The M1 command 1 again, stderr only. `--disable-userns` stops new user namespaces inside the outer bwrap. |
| `srt-1.0.0-disable-userns-parallax-config.stderr` | `srt --settings CONFIG -c 'python3 -c "print(1)"'` inside the same outer bwrap, with CONFIG made by `parallax.sandbox.rules(...).srt()` for a throwaway repo. |
| `bwrap-0.9.0-chroot-eperm.stderr` | `bwrap --dev-bind / / --unshare-user true` run from a chroot inside a throwaway user namespace, where the kernel refuses a new user namespace (EPERM). Capabilities cleared first with `setpriv --inh-caps=-all --ambient-caps=-all`. |
| `srt-1.0.0-chroot-eperm.stderr` | `srt -c true` the same way. |
| `srt-1.0.0-no-tools-on-path.stderr` | `srt -c true` with `PATH` holding only srt and node, so bwrap, socat and rg are not found. |
