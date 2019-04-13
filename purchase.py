# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pyson import Eval
from trytond.pool import Pool, PoolMeta
from trytond.model import fields
from trytond.i18n import gettext
from trytond.exceptions import UserError

__all__ = ['Purchase', 'PurchaseLine']


_STATES_EDIT = ~Eval('state').in_(['draft', 'quotation', 'confirmed',
        'processing'])
_STATES_EDIT_LINE = ~Eval('_parent_purchase', {}).get('state').in_(['draft'])


class Purchase(metaclass=PoolMeta):
    __name__ = 'purchase.purchase'
    shipment_moves = fields.Function(fields.Many2Many('stock.move', None, None,
        'Moves'), 'get_shipment_moves')

    @classmethod
    def __setup__(cls):
        super(Purchase, cls).__setup__()
        cls._check_modify_exclude = ['description', 'payment_term',
            'invoice_address', 'lines']

        if hasattr(cls, 'payment_type'):
                cls._check_modify_exclude += ['payment_type']

        for fname in (cls._check_modify_exclude):
            field = getattr(cls, fname)
            field.states['readonly'] = _STATES_EDIT
            if not hasattr(field, 'depends'):
                field.depends = ['state']
            elif 'state' not in field.depends:
                field.depends.append('state')

        # also, lines can't edit when shipment state was sent
        cls.lines.states['readonly'] = (_STATES_EDIT |
            Eval('shipment_state').in_(['sent', 'exception']))
        cls.lines.depends.append('shipment_state')

    def get_shipment_moves(self, name):
        '''
        Get all moves from a purchase; outgoing_moves, incoming_moves and
        inventory_moves
        '''
        moves = []
        for shipment in self.shipments:
            for move in shipment.moves:
                moves.append(move.id)
        for shipment in self.shipment_returns:
            for move in shipment.moves:
                moves.append(move.id)
        return moves

    @property
    def check_edit_state_method(self):
        '''
        Check edit state method.
        '''
        return self.state in ('confirmed', 'processing')

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        purchases_to_process = []
        cache_to_update = []

        for purchases, values in zip(actions, actions):
            for purchase in purchases:
                if not purchase.check_edit_state_method:
                    continue

                has_invoice_lines = False
                for line in purchase.lines:
                    if line.invoice_lines:
                        has_invoice_lines = True

                for v in values:
                    if v in cls._check_modify_exclude:
                        if has_invoice_lines:
                            raise UserError(gettext(
                                'purchase_edit.invalid_edit_fields_method',
                                    field=v, purchase=purchase.rec_name))
                        if purchase.shipments or purchase.shipment_returns:
                            raise UserError(gettext(
                                'purchase_edit.invalid_edit_fields_shipped',
                                    field=v, purchase=purchase.rec_name))

                if 'lines' in values:
                    cache_to_update.append(purchase)

                    for move in purchase.shipment_moves:
                        if move.state != 'draft':
                            raise UserError(gettext(
                                'purchase_edit.invalid_edit_move',
                                    move=move.rec_name))

                    for v in values['lines']:
                        if 'create' == v[0]:
                            purchases_to_process.append(purchase)
                        if 'delete' == v[0]:
                            raise UserError(gettext(
                                'purchase_edit.invalid_delete_line',
                                purchase=purchase.rec_name))

        super(Purchase, cls).write(*args)

        if purchases_to_process:
            cls.process(purchases_to_process)

        if cache_to_update:
            for purchase in cache_to_update:
                purchase.untaxed_amount_cache = None
                purchase.tax_amount_cache = None
                purchase.total_amount_cache = None
                purchase.save()
            cls.store_cache(cache_to_update)

    def _get_shipment_purchase(self, Shipment, key):
        # return purchase shipment to continue picking or create new shipment
        if Shipment.__name__ == 'stock.shipment.in':
            shipments = self.shipments
            if shipments and len(shipments) == 1:
                shipment, = shipments
                drafts = True
                for move in shipment.moves:
                    if move.state != 'draft':
                        drafts = False
                if drafts:
                    return shipment
        elif Shipment.__name__ == 'stock.shipment.in.return':
            shipment_returns = self.shipment_returns
            if shipment_returns and len(shipment_returns) == 1:
                shipment, = shipment_returns
                drafts = True
                for move in shipment.moves:
                    if move.state != 'draft':
                        drafts = False
                if drafts:
                    return shipment
        return super(Purchase, self)._get_shipment_purchase(Shipment, key)


class PurchaseLine(metaclass=PoolMeta):
    __name__ = 'purchase.line'

    @classmethod
    def __setup__(cls):
        super(PurchaseLine, cls).__setup__()
        cls._check_modify_exclude = ['product', 'unit', 'quantity',
            'unit_price']
        cls._check_readonly_fields = []
        cls._line2move = {'unit': 'uom'}

    def check_line_to_update(self, fields):
        if (self.purchase and self.purchase.state == 'processing' and self.moves):
            # No check should be held if the only fields updated are
            # 'moves_recreated' or 'moves_ignored'
            if set(fields) - {'moves_recreated', 'moves_ignored'}:
                return True
        return False

    @classmethod
    def check_editable(cls, lines, fields):
        # check purchase lines
        shipments = set()
        for line in lines:
            if not line.check_line_to_update(fields):
                continue

            moves = line.moves
            if len(moves) > 1:
                raise UserError(gettext('purchase_edit.invalid_edit_multimove',
                    line=line.rec_name))
            for move in moves:
                if move.shipment:
                    shipments.add(move.shipment)

        for shipment in shipments:
            for move in shipment.moves:
                if move.state != 'draft':
                    raise UserError(gettext(
                        'purchase_edit.invalid_edit_move',
                        move=move.rec_name))

    @classmethod
    def write(cls, *args):
        pool = Pool()
        ShipmentIn = pool.get('stock.shipment.in')
        Move = Pool().get('stock.move')

        actions = iter(args)
        moves_to_write = []
        shipment_in_waiting = set()
        shipment_in_draft = set()

        for lines, values in zip(actions, actions):
            vals = {}
            check_readonly_fields = []
            for v in values:
                if v in cls._check_readonly_fields:
                    check_readonly_fields.append(v)

            for field in cls._check_modify_exclude:
                if field in values:
                    val = values.get(field)
                    mfield = cls._line2move.get(field)
                    if mfield:
                        vals[mfield] = val
                    else:
                        if field == 'quantity':
                            val = abs(val)
                        vals[field] = val

            for line in lines:
                if not line.check_line_to_update(values.keys()):
                    continue

                # check that not change type line
                if 'type' in values and values['type'] != line.type:
                    raise UserError(gettext('purchase_edit.cannot_edit',
                        field='type'))
                if check_readonly_fields:
                    raise UserError(gettext('purchase_edit.cannot_edit',
                        fields=', '.join(check_readonly_fields)))

                if len(line.moves) == 1:
                    # get first move because in validate we check that can not
                    # edit a line that has more than one move
                    move, = line.moves
                    moves_to_write.extend(([move], vals))

                    if move.shipment:
                        if move.shipment.__name__ == 'stock.shipment.in':
                            shipment = move.shipment
                            if shipment.state == 'waiting':
                                shipment_in_waiting.add(shipment)
                            if shipment.state == 'draft':
                                shipment_in_draft.add(shipment)
            cls.check_editable(lines, values.keys())

        super(PurchaseLine, cls).write(*args)

        if moves_to_write:
            Move.write(*moves_to_write)

            # reload inventory_moves from outgoing_moves
            # not necessary in returns and reload inventory_moves from
            # incoming_moves
            if shipment_in_waiting:
                ShipmentIn.draft(list(shipment_in_waiting))
                ShipmentIn.wait(list(shipment_in_waiting))

            if shipment_in_draft:
                ShipmentIn.wait(list(shipment_in_draft))
                ShipmentIn.draft(list(shipment_in_draft))
