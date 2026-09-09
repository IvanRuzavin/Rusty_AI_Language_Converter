#![no_std]
#![no_main]
#![allow(non_upper_case_globals)]

use panic_halt as _;

mod ips_display_2;
mod mikrobus;

use ips_display_2::{Config, IpsDisplay2};
use mikrobus::*;

// Static 240x240 RGB565 version of assets/rust_logo_240x240.png.
// It is stored directly in flash and sent to the display once at startup.
const RUST_LOGO: &[u8; 240 * 240 * 2] =
    include_bytes!("assets/rust_logo_240x240.rgb565");

#[unsafe(no_mangle)]
fn main() -> ! {
    let config = Config {
        sck: MIKROBUS_1_SCK,
        miso: MIKROBUS_1_MISO,
        mosi: MIKROBUS_1_MOSI,
        cs: MIKROBUS_1_CS,
        rst: MIKROBUS_1_RST,
        dc: MIKROBUS_1_INT,
        backlight: MIKROBUS_1_AN,
        // A faster SPI clock makes the one-time full-screen upload nearly instant.
        spi_speed: 20_000_000,
    };

    let mut display = match IpsDisplay2::new(config) {
        Ok(display) => display,
        Err(_) => loop {
            core::hint::spin_loop();
        },
    };

    if display.default_config().is_err() {
        loop {
            core::hint::spin_loop();
        }
    }

    // Hide the transfer itself. The logo appears as a complete frame.
    let _ = display.backlight(false);

    if display.draw_rgb565_image(RUST_LOGO).is_err() {
        loop {
            core::hint::spin_loop();
        }
    }

    let _ = display.backlight(true);

    // Static presentation demo: keep the Rust logo on screen forever.
    loop {
        core::hint::spin_loop();
    }
}
