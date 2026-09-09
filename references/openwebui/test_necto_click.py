import unittest
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / 'backend'))

from necto_assistant_router.models.click_lvgl_lcd.utils import (
    click_generation_api_reference,
    extract_c_source_symbols,
    extract_header_symbols,
)


class TestNectoClickAst(unittest.TestCase):
    def test_ast_header_extraction_preserves_doxygen_context(self):
        header = '''
#include "foo.h"
// Board description: temperature °C sensor.
/** @brief Configuration for the sensor. */
typedef struct {
    int value; /**< Current sensor value. */
    const char *name; /*!< Display name. */
} foo_cfg_t;
/**
 * @brief Initialize the sensor.
 * @param ctx Driver context.
 * @return 0 on success, -1 on error.
 */
err_t foo_init(foo_cfg_t *ctx);
void (*foo_callback)(int);
#define FOO_MAP(x) (x)
'''

        symbols = extract_header_symbols({'mikroe.click.foo': header})['by_source']['mikroe.click.foo']

        self.assertEqual(symbols['macros'], ['FOO_MAP'])
        self.assertEqual(
            [function['name'] for function in symbols['functions']],
            ['foo_init'],
        )
        self.assertEqual(symbols['functions'][0]['doc']['params']['ctx'], 'Driver context.')
        self.assertEqual(
            symbols['structs'][0]['members'][0]['description'],
            'Current sensor value.',
        )
        self.assertEqual(
            symbols['structs'][0]['members'][1]['description'],
            'Display name.',
        )

        generation_context = click_generation_api_reference(
            click_header_elements={'by_source': {'mikroe.click.foo': symbols}},
            click_source_elements={'by_source': {'mikroe.click.foo': {'functions': []}}},
            active_clicks=['foo'],
        )
        self.assertIn('Purpose: Initialize the sensor.', generation_context)
        self.assertIn('Params: ctx: Driver context.', generation_context)
        self.assertIn('value: Current sensor value.', generation_context)


    def test_ast_source_extraction_tracks_calls_and_definitions(self):
        source = '''
#include "foo.h"
static void helper(void) { foo_init(0); }
void application_init(void) { helper(); }
void application_task(void) { FOO_MAP(1); }
'''

        symbols = extract_c_source_symbols(
            {'mikroe.click.foo': source},
            include_used_symbols=True,
        )['by_source']['mikroe.click.foo']

        self.assertEqual(
            [function['name'] for function in symbols['functions']],
            ['helper', 'application_init', 'application_task'],
        )
        self.assertIn('foo_init', symbols['used_functions'])
        self.assertIn('helper', symbols['used_functions'])
        self.assertIn('FOO_MAP', symbols['used_macro_like_tokens'])