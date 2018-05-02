# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.

try:
    from trytond.modules.account_invoice.tests.test_purchase_edit import (
        suite)
except ImportError:
    from .test_purchase_edit import suite

__all__ = ['suite']
