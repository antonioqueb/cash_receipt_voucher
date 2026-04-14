from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class CashReceipt(models.Model):
    _name = 'cash.receipt'
    _description = 'Comprobante de Pago en Efectivo'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Número de Recibo',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
    )
    date = fields.Datetime(
        string='Fecha de Recepción',
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Cliente',
        required=True,
        tracking=True,
    )
    sale_order_ids = fields.Many2many(
        'sale.order',
        'cash_receipt_sale_order_rel',
        'receipt_id',
        'order_id',
        string='Pedidos Asociados',
        tracking=True,
    )
    amount = fields.Monetary(
        string='Monto Recibido',
        required=True,
        tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Divisa',
        required=True,
        default=lambda self: self.env.company.currency_id,
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('delivered', 'Entregado al Cliente'),
        ('paid', 'Pago Registrado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', required=True, tracking=True, copy=False)

    notes = fields.Text(string='Notas / Concepto')
    received_by = fields.Many2one(
        'res.users',
        string='Recibido por',
        default=lambda self: self.env.user,
        tracking=True,
    )

    # Firma
    signature = fields.Binary(string='Firma / Sello')
    signature_name = fields.Char(string='Nombre del Firmante')

    # Vinculación con pago formal
    payment_id = fields.Many2one(
        'account.payment',
        string='Pago Registrado',
        readonly=True,
        copy=False,
        tracking=True,
    )
    payment_state = fields.Selection(
        related='payment_id.state',
        string='Estado del Pago',
        store=True,
    )

    # Campos calculados
    total_orders_amount = fields.Monetary(
        string='Total de Pedidos',
        compute='_compute_total_orders_amount',
        currency_field='currency_id',
    )
    pending_amount = fields.Monetary(
        string='Saldo Pendiente',
        compute='_compute_pending_amount',
        currency_field='currency_id',
    )
    is_fully_paid = fields.Boolean(
        string='Completamente Pagado',
        compute='_compute_pending_amount',
    )
    receipt_count = fields.Integer(
        string='Recibos Previos',
        compute='_compute_receipt_count',
    )

    @api.depends('sale_order_ids', 'sale_order_ids.amount_total')
    def _compute_total_orders_amount(self):
        for rec in self:
            rec.total_orders_amount = sum(rec.sale_order_ids.mapped('amount_total'))

    @api.depends('sale_order_ids', 'amount', 'total_orders_amount')
    def _compute_pending_amount(self):
        for rec in self:
            # Sumar todos los recibos pagados o entregados para estos pedidos
            other_receipts = self.search([
                ('sale_order_ids', 'in', rec.sale_order_ids.ids),
                ('state', 'in', ('delivered', 'paid')),
                ('id', '!=', rec.id if rec.id else 0),
            ])
            already_received = sum(other_receipts.mapped('amount'))
            rec.pending_amount = rec.total_orders_amount - already_received - rec.amount
            rec.is_fully_paid = rec.pending_amount <= 0

    @api.depends('sale_order_ids')
    def _compute_receipt_count(self):
        for rec in self:
            rec.receipt_count = self.search_count([
                ('sale_order_ids', 'in', rec.sale_order_ids.ids),
                ('id', '!=', rec.id if rec.id else 0),
            ])

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cash.receipt') or _('Nuevo')
        return super().create(vals_list)

    def action_deliver(self):
        """Marcar como entregado al cliente"""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Solo se pueden entregar recibos en estado borrador.'))
            rec.state = 'delivered'
            rec.message_post(
                body=_('Recibo entregado al cliente. <b>Recuerde registrar el pago formalmente en el sistema.</b>'),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
            # Crear actividad para recordar registrar el pago
            rec.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_('Registrar pago formal - %s') % rec.name,
                note=_('Se entregó un recibo de efectivo por %s %s. '
                       'Registre el pago formalmente para completar el proceso.') % (
                    rec.amount, rec.currency_id.name),
                user_id=rec.received_by.id or self.env.user.id,
            )

    def action_cancel(self):
        for rec in self:
            if rec.state == 'paid':
                raise UserError(_('No se puede cancelar un recibo ya vinculado a un pago registrado.'))
            rec.state = 'cancelled'

    def action_draft(self):
        for rec in self:
            if rec.state == 'paid':
                raise UserError(_('No se puede regresar a borrador un recibo ya vinculado a un pago.'))
            rec.state = 'draft'

    def action_register_payment(self):
        """Abrir wizard de registro de pago vinculado a las facturas de los pedidos"""
        self.ensure_one()
        # Buscar facturas de los pedidos asociados
        invoices = self.sale_order_ids.mapped('invoice_ids').filtered(
            lambda inv: inv.state == 'posted' and inv.payment_state != 'paid'
        )
        if not invoices:
            # Si no hay facturas, invitar a crear primero
            raise UserError(_(
                'No se encontraron facturas pendientes de pago para los pedidos asociados.\n\n'
                'Para registrar el pago formalmente:\n'
                '1. Primero cree las facturas desde los pedidos de venta\n'
                '2. Valide las facturas\n'
                '3. Regrese aquí para registrar el pago'
            ))

        # Abrir el wizard de pago de Odoo con los datos pre-llenados
        action = invoices.action_register_payment()
        # Pre-llenar con los datos del recibo
        if isinstance(action.get('context'), dict):
            action['context'].update({
                'default_amount': self.amount,
                'default_currency_id': self.currency_id.id,
                'default_journal_id': self._get_cash_journal().id if self._get_cash_journal() else False,
                'default_cash_receipt_id': self.id,
            })
        else:
            action['context'] = {
                'default_amount': self.amount,
                'default_currency_id': self.currency_id.id,
                'default_cash_receipt_id': self.id,
            }
        return action

    def _get_cash_journal(self):
        """Obtener diario de efectivo de la compañía"""
        return self.env['account.journal'].search([
            ('type', '=', 'cash'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)

    def action_view_payment(self):
        """Ver el pago vinculado"""
        self.ensure_one()
        if not self.payment_id:
            return
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'res_id': self.payment_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print_receipt(self):
        """Imprimir el recibo"""
        return self.env.ref('cash_receipt_voucher.action_report_cash_receipt').report_action(self)

    @api.onchange('sale_order_ids')
    def _onchange_sale_order_ids(self):
        if self.sale_order_ids:
            # Tomar el partner del primer pedido
            partners = self.sale_order_ids.mapped('partner_id')
            if len(partners) > 1:
                # Verificar que todos sean del mismo cliente (o padre)
                commercial_partners = partners.mapped('commercial_partner_id')
                if len(commercial_partners) > 1:
                    return {
                        'warning': {
                            'title': _('Advertencia'),
                            'message': _('Los pedidos seleccionados pertenecen a diferentes clientes. '
                                        'Se tomará el cliente del primer pedido.'),
                        }
                    }
            self.partner_id = partners[0]
            # Sugerir el monto total pendiente
            if not self.amount:
                self.amount = sum(self.sale_order_ids.mapped('amount_total'))
