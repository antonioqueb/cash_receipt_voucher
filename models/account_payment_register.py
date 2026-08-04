# -*- coding: utf-8 -*-
"""Registrar pago con el monto EXACTO de lo recibido en la orden.

UNIFICADO: el wizard suma los RECIBOS DE EFECTIVO (cash.receipt) y los
COMPROBANTES DE PAGO (sale.payment.proof, transferencias) pendientes de
aplicar de los pedidos de la factura, y propone esa suma como monto.

Divisas cruzadas (pedido USD cobrado en MXN, o viceversa): cada documento
se convierte a la divisa del pago con el TC BANORTE del sistema (el mismo
de órdenes y costeo), y el wizard muestra el desglose completo.

Al crear el pago: los recibos quedan vinculados (payment_id → estado
'Pago Registrado') y los comprobantes pasan a 'Aplicado' (cerrando su
actividad) — nada se re-cuenta en pagos futuros.
"""
from odoo import api, fields, models


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    som_cash_receipt_ids = fields.Many2many(
        'cash.receipt',
        string='Recibos por aplicar',
        compute='_compute_som_receipt_info',
    )
    som_payment_proof_ids = fields.Many2many(
        'sale.payment.proof',
        string='Comprobantes por aplicar',
        compute='_compute_som_receipt_info',
    )
    som_receipt_note = fields.Text(
        string='Detalle de pagos recibidos',
        compute='_compute_som_receipt_info',
    )

    # ------------------------------------------------------------------
    # Universo de documentos pendientes
    # ------------------------------------------------------------------
    def _som_wizard_orders(self):
        self.ensure_one()
        moves = self.line_ids.mapped('move_id')
        return moves.mapped('invoice_line_ids.sale_line_ids.order_id')

    def _som_find_pending_receipts(self):
        """Recibos no cancelados y sin pago vinculado, en cualquier divisa."""
        self.ensure_one()
        orders = self._som_wizard_orders()
        if not orders:
            return self.env['cash.receipt']
        return self.env['cash.receipt'].search([
            ('sale_order_ids', 'in', orders.ids),
            ('state', 'in', ('draft', 'delivered')),
            ('payment_id', '=', False),
        ])

    def _som_find_pending_proofs(self):
        """Comprobantes de pago pendientes de aplicar, con monto capturado.
        (sale_payment_proof es dependencia declarada del módulo.)"""
        self.ensure_one()
        orders = self._som_wizard_orders()
        if not orders:
            return self.env['sale.payment.proof']
        return self.env['sale.payment.proof'].search([
            ('sale_order_id', 'in', orders.ids),
            ('state', '=', 'pending'),
            ('amount', '>', 0),
        ])

    # ------------------------------------------------------------------
    # Conversión al TC Banorte
    # ------------------------------------------------------------------
    def _som_amount_in_payment_currency(self, amount, currency_name):
        """(monto convertido, tasa usada o None). None/None si no se puede
        convertir (par distinto de USD/MXN o sin tasa)."""
        self.ensure_one()
        pay_cur = self.currency_id.name or ''
        amount = amount or 0.0

        if currency_name == pay_cur:
            return amount, None

        rate = self.env['sale.order']._get_banorte_rate()
        if not rate or rate <= 0:
            return None, None

        if currency_name == 'MXN' and pay_cur == 'USD':
            return amount / rate, rate
        if currency_name == 'USD' and pay_cur == 'MXN':
            return amount * rate, rate

        return None, None

    def _som_note_line(self, label, name, amount, currency_name, converted, rate):
        pay_cur = self.currency_id.name or ''
        base = '• %s %s: %s %s' % (
            label, name, '{:,.2f}'.format(amount or 0.0), currency_name)
        if rate:
            op = '×' if currency_name == 'USD' else '÷'
            return '%s %s TC Banorte %.4f = %s %s' % (
                base, op, rate, '{:,.2f}'.format(converted), pay_cur)
        return base

    def _som_receipts_summary(self):
        """(recibos aplicables, comprobantes aplicables, total, nota)."""
        self.ensure_one()
        receipts = self._som_find_pending_receipts()
        proofs = self._som_find_pending_proofs()
        ok_receipts = self.env['cash.receipt']
        ok_proofs = proofs.browse()
        total = 0.0
        note_lines = []
        pay_cur = self.currency_id.name or ''

        def _round(value):
            return self.currency_id.round(value) if self.currency_id else round(value, 2)

        for receipt in receipts:
            cur = receipt.currency_id.name or ''
            converted, rate = self._som_amount_in_payment_currency(receipt.amount, cur)
            if converted is None:
                note_lines.append(
                    '• Recibo %s: %s %s — divisa sin conversión disponible, '
                    'NO se incluye.' % (
                        receipt.name, '{:,.2f}'.format(receipt.amount or 0.0), cur))
                continue
            converted = _round(converted)
            ok_receipts |= receipt
            total += converted
            note_lines.append(self._som_note_line(
                'Recibo', receipt.name, receipt.amount, cur, converted, rate))

        for proof in proofs:
            cur = proof.currency_id.name or ''
            converted, rate = self._som_amount_in_payment_currency(proof.amount, cur)
            if converted is None:
                note_lines.append(
                    '• Comprobante %s: %s %s — divisa sin conversión '
                    'disponible, NO se incluye.' % (
                        proof.name or proof.reference or proof.id,
                        '{:,.2f}'.format(proof.amount or 0.0), cur))
                continue
            converted = _round(converted)
            ok_proofs |= proof
            total += converted
            note_lines.append(self._som_note_line(
                'Comprobante', proof.name or proof.reference or str(proof.id),
                proof.amount, cur, converted, rate))

        note = ''
        if note_lines:
            note = 'Pagos recibidos de esta orden (recibos y comprobantes):\n'
            note += '\n'.join(note_lines)
            note += '\n\nTotal propuesto: %s %s' % ('{:,.2f}'.format(total), pay_cur)
        return ok_receipts, ok_proofs, total, note

    # ------------------------------------------------------------------
    # Integración con el wizard
    # ------------------------------------------------------------------
    @api.depends('line_ids', 'currency_id')
    def _compute_som_receipt_info(self):
        for wizard in self:
            receipts, proofs, _total, note = wizard._som_receipts_summary()
            wizard.som_cash_receipt_ids = receipts
            wizard.som_payment_proof_ids = proofs
            wizard.som_receipt_note = note

    @api.depends('line_ids', 'currency_id')
    def _compute_amount(self):
        res = super()._compute_amount()
        for wizard in self:
            _r, _p, total, _note = wizard._som_receipts_summary()
            if total > 0:
                # Monto EXACTO de lo recibido (recibos + comprobantes,
                # convertido al TC Banorte si la divisa difiere).
                wizard.amount = total
        return res

    def _create_payments(self):
        payments = super()._create_payments()
        if len(payments) == 1:
            for wizard in self:
                receipts, proofs, _total, note = wizard._som_receipts_summary()
                applied_names = []
                if receipts:
                    receipts.write({
                        'payment_id': payments.id,
                        'state': 'paid',
                    })
                    applied_names += receipts.mapped('name')
                if proofs:
                    # Cierra también la actividad de aplicación pendiente.
                    proofs.action_mark_applied()
                    applied_names += [
                        p.name or p.reference or str(p.id) for p in proofs]
                if applied_names:
                    body = 'Pago aplicado sobre: %s.' % ', '.join(applied_names)
                    if note:
                        body += '\n' + note
                    payments.message_post(body=body)
        return payments
