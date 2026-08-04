from odoo import models, fields, api, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_som_apply_payments(self):
        """Aplicar pago desde la pestaña unificada: abre el wizard estándar
        de pago sobre las facturas publicadas pendientes de la orden. El
        wizard propone el monto EXACTO de los recibos y comprobantes
        pendientes (convertidos al TC Banorte si la divisa difiere)."""
        self.ensure_one()
        invoices = self.invoice_ids.filtered(
            lambda m: m.state == 'posted'
            and m.payment_state in ('not_paid', 'partial')
        )
        if not invoices:
            raise UserError(_(
                'No hay facturas publicadas pendientes de pago en esta orden.\n\n'
                'Para aplicar el pago:\n'
                '1. Crea la factura desde la orden\n'
                '2. Publícala (Confirmar)\n'
                '3. Regresa aquí y vuelve a intentar'
            ))
        return invoices.action_register_payment()

    cash_receipt_ids = fields.Many2many(
        'cash.receipt',
        'cash_receipt_sale_order_rel',
        'order_id',
        'receipt_id',
        string='Recibos de Efectivo',
    )
    cash_receipt_count = fields.Integer(
        string='Recibos',
        compute='_compute_cash_receipt_count',
    )
    cash_received_amount = fields.Monetary(
        string='Efectivo Recibido',
        compute='_compute_cash_received_amount',
        currency_field='currency_id',
    )
    cash_receipt_pending = fields.Boolean(
        string='Recibo Pendiente de Pago',
        compute='_compute_cash_receipt_pending',
    )

    @api.depends('cash_receipt_ids')
    def _compute_cash_receipt_count(self):
        for order in self:
            order.cash_receipt_count = len(order.cash_receipt_ids)

    @api.depends('cash_receipt_ids', 'cash_receipt_ids.amount', 'cash_receipt_ids.state')
    def _compute_cash_received_amount(self):
        for order in self:
            order.cash_received_amount = sum(
                order.cash_receipt_ids.filtered(
                    lambda r: r.state in ('delivered', 'paid')
                ).mapped('amount')
            )

    @api.depends('cash_receipt_ids', 'cash_receipt_ids.state')
    def _compute_cash_receipt_pending(self):
        for order in self:
            order.cash_receipt_pending = any(
                r.state == 'delivered' for r in order.cash_receipt_ids
            )

    def action_open_cash_receipt_wizard(self):
        """Abrir wizard para crear recibo de efectivo"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Generar Recibo de Efectivo'),
            'res_model': 'cash.receipt.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_sale_order_ids': [(6, 0, self.ids)],
                'default_partner_id': self.partner_id.id,
                'default_amount': self.amount_total,
                'default_currency_id': self.currency_id.id,
                'active_id': self.id,
            },
        }

    def action_view_cash_receipts(self):
        """Ver recibos de efectivo asociados"""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Recibos de Efectivo'),
            'res_model': 'cash.receipt',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.cash_receipt_ids.ids)],
            'context': {'default_sale_order_ids': [(6, 0, self.ids)]},
        }
        if len(self.cash_receipt_ids) == 1:
            action['view_mode'] = 'form'
            action['res_id'] = self.cash_receipt_ids.id
        return action
