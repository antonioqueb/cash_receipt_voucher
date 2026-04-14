from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class CashReceiptWizard(models.TransientModel):
    _name = 'cash.receipt.wizard'
    _description = 'Wizard para Generar Recibo de Efectivo'

    sale_order_ids = fields.Many2many(
        'sale.order',
        string='Pedidos de Venta',
        required=True,
        domain="[('state', 'in', ('sale', 'done')), ('partner_id.commercial_partner_id', '=', commercial_partner_id)]",
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Cliente',
        required=True,
    )
    commercial_partner_id = fields.Many2one(
        'res.partner',
        string='Cliente Comercial',
        compute='_compute_commercial_partner_id',
    )
    amount = fields.Monetary(
        string='Monto a Recibir',
        required=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Divisa',
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company,
    )
    notes = fields.Text(string='Notas / Concepto de Pago')
    total_orders = fields.Monetary(
        string='Total de Pedidos',
        compute='_compute_totals',
        currency_field='currency_id',
    )
    already_received = fields.Monetary(
        string='Ya Recibido',
        compute='_compute_totals',
        currency_field='currency_id',
    )
    remaining = fields.Monetary(
        string='Pendiente',
        compute='_compute_totals',
        currency_field='currency_id',
    )
    deliver_immediately = fields.Boolean(
        string='Marcar como entregado inmediatamente',
        default=True,
        help='Si se activa, el recibo se marcará como entregado al cliente al generarse.',
    )
    # Firma
    signature = fields.Binary(string='Firma / Sello')
    signature_name = fields.Char(string='Nombre del Firmante')

    @api.depends('partner_id')
    def _compute_commercial_partner_id(self):
        for wiz in self:
            wiz.commercial_partner_id = wiz.partner_id.commercial_partner_id if wiz.partner_id else False

    @api.depends('sale_order_ids', 'amount')
    def _compute_totals(self):
        for wiz in self:
            wiz.total_orders = sum(wiz.sale_order_ids.mapped('amount_total'))
            # Recibos previos entregados o pagados
            existing = self.env['cash.receipt'].search([
                ('sale_order_ids', 'in', wiz.sale_order_ids.ids),
                ('state', 'in', ('delivered', 'paid')),
            ])
            wiz.already_received = sum(existing.mapped('amount'))
            wiz.remaining = wiz.total_orders - wiz.already_received - wiz.amount

    @api.onchange('sale_order_ids')
    def _onchange_sale_order_ids(self):
        if self.sale_order_ids:
            partners = self.sale_order_ids.mapped('partner_id')
            if partners:
                self.partner_id = partners[0]
            # Sugerir monto pendiente
            total = sum(self.sale_order_ids.mapped('amount_total'))
            existing = self.env['cash.receipt'].search([
                ('sale_order_ids', 'in', self.sale_order_ids.ids),
                ('state', 'in', ('delivered', 'paid')),
            ])
            already = sum(existing.mapped('amount'))
            suggested = total - already
            if suggested > 0:
                self.amount = suggested

    @api.constrains('amount')
    def _check_amount(self):
        for wiz in self:
            if wiz.amount <= 0:
                raise ValidationError(_('El monto debe ser mayor a cero.'))

    def action_generate_receipt(self):
        """Generar el recibo de efectivo"""
        self.ensure_one()

        receipt = self.env['cash.receipt'].create({
            'partner_id': self.partner_id.id,
            'sale_order_ids': [(6, 0, self.sale_order_ids.ids)],
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'notes': self.notes,
            'signature': self.signature,
            'signature_name': self.signature_name,
        })

        if self.deliver_immediately:
            receipt.action_deliver()

        # Retornar acción para ver el recibo e imprimir
        return {
            'type': 'ir.actions.act_window',
            'name': _('Recibo de Efectivo'),
            'res_model': 'cash.receipt',
            'res_id': receipt.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_generate_and_print(self):
        """Generar e imprimir inmediatamente"""
        self.ensure_one()

        receipt = self.env['cash.receipt'].create({
            'partner_id': self.partner_id.id,
            'sale_order_ids': [(6, 0, self.sale_order_ids.ids)],
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'notes': self.notes,
            'signature': self.signature,
            'signature_name': self.signature_name,
        })

        if self.deliver_immediately:
            receipt.action_deliver()

        return receipt.action_print_receipt()
