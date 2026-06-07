# openiboot on the iPhone 3GS (S5L8920) — bring-up notes

Documentation of a hobbyist effort to bring up [openiboot](https://github.com/iDroid-Project/openiboot)
on an **iPhone 3GS (SoC S5L8920, Cortex‑A8)** that I own, chain-loaded from a
jailbroken **iOS 6.1.6** install using `kloader`. This repo collects the findings,
the diagnostic method, and a set of patches against the iDroid `openiboot` tree.

> openiboot is GPLv3. This repo contains only documentation plus patches/diffs
> against [iDroid-Project/openiboot](https://github.com/iDroid-Project/openiboot);
> apply them on top of that tree. All work was performed on my own device for
> learning purposes.

## TL;DR

The 3GS openiboot port was never finished — it's a half-ported skeleton copied
from the S5L8900/8720/A4 trees, with many hardware constants never re-derived for
the 8920. Loading it via `kloader` *does* work as a pipeline, and the bootloader
relocates, enters C, and runs `platform_init()`. It does **not** yet sustain to a
console. This write-up pins down **why**, with a blind (no-serial) bisection method.

## Setup / harness

- Device: iPhone 3GS, `iPhone2,1`, jailbroken iOS 6.1.6 (p0sixspwn).
- Host: Apple‑silicon Mac; [Legacy iOS Kit](https://github.com/LukeZGD/Legacy-iOS-Kit)
  provides `iproxy`, `sshpass`, and the `kloader` binaries.
- Connect: `iproxy 2222 22` → `ssh root@127.0.0.1 -p 2222` (legacy KEX/cipher/host‑key
  algorithms required for the old dropbear/OpenSSH; default password `alpine`).
- Copy with `scp -O` (force legacy SCP protocol; modern macOS scp defaults to SFTP).
- Build: [`build-3gs.sh`](build-3gs.sh) builds the ELF + raw `.bin` reproducibly via
  Docker (amd64 cross toolchain). Wrap the `.bin` into the img3 `kloader` expects with
  [`wrap_img3.py`](wrap_img3.py) (swaps only the `DATA` tag of a reference signed img3;
  `kloader` ignores the signature).
- Run: `kloader /var/root/openiboot.img3` from the booted, jailbroken OS (needs `tfp0`).

`kloader` **must** run from the full jailbroken OS, not a restore ramdisk — the
ramdisk lacks `tfp0`, so kloader fails with `failed to get kernel_base` /
`task_for_pid 0 failed`.

## Blind bisection method (no serial cable)

With no UART, the only observable is: device **hangs** (black screen) vs **reboots**
(Apple logo). Insert an infinite loop (`for(;;){}` in C, `B .` in asm) after a given
init step:

- **hang** → execution reached the loop (everything before it survived);
- **reboot** → something before the loop reset the SoC.

Walking that marker through `platform_init()` localizes a fault to a single function.
**Caveat learned the hard way:** this only works while the reset source is
deterministic. Once interrupts are enabled / past a certain point, results become
nondeterministic (see watchdog below) and single observations can't be trusted —
re-run the *same* image several times and tally.

## Findings

### 1. Two deterministic SoC-reset bugs: `uart_setup()` and `spi_setup()`

Both reset the SoC immediately (reproducible with IRQs off, so they're real driver
bugs, not noise). Root cause: **stale hardware constants** copied from the S5L8900
port and never re-derived for the 8920. Examples in-tree:

- `plat-s5l8920/includes/hardware/clock.h` is literally titled *"Clock constants for
  the S5L8730."*
- `uart.h`: `UART` base `0x82500000` with UARTs spaced **1 MB** apart
  (`0x100000`…`0x400000`) — writes to UART1‑4 land on unrelated peripherals. Also a
  divide‑by‑zero: `uart_setup()` sets `baud` but calls
  `uart_set_clk()→uart_set_baud_rate()` *before* `sample_rate` is initialized (it's 0).
- `lcd.h`: `LCD` `0x38900000` is the stale 8900 address (the real 8920 display is the
  CLCD at `0x85400000`).
- `s5l8920.h`: `WDT_CTRL 0x3E300000` is stale 8900; the real 8920 reset block is in the
  PMGR aperture at `0xBF1002xx` (see below).

**Workaround:** skip `uart_setup()`/`spi_setup()` — neither is needed to bring the
bootloader up (UART is debug-only). After skipping both, **all of `platform_init()`
runs without a deterministic reset.**

### 2. The nondeterministic reset = an un-disabled hardware watchdog (~2 min)

Past `platform_init()`, the same binary sometimes hangs and sometimes reboots.
A clean-handoff run that hangs **self-reboots at ≈2 minutes** — i.e. a hardware/PMU
watchdog that iBoot armed and openiboot never disables or pets.

- `plat-s5l8920` is the **only** platform with neither `wdt.c` nor `pmu.c`; the build
  defines `MALLOC_NO_WDT`; `power.c` is an empty stub. Nothing ever touches a watchdog.
- The real 8920 reset/watchdog block is at `0xBF100210 / 0xBF100214 / 0xBF10021C`
  (proven by openiboot's own `Reboot`/`DebugReboot`, which *fire* a reset via
  `0x214=1, 0x210=0x80000000, 0x21C=4`). The A4 sibling uses the analogous
  `0xBF102020/2024/202C`.
- Simply zeroing those three registers at entry did **not** stop the ~2-min reset, so
  either that block is only a write-to-fire trigger (not the running counter) or the
  disable needs a different bit/sequence — or the ~2-min watchdog is the **PMU** (I2C).
  *(Open — under investigation.)*

### 3. The handoff explains the rest

`kloader` hands off by **sleeping the SoC and waking into openiboot**. Consequences:

- The wake path varies run-to-run → some handoffs fault within the first instants
  (**fast reboots**, kloader's own ~"99.9%" nondeterminism), independent of openiboot.
- Because the device was asleep, the **LCD is powered off** on entry — there is **no
  live framebuffer to paint**. Getting any pixel out requires a *full* display
  bring-up (panel power/reset + `displaypipe_init`/CLCD + `pinot` panel init +
  backlight), not just a memset. (The CLCD scanout address register is `CLCD_BASE+0x24`.)

### 4. Robust entry shim

Added as the first instructions of `ArmReset` (`arch-arm/entry.sx`): mask IRQ/FIQ,
clean the D-cache to PoC, disable MMU + I/D caches, invalidate TLB/I-cache/branch
predictor, barriers. This makes entry run in deterministic physical-address space
regardless of the MMU/cache state kloader leaves. (It does **not** fix the watchdog,
which is CPU-state-independent — hence the continuing ~2-min reboot.)

## Status

| Stage | State |
|---|---|
| kloader pipeline / `tfp0` handoff | ✅ works |
| relocation + entry → C | ✅ works |
| `platform_init()` (with uart/spi skipped) | ✅ completes |
| `uart_setup` / `spi_setup` | 🩹 reset on stale constants — skipped |
| hardware watchdog (~2 min) | ❌ not yet disabled |
| display / pixel output | ❌ needs full LCD bring-up (display off after sleep handoff) |
| boot to console | ❌ not yet |

## Patches

See [`patches/bringup-diagnostics-and-fixes.diff`](patches/bringup-diagnostics-and-fixes.diff):
the entry shim, the `uart_setup`/`spi_setup` skips, and the diagnostic markers, against
the iDroid `openiboot` tree.

## Credits

Built on [iDroid-Project/openiboot](https://github.com/iDroid-Project/openiboot)
(GPLv3) and [Legacy iOS Kit](https://github.com/LukeZGD/Legacy-iOS-Kit). `kloader` is
from the winocm/axi0mX `ios-kexec-utils` lineage.
