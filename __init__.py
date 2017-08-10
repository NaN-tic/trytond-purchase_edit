# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from .purchase import *


def register():
    Pool.register(
        Purchase,
        PurchaseLine,
        module='purchase_edit', type_='model')
