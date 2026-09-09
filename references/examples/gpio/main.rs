/****************************************************************************
**
** Copyright (C) ${COPYRIGHT_YEAR} MikroElektronika d.o.o.
** Contact: https://www.mikroe.com/contact
**
** This file is part of the mikroSDK package
**
** Commercial License Usage
**
** Licensees holding valid commercial NECTO compilers AI licenses may use this
** file in accordance with the commercial license agreement provided with the
** Software or, alternatively, in accordance with the terms contained in
** a written agreement between you and The MikroElektronika Company.
** For licensing terms and conditions see
** https://www.mikroe.com/legal/software-license-agreement.
** For further information use the contact form at
** https://www.mikroe.com/contact.
**
**
** GNU Lesser General Public License Usage
**
** Alternatively, this file may be used for
** non-commercial projects under the terms of the GNU Lesser
** General Public License version 3 as published by the Free Software
** Foundation: https://www.gnu.org/licenses/lgpl-3.0.html.
**
** The above copyright notice and this permission notice shall be
** included in all copies or substantial portions of the Software.
**
** THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
** EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
** OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
** IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
** DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT
** OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
** OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
**
****************************************************************************/

#![no_std]
#![no_main]
#![allow(non_upper_case_globals)]

use panic_halt as _;

use drv_digital_out::{
    digital_out_init, digital_out_low, digital_out_t, digital_out_toggle,
};
use drv_name::{pin_name_t, GPIO_B0, GPIO_B14, GPIO_B7};
use system::init_clock::delay_1sec;

const pin_out_1: pin_name_t = GPIO_B0;
const pin_out_2: pin_name_t = GPIO_B7;
const pin_out_3: pin_name_t = GPIO_B14;

#[unsafe(no_mangle)]
fn main() -> ! {
    let mut output_1 = digital_out_t::default();
    let mut output_2 = digital_out_t::default();
    let mut output_3 = digital_out_t::default();

    if digital_out_init(&mut output_1, pin_out_1).is_err()
        || digital_out_init(&mut output_2, pin_out_2).is_err()
        || digital_out_init(&mut output_3, pin_out_3).is_err()
    {
        loop {}
    }

    // Start all outputs from a known low state.
    let _ = digital_out_low(&mut output_1);
    let _ = digital_out_low(&mut output_2);
    let _ = digital_out_low(&mut output_3);

    loop {
        let _ = digital_out_toggle(&mut output_1);
        let _ = digital_out_toggle(&mut output_2);
        let _ = digital_out_toggle(&mut output_3);

        delay_1sec();
    }
}
