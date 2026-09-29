# Parallax on Windows, through WSL2

Claude Code's sandbox runs on Linux, macOS and WSL2, not on native Windows, so on Windows Parallax lives in a WSL2 distro made just for it. Once the distro exists, the Linux steps in the [README](../README.md#setup) apply inside it. Windows drives and interop end up off, so the distro reaches nothing on the Windows side.

## Make the distro

1. In PowerShell as admin: `wsl --install --no-distribution`, then restart.
2. Make a distro named `parallax` from Ubuntu 24.04:
   ```powershell
   wsl --install Ubuntu-24.04 --no-launch
   wsl --export Ubuntu-24.04 $env:TEMP\u.tar
   wsl --import parallax C:\WSL\parallax $env:TEMP\u.tar --version 2
   wsl --unregister Ubuntu-24.04
   ```
3. `wsl -d parallax`, then as root: `adduser <you>` and `usermod -aG sudo <you>`.
4. Follow the README's setup steps 1 to 3 inside the distro. Ubuntu 24.04 blocks the unprivileged user namespaces the sandbox needs until you allow them; the README's step 1 has the `sysctl` line.
5. Clone while Windows drives are still mounted if the repo is on the Windows side, and drop the `/mnt/c` remote afterwards: `git -C ~/code/parallax remote remove origin`, since the next step turns drives off.

## Harden WSL

Interop and mounted Windows drives off, so a process in the distro can't reach Windows programs or files. Write `/etc/wsl.conf`:

```
[user]
default=<you>
[interop]
enabled=false
appendWindowsPath=false
[automount]
enabled=false
```

Then `wsl --terminate parallax` from PowerShell and start it again. `parallax doctor` reports all three as off; each one still on is a warning, not a failure, since the sandbox still stands.

## Using it from Windows

`parallax ui` prints a `localhost` link for your Windows browser: WSL forwards it. With interop off, Parallax can't open the browser itself, so you open the link. Claude Code's login inside the distro works the same way: copy the URL it prints into your browser.
