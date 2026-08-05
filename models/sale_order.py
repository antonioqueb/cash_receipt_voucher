from odoo import models, fields, api, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_som_apply_payments(self):
        """Aplicar pago desde la pestaña unificada: abre el wizard estándar
        de pago sobre las facturas publicadas pendientes de la orden. El
        wizard propone el monto EXACTO de los recibos y comprobantes
        pendientes (convertidos al TC Banorte si la divisa difiere).

        FLUJO DIRECTO: si la orden aún no tiene factura publicada, aquí
        mismo se crea (o se toma la borrador existente), se publica y se
        abre el wizard — sin pasos manuales intermedios."""
        self.ensure_one()

        def _pending(moves):
            return moves.filtered(
                lambda m: m.move_type == 'out_invoice'
                and m.state == 'posted'
                and m.payment_state in ('not_paid', 'partial')
            )

        invoices = _pending(self.invoice_ids)

        if not invoices:
            # 1) Facturas en borrador ya creadas: solo publicarlas.
            drafts = self.invoice_ids.filtered(
                lambda m: m.move_type == 'out_invoice' and m.state == 'draft')

            # 2) Nada en borrador: crear la factura desde la orden.
            if not drafts:
                if self.state not in ('sale', 'done'):
                    raise UserError(_(
                        'La orden debe estar CONFIRMADA para poder facturar '
                        'y aplicar el pago.'
                    ))
                try:
                    # Sin skip_auth_check: los candados de autorización
                    # (descuentos/precios) siguen aplicando.
                    drafts = self._create_invoices(final=True)
                except UserError:
                    raise
                except Exception as exc:
                    raise UserError(_(
                        'No se pudo crear la factura automáticamente: %s'
                    ) % exc)

            drafts = drafts.filtered(lambda m: m.state == 'draft')
            if drafts:
                drafts.action_post()

            invoices = _pending(self.invoice_ids)

        if not invoices:
            raise UserError(_(
                'No se encontró nada pendiente de facturar ni facturas con '
                'saldo por pagar en esta orden.'
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
