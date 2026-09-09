//! Minimal no_std driver for the MikroE IPS Display 2 Click (ST7789V).
//!
//! The API is implemented on top of the Rust mikroSDK SPI and digital-output
//! driver layers, so the same source can be built by any complete setup that
//! the VS Code extension generates.

use drv_digital_out::{
    digital_out_high, digital_out_init, digital_out_low, digital_out_t,
};
use drv_name::{pin_name_t, HAL_PIN_NC};
use drv_spi_master::{
    spi_master_config_t, spi_master_open, spi_master_set_default_write_data,
    spi_master_set_mode, spi_master_set_speed, spi_master_t, SPI_MASTER_MODE_DEFAULT,
};
use system::init_clock::delay_1ms;

const CMD_SLEEP_OUT: u8 = 0x11;
const CMD_INVERSION_ON: u8 = 0x21;
const CMD_DISPLAY_ON: u8 = 0x29;
const CMD_COLUMN_ADDRESS: u8 = 0x2A;
const CMD_ROW_ADDRESS: u8 = 0x2B;
const CMD_MEMORY_WRITE: u8 = 0x2C;
const CMD_MEMORY_ACCESS_CONTROL: u8 = 0x36;
const CMD_PIXEL_FORMAT: u8 = 0x3A;
const CMD_PORCH_CONTROL: u8 = 0xB2;
const CMD_GATE_CONTROL: u8 = 0xB7;
const CMD_VCOM_SETTING: u8 = 0xBB;
const CMD_VDV_VRH_ENABLE: u8 = 0xC2;
const CMD_VRH_SET: u8 = 0xC3;
const CMD_VDV_SET: u8 = 0xC4;
const CMD_FRAME_RATE: u8 = 0xC6;
const CMD_POWER_CONTROL_1: u8 = 0xD0;
const CMD_POSITIVE_GAMMA: u8 = 0xE0;
const CMD_NEGATIVE_GAMMA: u8 = 0xE1;

pub const WIDTH: u16 = 240;
pub const HEIGHT: u16 = 240;

pub const COLOR_BLACK: u16 = 0x0000;
pub const COLOR_WHITE: u16 = 0xFFFF;
pub const COLOR_RED: u16 = 0xF800;
pub const COLOR_LIME: u16 = 0x07E0;
pub const COLOR_BLUE: u16 = 0x001F;
pub const COLOR_CYAN: u16 = 0x07FF;
pub const COLOR_MAGENTA: u16 = 0xF81F;
pub const COLOR_YELLOW: u16 = 0xFFE0;

#[derive(Clone, Copy, PartialEq)]
pub enum Rotation {
    Vertical0,
    Vertical180,
    Horizontal0,
    Horizontal180,
}

#[derive(Clone, Copy)]
pub struct Point {
    pub x: u16,
    pub y: u16,
}

pub struct Config {
    pub sck: pin_name_t,
    pub miso: pin_name_t,
    pub mosi: pin_name_t,
    pub cs: pin_name_t,
    pub rst: pin_name_t,
    pub dc: pin_name_t,
    pub backlight: pin_name_t,
    pub spi_speed: u32,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            sck: HAL_PIN_NC,
            miso: HAL_PIN_NC,
            mosi: HAL_PIN_NC,
            cs: HAL_PIN_NC,
            rst: HAL_PIN_NC,
            dc: HAL_PIN_NC,
            backlight: HAL_PIN_NC,
            spi_speed: 5_000_000,
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
pub enum Error {
    Spi,
    Pin,
    OutOfBounds,
    InvalidImageSize,
}

pub type Result<T> = core::result::Result<T, Error>;

pub struct IpsDisplay2 {
    spi: spi_master_t,
    cs: digital_out_t,
    rst: digital_out_t,
    dc: digital_out_t,
    backlight: digital_out_t,
    rotation: Rotation,
}

impl IpsDisplay2 {
    pub fn new(config: Config) -> Result<Self> {
        let mut spi = spi_master_t::default();
        let mut spi_config = spi_master_config_t::default();
        spi_config.sck = config.sck;
        spi_config.miso = config.miso;
        spi_config.mosi = config.mosi;
        spi_master_open(&mut spi, spi_config).map_err(|_| Error::Spi)?;
        spi_master_set_default_write_data(&mut spi, 0).map_err(|_| Error::Spi)?;
        spi_master_set_mode(&mut spi, SPI_MASTER_MODE_DEFAULT).map_err(|_| Error::Spi)?;
        spi_master_set_speed(&mut spi, config.spi_speed).map_err(|_| Error::Spi)?;

        let mut cs = digital_out_t::default();
        let mut rst = digital_out_t::default();
        let mut dc = digital_out_t::default();
        let mut backlight = digital_out_t::default();
        digital_out_init(&mut cs, config.cs).map_err(|_| Error::Pin)?;
        digital_out_init(&mut rst, config.rst).map_err(|_| Error::Pin)?;
        digital_out_init(&mut dc, config.dc).map_err(|_| Error::Pin)?;
        digital_out_init(&mut backlight, config.backlight).map_err(|_| Error::Pin)?;
        digital_out_high(&mut cs).map_err(|_| Error::Pin)?;
        digital_out_low(&mut backlight).map_err(|_| Error::Pin)?;

        Ok(Self {
            spi,
            cs,
            rst,
            dc,
            backlight,
            rotation: Rotation::Vertical0,
        })
    }

    pub fn default_config(&mut self) -> Result<()> {
        self.reset()?;
        self.write_command(CMD_SLEEP_OUT)?;
        delay_ms(120);
        self.set_rotation(Rotation::Vertical0)?;
        self.write_command_data(CMD_PIXEL_FORMAT, &mut [0x05])?;
        self.write_command_data(CMD_PORCH_CONTROL, &mut [0x0C, 0x0C, 0x00, 0x33, 0x33])?;
        self.write_command_data(CMD_GATE_CONTROL, &mut [0x35])?;
        self.write_command_data(CMD_VCOM_SETTING, &mut [0x3A])?;
        self.write_command_data(CMD_VDV_VRH_ENABLE, &mut [0x01])?;
        self.write_command_data(CMD_VRH_SET, &mut [0x19])?;
        self.write_command_data(CMD_VDV_SET, &mut [0x20])?;
        self.write_command_data(CMD_FRAME_RATE, &mut [0x0F])?;
        self.write_command_data(CMD_POWER_CONTROL_1, &mut [0xA4, 0xA1])?;
        self.write_command_data(
            CMD_POSITIVE_GAMMA,
            &mut [0xD0, 0x08, 0x0E, 0x09, 0x09, 0x05, 0x31, 0x33, 0x48, 0x17, 0x14, 0x15, 0x31, 0x34],
        )?;
        self.write_command_data(
            CMD_NEGATIVE_GAMMA,
            &mut [0xD0, 0x08, 0x0E, 0x09, 0x09, 0x15, 0x31, 0x33, 0x48, 0x17, 0x14, 0x15, 0x31, 0x34],
        )?;
        self.write_command(CMD_INVERSION_ON)?;
        self.fill_screen(COLOR_BLACK)?;
        self.write_command(CMD_DISPLAY_ON)?;
        digital_out_high(&mut self.backlight).map_err(|_| Error::Pin)?;
        delay_ms(100);
        Ok(())
    }

    pub fn set_rotation(&mut self, rotation: Rotation) -> Result<()> {
        let value = match rotation {
            Rotation::Vertical0 => 0x00,
            Rotation::Vertical180 => 0xC0,
            Rotation::Horizontal0 => 0x60,
            Rotation::Horizontal180 => 0xA0,
        };
        self.write_command_data(CMD_MEMORY_ACCESS_CONTROL, &mut [value])?;
        self.rotation = rotation;
        Ok(())
    }

    pub fn fill_screen(&mut self, color: u16) -> Result<()> {
        let (width, height) = self.dimensions();
        self.set_position(Point { x: 0, y: 0 }, Point { x: width - 1, y: height - 1 })?;
        self.write_solid_pixels(color, u32::from(width) * u32::from(height))
    }

    pub fn draw_pixel(&mut self, point: Point, color: u16) -> Result<()> {
        self.set_position(point, point)?;
        self.write_solid_pixels(color, 1)
    }

    pub fn draw_line(&mut self, start: Point, end: Point, color: u16) -> Result<()> {
        let mut x0 = i32::from(start.x);
        let mut y0 = i32::from(start.y);
        let x1 = i32::from(end.x);
        let y1 = i32::from(end.y);
        let dx = (x1 - x0).abs();
        let sx = if x0 < x1 { 1 } else { -1 };
        let dy = -(y1 - y0).abs();
        let sy = if y0 < y1 { 1 } else { -1 };
        let mut error = dx + dy;

        loop {
            self.draw_pixel(Point { x: x0 as u16, y: y0 as u16 }, color)?;
            if x0 == x1 && y0 == y1 {
                break;
            }
            let twice = error * 2;
            if twice >= dy {
                error += dy;
                x0 += sx;
            }
            if twice <= dx {
                error += dx;
                y0 += sy;
            }
        }
        Ok(())
    }

    pub fn draw_rectangle(&mut self, start: Point, end: Point, color: u16) -> Result<()> {
        self.draw_line(start, Point { x: end.x, y: start.y }, color)?;
        self.draw_line(start, Point { x: start.x, y: end.y }, color)?;
        self.draw_line(Point { x: start.x, y: end.y }, end, color)?;
        self.draw_line(Point { x: end.x, y: start.y }, end, color)
    }

    pub fn draw_circle(&mut self, center: Point, radius: u16, color: u16) -> Result<()> {
        let mut x = 0i32;
        let mut y = i32::from(radius);
        let mut decision = 1 - y;
        while x <= y {
            let cx = i32::from(center.x);
            let cy = i32::from(center.y);
            let points = [
                (cx + x, cy + y), (cx - x, cy + y), (cx + x, cy - y), (cx - x, cy - y),
                (cx + y, cy + x), (cx - y, cy + x), (cx + y, cy - x), (cx - y, cy - x),
            ];
            for (px, py) in points {
                if px >= 0 && py >= 0 {
                    self.draw_pixel(Point { x: px as u16, y: py as u16 }, color)?;
                }
            }
            x += 1;
            if decision < 0 {
                decision += (x * 2) + 1;
            } else {
                y -= 1;
                decision += ((x - y) * 2) + 1;
            }
        }
        Ok(())
    }

    /// Draw a full-screen image stored as big-endian RGB565 bytes.
    ///
    /// The expected payload is WIDTH * HEIGHT * 2 bytes. This matches the
    /// preconverted `.rgb565` asset produced by tools/png_to_rgb565.py.
    pub fn draw_rgb565_image(&mut self, pixels: &[u8]) -> Result<()> {
        let (width, height) = self.dimensions();
        let expected = usize::from(width) * usize::from(height) * 2;
        if pixels.len() != expected {
            return Err(Error::InvalidImageSize);
        }

        self.set_position(
            Point { x: 0, y: 0 },
            Point { x: width - 1, y: height - 1 },
        )?;

        // The current SPI API takes a mutable slice. Keep the image itself in
        // flash and copy manageable chunks to a small stack buffer for transfer.
        let mut tx = [0u8; 1024];
        digital_out_low(&mut self.cs).map_err(|_| Error::Pin)?;
        digital_out_high(&mut self.dc).map_err(|_| Error::Pin)?;

        for chunk in pixels.chunks(tx.len()) {
            tx[..chunk.len()].copy_from_slice(chunk);
            drv_spi_master::spi_master_write(
                &mut self.spi,
                &mut tx[..chunk.len()],
                chunk.len(),
            )
            .map_err(|_| Error::Spi)?;
        }

        digital_out_high(&mut self.cs).map_err(|_| Error::Pin)?;
        Ok(())
    }

    pub fn backlight(&mut self, enabled: bool) -> Result<()> {
        if enabled {
            digital_out_high(&mut self.backlight).map_err(|_| Error::Pin)
        } else {
            digital_out_low(&mut self.backlight).map_err(|_| Error::Pin)
        }
    }

    fn dimensions(&self) -> (u16, u16) {
        match self.rotation {
            Rotation::Vertical0 | Rotation::Vertical180 => (WIDTH, HEIGHT),
            Rotation::Horizontal0 | Rotation::Horizontal180 => (HEIGHT, WIDTH),
        }
    }

    fn reset(&mut self) -> Result<()> {
        digital_out_low(&mut self.rst).map_err(|_| Error::Pin)?;
        delay_ms(1);
        digital_out_high(&mut self.rst).map_err(|_| Error::Pin)?;
        delay_ms(200);
        Ok(())
    }

    fn set_position(&mut self, start: Point, end: Point) -> Result<()> {
        let (width, height) = self.dimensions();
        if start.x > end.x || start.y > end.y || end.x >= width || end.y >= height {
            return Err(Error::OutOfBounds);
        }
        let (x_offset, y_offset) = match self.rotation {
            Rotation::Vertical0 => (0, 0),
            Rotation::Vertical180 => (0, 80),
            Rotation::Horizontal0 => (0, 0),
            Rotation::Horizontal180 => (80, 0),
        };
        let x0 = start.x + x_offset;
        let x1 = end.x + x_offset;
        let y0 = start.y + y_offset;
        let y1 = end.y + y_offset;
        self.write_command_data(
            CMD_COLUMN_ADDRESS,
            &mut [(x0 >> 8) as u8, x0 as u8, (x1 >> 8) as u8, x1 as u8],
        )?;
        self.write_command_data(
            CMD_ROW_ADDRESS,
            &mut [(y0 >> 8) as u8, y0 as u8, (y1 >> 8) as u8, y1 as u8],
        )?;
        self.write_command(CMD_MEMORY_WRITE)
    }

    fn write_command(&mut self, command: u8) -> Result<()> {
        let mut command_buffer = [command];
        digital_out_low(&mut self.cs).map_err(|_| Error::Pin)?;
        digital_out_low(&mut self.dc).map_err(|_| Error::Pin)?;
        let result = drv_spi_master::spi_master_write(&mut self.spi, &mut command_buffer, 1)
            .map_err(|_| Error::Spi);
        digital_out_high(&mut self.cs).map_err(|_| Error::Pin)?;
        result
    }

    fn write_command_data(&mut self, command: u8, data: &mut [u8]) -> Result<()> {
        let mut command_buffer = [command];
        digital_out_low(&mut self.cs).map_err(|_| Error::Pin)?;
        digital_out_low(&mut self.dc).map_err(|_| Error::Pin)?;
        drv_spi_master::spi_master_write(&mut self.spi, &mut command_buffer, 1)
            .map_err(|_| Error::Spi)?;
        digital_out_high(&mut self.dc).map_err(|_| Error::Pin)?;
        let data_len = data.len();
        let result = drv_spi_master::spi_master_write(&mut self.spi, data, data_len)
            .map_err(|_| Error::Spi);
        digital_out_high(&mut self.cs).map_err(|_| Error::Pin)?;
        result
    }

    fn write_solid_pixels(&mut self, color: u16, mut count: u32) -> Result<()> {
        let high = (color >> 8) as u8;
        let low = color as u8;
        let mut buffer = [0u8; 128];
        for pair in buffer.chunks_exact_mut(2) {
            pair[0] = high;
            pair[1] = low;
        }

        digital_out_low(&mut self.cs).map_err(|_| Error::Pin)?;
        digital_out_high(&mut self.dc).map_err(|_| Error::Pin)?;
        while count > 0 {
            let pixels = if count > 64 { 64 } else { count as usize };
            let byte_count = pixels * 2;
            drv_spi_master::spi_master_write(&mut self.spi, &mut buffer[..byte_count], byte_count)
                .map_err(|_| Error::Spi)?;
            count -= pixels as u32;
        }
        digital_out_high(&mut self.cs).map_err(|_| Error::Pin)?;
        Ok(())
    }
}

fn delay_ms(milliseconds: u16) {
    for _ in 0..milliseconds {
        delay_1ms();
    }
}
