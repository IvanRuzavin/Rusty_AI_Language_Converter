//! Small, allocation-free Rust logo renderer for the 240x240 IPS Display 2 Click.
//!
//! The centre "R" stays fixed while the outer cog is rotated for every frame.
//! Rendering is scanline based, so it needs only a 480-byte line buffer instead
//! of a full 240x240 framebuffer.

use crate::ips_display_2::{IpsDisplay2, Result, HEIGHT, WIDTH};

const CENTER_X: i32 = 120;
const CENTER_Y: i32 = 120;

// 64 animation phases (5.625 degrees per frame), Q14 fixed-point sine.
// Cosine is sine shifted by 90 degrees (16 table entries).
const SIN_Q14: [i32; 64] = [
    0, 1606, 3196, 4756, 6270, 7723, 9102, 10394,
    11585, 12665, 13623, 14449, 15137, 15679, 16069, 16305,
    16384, 16305, 16069, 15679, 15137, 14449, 13623, 12665,
    11585, 10394, 9102, 7723, 6270, 4756, 3196, 1606,
    0, -1606, -3196, -4756, -6270, -7723, -9102, -10394,
    -11585, -12665, -13623, -14449, -15137, -15679, -16069, -16305,
    -16384, -16305, -16069, -15679, -15137, -14449, -13623, -12665,
    -11585, -10394, -9102, -7723, -6270, -4756, -3196, -1606,
];

// 16 equally spaced tooth direction vectors, Q10 fixed point.
const TOOTH_DIRS_Q10: [(i32, i32); 16] = [
    (1024, 0),
    (946, 392),
    (724, 724),
    (392, 946),
    (0, 1024),
    (-392, 946),
    (-724, 724),
    (-946, 392),
    (-1024, 0),
    (-946, -392),
    (-724, -724),
    (-392, -946),
    (0, -1024),
    (392, -946),
    (724, -724),
    (946, -392),
];

// Five bolt-hole directions. Having five holes deliberately breaks the tooth
// symmetry, which makes the rotation visually obvious through the full turn.
const HOLE_DIRS_Q10: [(i32, i32); 5] = [
    (1024, 0),
    (316, 974),
    (-828, 602),
    (-828, -602),
    (316, -974),
];

// RGB565 colours.
const COLOR_BACKGROUND: u16 = 0x0000;
const COLOR_CENTER: u16 = 0x1082;
const COLOR_RUST_DARK: u16 = 0x8122;
const COLOR_RUST: u16 = 0xCA05;
const COLOR_RUST_LIGHT: u16 = 0xF2C8;
const COLOR_R: u16 = 0xFFFF;
const COLOR_R_SHADOW: u16 = 0xAD55;

pub const FRAME_PHASES: usize = 64;

/// Render one complete animated frame.
///
/// `phase` is wrapped automatically to `0..FRAME_PHASES`.
pub fn render_frame(display: &mut IpsDisplay2, phase: usize) -> Result<()> {
    let phase = phase & (FRAME_PHASES - 1);
    let sin = SIN_Q14[phase];
    let cos = SIN_Q14[(phase + 16) & (FRAME_PHASES - 1)];

    // Big-endian RGB565 bytes, one complete LCD row.
    let mut row = [0u8; (WIDTH as usize) * 2];

    let mut y = 0u16;
    while y < HEIGHT {
        let sy = i32::from(y) - CENTER_Y;
        let mut x = 0u16;

        while x < WIDTH {
            let sx = i32::from(x) - CENTER_X;

            // Rotate the sampling coordinate in the opposite direction. The
            // gear itself can then be described once in its zero-angle form.
            let gx = ((sx * cos) + (sy * sin)) >> 14;
            let gy = ((-sx * sin) + (sy * cos)) >> 14;

            let color = pixel_color(sx, sy, gx, gy);
            let index = usize::from(x) * 2;
            row[index] = (color >> 8) as u8;
            row[index + 1] = color as u8;

            x += 1;
        }

        display.write_row_rgb565_be(y, &mut row)?;
        y += 1;
    }

    Ok(())
}

fn pixel_color(screen_x: i32, screen_y: i32, gear_x: i32, gear_y: i32) -> u16 {
    let screen_r2 = (screen_x * screen_x) + (screen_y * screen_y);

    // Dark circular field behind the stationary R.
    let mut color = if screen_r2 <= 68 * 68 {
        COLOR_CENTER
    } else {
        COLOR_BACKGROUND
    };

    if let Some(gear_color) = gear_pixel(gear_x, gear_y, screen_x, screen_y) {
        color = gear_color;
    }

    // A small dark offset gives the white R some depth without a framebuffer.
    if rust_r(screen_x + 2, screen_y + 2) {
        color = COLOR_R_SHADOW;
    }
    if rust_r(screen_x, screen_y) {
        color = COLOR_R;
    }

    color
}

/// Returns the colour of a gear pixel, or None when the point is outside the gear.
fn gear_pixel(x: i32, y: i32, screen_x: i32, screen_y: i32) -> Option<u16> {
    let r2 = (x * x) + (y * y);

    // The five moving holes are cut from the gear before anything else.
    for (ux, uy) in HOLE_DIRS_Q10 {
        let hx = (82 * ux) >> 10;
        let hy = (82 * uy) >> 10;
        let dx = x - hx;
        let dy = y - hy;
        if (dx * dx) + (dy * dy) <= 6 * 6 {
            return None;
        }
    }

    let mut on_gear = r2 >= 69 * 69 && r2 <= 95 * 95;

    // Add 16 radial rectangular teeth. Coordinates are already inverse-rotated,
    // therefore these directions remain constant while the whole gear spins.
    if !on_gear && r2 >= 88 * 88 && r2 <= 110 * 110 {
        for (ux, uy) in TOOTH_DIRS_Q10 {
            let radial = ((x * ux) + (y * uy)) >> 10;
            let tangent = ((-x * uy) + (y * ux)) >> 10;

            if radial >= 90 && radial <= 109 && tangent.abs() <= 7 {
                on_gear = true;
                break;
            }
        }
    }

    if !on_gear {
        return None;
    }

    // Fixed screen-space light from the upper-left gives the spinning cog a
    // metallic-looking highlight while the geometry itself keeps rotating.
    if screen_x + screen_y < -75 {
        Some(COLOR_RUST_LIGHT)
    } else if r2 > 96 * 96 || r2 < 73 * 73 {
        Some(COLOR_RUST_DARK)
    } else {
        Some(COLOR_RUST)
    }
}

/// Simple vector-style stationary letter R, centred in the gear.
fn rust_r(x: i32, y: i32) -> bool {
    // Vertical stem.
    if x >= -42 && x <= -27 && y >= -54 && y <= 54 {
        return true;
    }

    // Top and middle bars.
    if x >= -42 && x <= 14 && y >= -54 && y <= -39 {
        return true;
    }
    if x >= -42 && x <= 13 && y >= -8 && y <= 7 {
        return true;
    }

    // Right side of the upper bowl.
    if x >= 13 && x <= 28 && y >= -40 && y <= -7 {
        return true;
    }

    // Slightly round off the two right-hand corners of the bowl.
    let top_dx = x - 13;
    let top_dy = y + 39;
    if x >= 8 && y <= -34 && (top_dx * top_dx) + (top_dy * top_dy) <= 15 * 15 {
        return true;
    }

    let mid_dx = x - 13;
    let mid_dy = y + 7;
    if x >= 8 && y >= -12 && (mid_dx * mid_dx) + (mid_dy * mid_dy) <= 15 * 15 {
        return true;
    }

    // Diagonal R leg: thick line segment from roughly the bowl junction to
    // the lower-right of the centre disc.
    thick_segment(x, y, 1, 1, 37, 53, 7)
}

fn thick_segment(
    x: i32,
    y: i32,
    x0: i32,
    y0: i32,
    x1: i32,
    y1: i32,
    half_width: i32,
) -> bool {
    let vx = x1 - x0;
    let vy = y1 - y0;
    let px = x - x0;
    let py = y - y0;

    let length2 = (vx * vx) + (vy * vy);
    let projection = (px * vx) + (py * vy);
    if projection < 0 || projection > length2 {
        return false;
    }

    let cross = (px * vy) - (py * vx);
    let cross2 = i64::from(cross) * i64::from(cross);
    let limit = i64::from(half_width * half_width) * i64::from(length2);
    cross2 <= limit
}
