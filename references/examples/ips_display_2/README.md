# IPS Display 2 Click — Static Rust Logo Demo

Presentation demo for:

- **MCU:** STM32F756ZG
- **MCU card:** MCU CARD for STM32
- **Board:** UNI-DS v8
- **Display:** IPS Display 2 Click (ST7789V, 240x240)
- **Socket:** mikroBUS 1

The application initializes the display, uploads one full-screen Rust logo image, turns the backlight on, and then leaves the image on screen permanently.

There is **no runtime animation** and no per-pixel drawing loop. The supplied PNG is preconverted to the display's native **RGB565** format so the MCU only has to transmit 115,200 bytes over SPI once.

## Files

- `assets/rust_logo_240x240.png` — presentation image used as the source asset.
- `assets/rust_logo_240x240.rgb565` — preconverted image that is compiled into flash.
- `main.rs` — initializes the display and uploads the image once.
- `ips_display_2.rs` — IPS Display 2 Click driver with full-screen RGB565 transfer support.
- `mikrobus.rs` — generated UNI-DS v8 mapping for the configured STM32F756ZG setup.
- `tools/png_to_rgb565.py` — optional helper for replacing the image later.

## Build / flash

Open the project in VS Code with MikroBUS Rust Tools and use the already configured setup:

**UNI-DS v8 + MCU CARD for STM32 + STM32F756ZG**

Then:

- `Ctrl+Shift+B` — build
- `Ctrl+F5` — build and flash
- `F5` — debug

## Replacing the picture

If you want another 240x240 PNG later:

```bash
python3 tools/png_to_rgb565.py my_picture.png assets/rust_logo_240x240.rgb565
```

The converter requires Pillow on the development PC. No PNG decoder is needed on the MCU.
