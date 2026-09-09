# STM32F412RE standalone GPIO blink

This example toggles three GPIO outputs once per second:

| Output | Rust pin |
|---|---|
| 1 | `GPIO_B0` |
| 2 | `GPIO_B7` |
| 3 | `GPIO_B14` |

The folder contains a lightweight root `Cargo.toml`, but no copied mikroSDK tree or machine-specific SDK dependency paths. The MikroBUS Rust VS Code extension builds the active `main.rs` through the reusable MCU setup.

## Build and run

1. Open this folder in VS Code.
2. Run **MikroBUS Rust: Configure MCU** and create an `STM32F412RE` setup.
3. Apply that setup to the current workspace.
4. Open `main.rs`.
5. Use:
   - `Ctrl+Shift+B` to build;
   - `Ctrl+F5` to build and flash;
   - `F5` to debug.

The application initializes all three pins as digital outputs, drives them low, and then toggles them together every second.
