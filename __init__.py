# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from . import purchase


def register():
    Pool.register(
        purchase.Purchase,
        purchase.PurchaseLine,
        module='purchase_edit', type_='model')
