# -*- coding: utf-8 -*-
"""Registrar pago con el monto EXACTO de los recibos de efectivo.

Cuando la factura que se está pagando viene de pedidos con recibos de
efectivo/transferencia registrados (cash.receipt) aún no vinculados a un
pago, el wizard propone como monto la SUMA exacta de esos recibos.

Divisas cruzadas (pedido USD cobrado en MXN, o viceversa): el recibo se
convierte a la divisa del pago con el TC BANORTE del sistema (el mismo de
órdenes y costeo), y el wizard muestra el desglose de la conversión para
que el cajero vea de dónde sale el número.

Al crear el pago, los recibos quedan vinculados (payment_id) y pasan a
'Pago Registrado' — un segundo pago ya no los re-cuenta.
"""
from odoo import api, fields, models


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    som_cash_receipt_ids = fields.Many2many(
        'cash.receipt',
        string='Recibos por aplicar',
        compute='_compute_som_receipt_info',
        help='Recibos de efectivo de los pedidos de esta factura que aún no '
             'están vinculados a ningún pago. El monto propuesto es su suma '
             '(convertida al TC Banorte si la divisa difiere).',
    )
    som_receipt_note = fields.Text(
        string='Detalle de recibos',
        compute='_compute_som_receipt_info',
    )

    def _som_find_pending_receipts(self):
        """Recibos no cancelados y sin pago vinculado, de los pedidos de las
        facturas del wizard — en CUALQUIER divisa (la conversión al TC
        Banorte se hace aparte)."""
        self.ensure_one()
        moves = self.line_ids.mapped('move_id')
        orders = moves.mapped('invoice_line_ids.sale_line_ids.order_id')
        if not orders:
            return self.env['cash.receipt']
        return self.env['cash.receipt'].search([
            ('sale_order_ids', 'in', orders.ids),
            ('state', 'in', ('draft', 'delivered')),
            ('payment_id', '=', False),
        ])

    def _som_receipt_amount_in_payment_currency(self, receipt):
        """Monto del recibo en la divisa del pago. Mismo par USD/MXN al TC
        Banorte del sistema; otras combinaciones no se convierten (None)."""
        self.ensure_one()
        pay_cur = self.currency_id.name or ''
        rec_cur = receipt.currency_id.name or ''
        amount = receipt.amount or 0.0

        if rec_cur == pay_cur:
            return amount, None

        rate = self.env['sale.order']._get_banorte_rate()
        if not rate or rate <= 0:
            return None, None

        if rec_cur == 'MXN' and pay_cur == 'USD':
            return amount / rate, rate
        if rec_cur == 'USD' and pay_cur == 'MXN':
            return amount * rate, rate

        return None, None

    def _som_receipts_summary(self):
        """(recibos aplicables, total en divisa del pago, nota de desglose)."""
        self.ensure_one()
        receipts = self._som_find_pending_receipts()
        applicable = self.env['cash.receipt']
        total = 0.0
        note_lines = []
        pay_cur = self.currency_id.name or ''

        for receipt in receipts:
            converted, rate = self._som_receipt_amount_in_payment_currency(receipt)
            if converted is None:
                note_lines.append(
                    '• %s: %s %s — divisa sin conversión disponible, NO se '
                    'incluye en el monto propuesto.' % (
                        receipt.name,
                        '{:,.2f}'.format(receipt.amount or 0.0),
                        receipt.currency_id.name or '',
                    )
                )
                continue
            converted = self.currency_id.round(converted) if self.currency_id else round(converted, 2)
            applicable |= receipt
            total += converted
            if rate:
                note_lines.append(
                    '• %s: %s %s × TC Banorte %.4f = %s %s' % (
                        receipt.name,
                        '{:,.2f}'.format(receipt.amount or 0.0),
                        receipt.currency_id.name or '',
                        rate,
                        '{:,.2f}'.format(converted),
                        pay_cur,
                    ) if (receipt.currency_id.name or '') == 'USD' else
                    '• %s: %s %s ÷ TC Banorte %.4f = %s %s' % (
                        receipt.name,
                        '{:,.2f}'.format(receipt.amount or 0.0),
                        receipt.currency_id.name or '',
                        rate,
                        '{:,.2f}'.format(converted),
                        pay_cur,
                    )
                )
            else:
                note_lines.append(
                    '• %s: %s %s' % (
                        receipt.name,
                        '{:,.2f}'.format(converted),
                        pay_cur,
                    )
                )

        note = ''
        if note_lines:
            note = 'Recibos registrados de esta orden:\n' + '\n'.join(note_lines)
            note += '\n\nTotal propuesto: %s %s' % ('{:,.2f}'.format(total), pay_cur)
        return applicable, total, note

    @api.depends('line_ids', 'currency_id')
    def _compute_som_receipt_info(self):
        for wizard in self:
            applicable, _total, note = wizard._som_receipts_summary()
            wizard.som_cash_receipt_ids = applicable
            wizard.som_receipt_note = note

    @api.depends('line_ids', 'currency_id')
    def _compute_amount(self):
        res = super()._compute_amount()
        for wizard in self:
            _applicable, total, _note = wizard._som_receipts_summary()
            if total > 0:
                # Monto EXACTO registrado en recibos (convertido al TC
                # Banorte si la divisa difiere): es lo que físicamente se
                # recibió, no el residual de la factura.
                wizard.amount = total
        return res

    def _create_payments(self):
        payments = super()._create_payments()
        # Vincular los recibos aplicados al pago creado (pago único: el flujo
        # normal de una factura). El recibo pasa a 'Pago Registrado' y deja
        # de proponerse en pagos futuros.
        if len(payments) == 1:
            for wizard in self:
                applicable, _total, note = wizard._som_receipts_summary()
                if applicable:
                    applicable.write({
                        'payment_id': payments.id,
                        'state': 'paid',
                    })
                    body = 'Pago aplicado sobre recibo(s): %s.' % (
                        ', '.join(applicable.mapped('name')))
                    if note:
                        body += '\n' + note
                    payments.message_post(body=body)
        return payments
