# -*- coding: utf-8 -*-
"""Registrar pago con el monto EXACTO de los recibos de efectivo.

Cuando la factura que se está pagando viene de pedidos con recibos de
efectivo/transferencia registrados (cash.receipt) aún no vinculados a un
pago, el wizard propone como monto la SUMA exacta de esos recibos (misma
divisa). Al crear el pago, los recibos quedan vinculados (payment_id) y
pasan a estado 'Pago Registrado' — así un segundo pago ya no los re-cuenta.
"""
from odoo import api, fields, models


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    som_cash_receipt_ids = fields.Many2many(
        'cash.receipt',
        string='Recibos por aplicar',
        compute='_compute_som_cash_receipt_ids',
        help='Recibos de efectivo de los pedidos de esta factura que aún no '
             'están vinculados a ningún pago. El monto propuesto es su suma.',
    )

    def _som_find_pending_receipts(self):
        """Recibos no cancelados y sin pago vinculado, de los pedidos de las
        facturas del wizard, en la MISMA divisa del pago."""
        self.ensure_one()
        moves = self.line_ids.mapped('move_id')
        orders = moves.mapped('invoice_line_ids.sale_line_ids.order_id')
        if not orders:
            return self.env['cash.receipt']
        return self.env['cash.receipt'].search([
            ('sale_order_ids', 'in', orders.ids),
            ('state', 'in', ('draft', 'delivered')),
            ('payment_id', '=', False),
            ('currency_id', '=', self.currency_id.id),
        ])

    @api.depends('line_ids', 'currency_id')
    def _compute_som_cash_receipt_ids(self):
        for wizard in self:
            wizard.som_cash_receipt_ids = wizard._som_find_pending_receipts()

    @api.depends('line_ids', 'currency_id')
    def _compute_amount(self):
        res = super()._compute_amount()
        for wizard in self:
            receipts = wizard._som_find_pending_receipts()
            total = sum(receipts.mapped('amount'))
            if total > 0:
                # Monto EXACTO registrado en recibos: es lo que físicamente
                # se recibió (efectivo o transferencia), no el residual.
                wizard.amount = total
        return res

    def _create_payments(self):
        payments = super()._create_payments()
        # Vincular los recibos aplicados al pago creado (pago único: el flujo
        # normal de una factura). Con esto el recibo pasa a 'Pago Registrado'
        # y deja de proponerse en pagos futuros.
        if len(payments) == 1:
            for wizard in self:
                receipts = wizard._som_find_pending_receipts()
                if receipts:
                    receipts.write({
                        'payment_id': payments.id,
                        'state': 'paid',
                    })
                    payments.message_post(body=(
                        'Pago aplicado sobre recibo(s) de efectivo: %s.'
                        % ', '.join(receipts.mapped('name'))
                    ))
        return payments
