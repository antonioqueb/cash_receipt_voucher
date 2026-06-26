import base64
from datetime import timedelta

from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero

# Nivel 1 (consulta): ve el control interno. Nivel 2 (edición): puede ajustarlo.
CASH_INTERNAL_VIEW_GROUP = 'cash_receipt_voucher.group_cash_internal_control'
CASH_INTERNAL_EDIT_GROUP = 'cash_receipt_voucher.group_cash_internal_control_edit'


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

    # ------------------------------------------------------------------
    # CONTROL INTERNO DE EFECTIVO (doble control)
    # ------------------------------------------------------------------
    # 'amount' es el monto OFICIAL: el que ve el cliente, el que va al recibo
    # PDF, al estado de cuenta y a la contabilidad. NUNCA se altera aquí.
    # 'amount_internal' es una capa PARALELA y RESTRINGIDA: el efectivo que
    # realmente se controló/ingresó a caja. Por defecto es igual al oficial y
    # solo el grupo 'Control Interno de Efectivo' puede ajustarlo. No impacta
    # ningún documento oficial: vive solo en el reporte de control interno.
    amount_internal = fields.Monetary(
        string='Efectivo Real (Control Interno)',
        currency_field='currency_id',
        copy=False,
        tracking=True,
        help='Efectivo realmente ingresado/controlado en caja. Por defecto es '
             'igual al Monto Recibido. Solo el grupo "Control Interno de '
             'Efectivo" puede modificarlo. No afecta el recibo, el estado de '
             'cuenta ni la contabilidad: es un control interno paralelo.',
    )
    amount_internal_diff = fields.Monetary(
        string='Diferencia de Caja',
        compute='_compute_amount_internal_diff',
        store=True,
        currency_field='currency_id',
        help='Monto Oficial menos Efectivo Real. '
             'Positivo = faltante de caja; negativo = sobrante.',
    )
    has_internal_diff = fields.Boolean(
        string='Tiene Diferencia',
        compute='_compute_amount_internal_diff',
        store=True,
    )
    internal_diff_reason = fields.Char(
        string='Motivo del Ajuste Interno',
        copy=False,
        tracking=True,
    )
    internal_adjusted_by = fields.Many2one(
        'res.users',
        string='Ajuste Interno por',
        readonly=True,
        copy=False,
    )
    internal_adjusted_date = fields.Datetime(
        string='Fecha de Ajuste Interno',
        readonly=True,
        copy=False,
    )
    can_adjust_internal = fields.Boolean(
        string='Puede Ajustar Efectivo Real',
        compute='_compute_can_adjust_internal',
        help='Técnico: verdadero si el usuario tiene el nivel "Ajuste de '
             'Efectivo Real". Controla el solo-lectura de los campos internos.',
    )

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

    @api.depends('amount', 'amount_internal', 'currency_id')
    def _compute_amount_internal_diff(self):
        for rec in self:
            rounding = rec.currency_id.rounding or 0.01
            diff = (rec.amount or 0.0) - (rec.amount_internal or 0.0)
            rec.amount_internal_diff = diff
            rec.has_internal_diff = not float_is_zero(diff, precision_rounding=rounding)

    # ------------------------------------------------------------------
    # Control interno: helpers de permiso/mirror
    # ------------------------------------------------------------------
    def _can_adjust_internal(self):
        """¿El usuario actual puede ajustar el efectivo real interno?"""
        return self.env.user.has_group(CASH_INTERNAL_GROUP)

    @staticmethod
    def _amounts_differ(a, b, currency=None):
        rounding = (currency.rounding if currency else 0.0) or 0.01
        return float_compare(a or 0.0, b or 0.0, precision_rounding=rounding) != 0

    @api.onchange('amount')
    def _onchange_amount_mirror_internal(self):
        """Mientras el efectivo real siga 'en espejo' con el oficial (sin ajuste
        interno), seguir el monto oficial. Si ya divergió, no se toca."""
        for rec in self:
            origin_amount = rec._origin.amount if rec._origin else 0.0
            if not rec.amount_internal or not rec._amounts_differ(
                    rec.amount_internal, origin_amount, rec.currency_id):
                rec.amount_internal = rec.amount

    @api.model_create_multi
    def create(self, vals_list):
        can_adjust = self._can_adjust_internal()
        for vals in vals_list:
            self._check_recent_duplicate(vals)
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cash.receipt') or _('Nuevo')
            # Espejo por defecto: el efectivo real arranca igual al oficial.
            if vals.get('amount_internal') in (None, False):
                vals['amount_internal'] = vals.get('amount', 0.0)
            elif not can_adjust:
                # Intento de nacer divergente sin permiso: forzar espejo.
                vals['amount_internal'] = vals.get('amount', 0.0)
                vals.pop('internal_diff_reason', None)
        records = super().create(vals_list)
        # Sellar auditoría de los que nacieron ya ajustados (solo grupo).
        for rec in records:
            if can_adjust and rec._amounts_differ(rec.amount_internal, rec.amount, rec.currency_id):
                rec.internal_adjusted_by = self.env.user
                rec.internal_adjusted_date = fields.Datetime.now()
        return records

    def write(self, vals):
        adjusting = 'amount_internal' in vals or 'internal_diff_reason' in vals
        if adjusting and not self._can_adjust_internal():
            # Permitido solo si en realidad no cambia el valor real interno.
            for rec in self:
                if 'amount_internal' in vals and rec._amounts_differ(
                        vals['amount_internal'], rec.amount_internal, rec.currency_id):
                    raise UserError(_(
                        'No tiene permisos para modificar el efectivo real '
                        '(control interno) del recibo %(name)s.\n'
                        'Se requiere pertenecer al grupo '
                        '"Control Interno de Efectivo".'
                    ) % {'name': rec.name})
                if 'internal_diff_reason' in vals and (vals.get('internal_diff_reason') or '') != (rec.internal_diff_reason or ''):
                    raise UserError(_(
                        'No tiene permisos para registrar el motivo del ajuste '
                        'interno. Se requiere el grupo "Control Interno de '
                        'Efectivo".'))
        res = super().write(vals)
        if 'amount_internal' in vals and self._can_adjust_internal():
            stamp = {
                'internal_adjusted_by': self.env.user.id,
                'internal_adjusted_date': fields.Datetime.now(),
            }
            for rec in self:
                super(CashReceipt, rec).write(stamp)
        return res

    @api.model
    def _check_recent_duplicate(self, vals):
        """Evita recibos duplicados por doble-clic: bloquea crear un recibo
        idéntico (mismo cliente, monto, divisa y pedidos) creado hace menos de un
        minuto. El registro unificado lo omite con 'skip_duplicate_check'."""
        if self.env.context.get('skip_duplicate_check'):
            return
        partner_id = vals.get('partner_id')
        amount = vals.get('amount')
        if not partner_id or not amount:
            return
        order_ids = []
        for cmd in (vals.get('sale_order_ids') or []):
            if not isinstance(cmd, (list, tuple)) or not cmd:
                continue
            if cmd[0] == 6 and len(cmd) > 2:
                order_ids = list(cmd[2] or [])
            elif cmd[0] == 4 and len(cmd) > 1:
                order_ids.append(cmd[1])
        threshold = fields.Datetime.now() - timedelta(seconds=60)
        domain = [
            ('partner_id', '=', partner_id),
            ('amount', '=', amount),
            ('state', '!=', 'cancelled'),
            ('create_date', '>=', threshold),
        ]
        if vals.get('currency_id'):
            domain.append(('currency_id', '=', vals['currency_id']))
        if order_ids:
            domain.append(('sale_order_ids', 'in', order_ids))
        dup = self.search(domain, limit=1)
        if dup:
            raise UserError(_(
                'Ya se registró un recibo de efectivo idéntico (%(name)s) hace '
                'menos de un minuto. Para evitar duplicados no se creará otro.\n'
                'Si de verdad necesitas un segundo recibo por el mismo monto, '
                'espera un minuto o cancela el anterior.'
            ) % {'name': dup.name})

    def action_deliver(self):
        """Marcar como entregado al cliente"""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Solo se pueden entregar recibos en estado borrador.'))
            rec.state = 'delivered'
            rec.message_post(
                body=Markup(_('Recibo entregado al cliente. <b>Recuerde registrar el pago formalmente en el sistema.</b>')),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
            # Recordatorio al CAJERO (se conserva): registrar el pago formal.
            rec.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_('Registrar pago formal - %s') % rec.name,
                note=_('Se entregó un recibo de efectivo por %s %s. '
                       'Registre el pago formalmente para completar el proceso.') % (
                    rec.amount, rec.currency_id.name),
                user_id=rec.received_by.id or self.env.user.id,
            )
            # Avisos UNIFICADOS en la(s) orden(es): Clara aplica + Lourdes/Zulema
            # generan la factura. Se suprime con 'skip_payment_notify' cuando el
            # registro unificado de pagos quiere notificar una sola vez.
            if not rec.env.context.get('skip_payment_notify'):
                rec._notify_orders_payment_received()

    def _render_receipt_pdf_bytes(self):
        """Renderiza el PDF del recibo. Devuelve los bytes o None si falla."""
        self.ensure_one()
        try:
            pdf_content, _ext = self.env['ir.actions.report']._render_qweb_pdf(
                'cash_receipt_voucher.action_report_cash_receipt', res_ids=self.ids,
            )
            return pdf_content
        except Exception:
            return None

    def _notify_orders_payment_received(self):
        """Dispara el motor unificado de avisos (definido en sale_payment_proof)
        para cada orden asociada, vinculando el PDF del recibo a la actividad."""
        self.ensure_one()
        orders = self.sale_order_ids
        if not orders or not hasattr(orders, '_payment_received_notify'):
            return
        pdf_bytes = self._render_receipt_pdf_bytes()
        for order in orders:
            attachments = self.env['ir.attachment']
            if pdf_bytes:
                attachments = self.env['ir.attachment'].create({
                    'name': '%s.pdf' % (self.name or 'recibo'),
                    'type': 'binary',
                    'datas': base64.b64encode(pdf_bytes),
                    'res_model': order._name,
                    'res_id': order.id,
                    'mimetype': 'application/pdf',
                })
            order._payment_received_notify(
                amount=self.amount,
                currency=self.currency_id,
                method_label=_('Efectivo'),
                reference=self.name,
                notes=self.notes or '',
                attachments=attachments or None,
                post_chatter=True,
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
