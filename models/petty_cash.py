# -*- coding: utf-8 -*-
"""Caja Chica Operaciones.

Fondo pequeño que el Administrador de Efectivo ENTREGA desde la caja
principal (una Salida de Caja marcada "Entrega a Caja Chica"). Caja Chica
lo RECIBE (ingreso pendiente → recibido, doble control) y va dando salida
con egresos registrados uno por uno: monto, concepto, categoría, a quién se
pagó, referencia del ticket y foto del comprobante en el chatter.

Reglas de control:
- Un egreso nunca puede dejar el saldo en negativo.
- Un movimiento registrado no se edita (monto/fecha/tipo): se cancela con
  motivo (solo Responsable de Caja Chica o Administrador de Efectivo) y se
  registra de nuevo. Todo queda en el chatter.
"""
from datetime import timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

from .som_date_format import som_format_date

GROUP_USER = 'cash_receipt_voucher.group_petty_cash_user'
GROUP_MANAGER = 'cash_receipt_voucher.group_petty_cash_manager'
GROUP_CASH_ADMIN = 'cash_receipt_voucher.group_cash_internal_control_edit'


class PettyCashCategory(models.Model):
    _name = 'petty.cash.category'
    _description = 'Categoría de gasto de Caja Chica'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()

    _name_uniq = models.Constraint('unique(name)', 'Ya existe una categoría con ese nombre.')


class PettyCashEntry(models.Model):
    _name = 'petty.cash.entry'
    _description = 'Movimiento de Caja Chica'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _rec_name = 'name'

    _name_company_uniq = models.Constraint(
        'unique(name, company_id)', 'Ya existe un movimiento de caja chica con ese folio.')

    name = fields.Char(string='Folio', required=True, copy=False, readonly=True, default='/')
    entry_type = fields.Selection([
        ('in', 'Ingreso'),
        ('out', 'Egreso'),
    ], string='Tipo', required=True, default='out', index=True)
    state = fields.Selection([
        ('pending', 'Pendiente de recibir'),
        ('posted', 'Registrado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='posted', tracking=True, index=True)

    date = fields.Datetime(string='Fecha', required=True, default=fields.Datetime.now, tracking=True)
    amount = fields.Monetary(string='Monto', required=True, tracking=True)
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.ref('base.MXN', raise_if_not_found=False) or self.env.company.currency_id)
    concept = fields.Char(string='Concepto', required=True, tracking=True)
    category_id = fields.Many2one('petty.cash.category', string='Categoría', tracking=True)
    paid_to = fields.Char(string='Pagado a / Recibido de', tracking=True,
                          help='Persona o negocio. En egresos: a quién se le pagó.')
    partner_id = fields.Many2one('res.partner', string='Contacto (opcional)')
    reference = fields.Char(string='Referencia / Ticket', help='Folio del ticket, nota o factura.')
    notes = fields.Text(string='Notas')
    has_receipt = fields.Boolean(string='Comprobante adjunto', compute='_compute_has_receipt')

    user_id = fields.Many2one('res.users', string='Registró', required=True,
                              default=lambda self: self.env.user, readonly=True)
    received_by = fields.Many2one('res.users', string='Recibió', readonly=True)
    received_date = fields.Datetime(string='Fecha de recepción', readonly=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)

    # Origen: Salida de la caja principal marcada "Entrega a Caja Chica".
    disbursement_id = fields.Many2one('cash.disbursement', string='Salida de Caja (origen)',
                                      readonly=True, ondelete='set null')
    cancel_reason = fields.Char(string='Motivo de cancelación', readonly=True)
    cancelled_by = fields.Many2one('res.users', readonly=True)
    cancelled_date = fields.Datetime(readonly=True)

    signed_amount = fields.Monetary(string='Importe (±)', compute='_compute_signed_amount', store=True)

    @api.depends('amount', 'entry_type', 'state')
    def _compute_signed_amount(self):
        for rec in self:
            if rec.state != 'posted':
                rec.signed_amount = 0.0
            else:
                rec.signed_amount = rec.amount if rec.entry_type == 'in' else -rec.amount

    def _compute_has_receipt(self):
        Att = self.env['ir.attachment'].sudo()
        for rec in self:
            rec.has_receipt = bool(rec.id) and Att.search_count([
                ('res_model', '=', self._name), ('res_id', '=', rec.id)], limit=1) > 0

    # ------------------------------------------------------------------
    # Permisos
    # ------------------------------------------------------------------
    @api.model
    def _is_manager(self):
        u = self.env.user
        return self.env.su or u.has_group(GROUP_MANAGER) or u.has_group(GROUP_CASH_ADMIN)

    # ------------------------------------------------------------------
    # Saldo
    # ------------------------------------------------------------------
    @api.model
    def _balance(self, company=None, until=None, exclude_ids=None):
        """Saldo registrado (ingresos − egresos) hasta `until` (Datetime)."""
        company = company or self.env.company
        domain = [('state', '=', 'posted'), ('company_id', '=', company.id)]
        if until:
            domain.append(('date', '<=', until))
        if exclude_ids:
            domain.append(('id', 'not in', list(exclude_ids)))
        groups = self.sudo()._read_group(domain, ['entry_type'], ['amount:sum'])
        total = 0.0
        for entry_type, amount in groups:
            total += amount if entry_type == 'in' else -amount
        return total

    # ------------------------------------------------------------------
    # Validaciones
    # ------------------------------------------------------------------
    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_('El monto debe ser mayor a cero.'))

    @api.constrains('entry_type', 'category_id', 'state')
    def _check_category(self):
        for rec in self:
            if rec.entry_type == 'out' and rec.state == 'posted' and not rec.category_id:
                raise ValidationError(_('Todo egreso de caja chica debe llevar categoría.'))

    @api.constrains('amount', 'entry_type', 'state', 'company_id')
    def _check_balance(self):
        for rec in self:
            if rec.entry_type != 'out' or rec.state != 'posted':
                continue
            available = rec._balance(rec.company_id, exclude_ids=[rec.id])
            if rec.currency_id.compare_amounts(rec.amount, available) > 0:
                raise ValidationError(_(
                    'Saldo insuficiente en caja chica: disponible %(disp)s, egreso %(amt)s.',
                    disp=rec.currency_id.format(available) if hasattr(rec.currency_id, 'format') else round(available, 2),
                    amt=rec.currency_id.format(rec.amount) if hasattr(rec.currency_id, 'format') else rec.amount))

    # ------------------------------------------------------------------
    # CRUD con control
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                code = 'petty.cash.in' if vals.get('entry_type', 'out') == 'in' else 'petty.cash.out'
                vals['name'] = self.env['ir.sequence'].next_by_code(code) or '/'
        recs = super().create(vals_list)
        for rec in recs.filtered(lambda r: r.state == 'posted'):
            rec._log_posted()
        return recs

    _LOCKED = {'amount', 'date', 'entry_type', 'currency_id', 'company_id'}

    def write(self, vals):
        if self._LOCKED & set(vals) and not self.env.context.get('petty_cash_internal'):
            locked = self.filtered(lambda r: r.state == 'posted')
            if locked:
                raise UserError(_(
                    'Un movimiento registrado no se edita (%s). Cancélalo con motivo y captúralo de nuevo.'
                ) % ', '.join(locked.mapped('name')))
        return super().write(vals)

    def unlink(self):
        raise UserError(_('Los movimientos de caja chica no se borran: se cancelan con motivo.'))

    def _log_posted(self):
        self.ensure_one()
        kind = 'Ingreso' if self.entry_type == 'in' else 'Egreso'
        self.message_post(body=_('%(kind)s registrado por %(user)s: %(amt)s · %(concept)s',
                                 kind=kind, user=self.env.user.name, amt=self.amount, concept=self.concept),
                          message_type='notification')

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_receive(self):
        """El operador confirma que recibió el efectivo entregado por la caja
        principal: el ingreso pendiente queda registrado."""
        for rec in self:
            if rec.state != 'pending':
                raise UserError(_('Solo se reciben ingresos pendientes.'))
            rec.with_context(petty_cash_internal=True).write({
                'state': 'posted',
                'date': fields.Datetime.now(),
                'received_by': self.env.user.id,
                'received_date': fields.Datetime.now(),
            })
            rec.message_post(body=_('Efectivo recibido por %s (%s).') % (self.env.user.name, rec.amount),
                             message_type='notification')
            if rec.disbursement_id:
                rec.disbursement_id.message_post(
                    body=_('Caja Chica confirmó la recepción: %s.') % rec.name, message_type='notification')
        return True

    def action_cancel(self, reason=None):
        if not self._is_manager():
            raise UserError(_('Solo el Responsable de Caja Chica o el Administrador de Efectivo pueden cancelar.'))
        reason = (reason or self.env.context.get('cancel_reason') or '').strip()
        if not reason:
            return {
                'type': 'ir.actions.act_window', 'res_model': 'petty.cash.cancel.wizard',
                'view_mode': 'form', 'target': 'new',
                'context': {'default_entry_ids': [(6, 0, self.ids)]},
            }
        for rec in self:
            if rec.state == 'cancelled':
                continue
            # Cancelar un ingreso ya gastado dejaría saldo negativo.
            if rec.entry_type == 'in' and rec.state == 'posted':
                remaining = rec._balance(rec.company_id, exclude_ids=[rec.id])
                if rec.currency_id.compare_amounts(remaining, 0.0) < 0:
                    raise UserError(_(
                        'No se puede cancelar %s: ese ingreso ya se gastó (el saldo quedaría en %s).'
                    ) % (rec.name, round(remaining, 2)))
            rec.with_context(petty_cash_internal=True).write({
                'state': 'cancelled', 'cancel_reason': reason,
                'cancelled_by': self.env.user.id, 'cancelled_date': fields.Datetime.now(),
            })
            rec.message_post(body=_('Cancelado por %s: %s') % (self.env.user.name, reason),
                             message_type='notification')
        return True

    # ------------------------------------------------------------------
    # Panel (client action) — datos simples
    # ------------------------------------------------------------------
    @api.model
    def _period_bounds(self, period, date_from=False, date_to=False):
        today = fields.Date.context_today(self)
        if period == 'today':
            df = dt = today
        elif period == 'week':
            df = today - timedelta(days=today.weekday()); dt = today
        elif period == 'year':
            df = today.replace(month=1, day=1); dt = today
        elif period == 'custom' and date_from and date_to:
            df = fields.Date.to_date(date_from); dt = fields.Date.to_date(date_to)
        elif period == 'all':
            df = None; dt = today
        else:  # month
            df = today.replace(day=1); dt = today
        return df, dt

    @api.model
    def _dt_range(self, df, dt):
        """Límites del día en la zona del usuario → naive UTC (como guarda Odoo)."""
        from datetime import datetime, time as dtime
        import pytz
        tz = pytz.timezone(self.env.user.tz or 'America/Mexico_City')
        start = tz.localize(datetime.combine(df, dtime.min)).astimezone(pytz.utc).replace(tzinfo=None) if df else None
        end = tz.localize(datetime.combine(dt, dtime.max)).astimezone(pytz.utc).replace(tzinfo=None)
        return start, end

    @api.model
    def get_petty_dashboard(self, period='month', date_from=False, date_to=False):
        company = self.env.company
        cur = self.env.ref('base.MXN', raise_if_not_found=False) or company.currency_id
        df, dt = self._period_bounds(period, date_from, date_to)
        start, end = self._dt_range(df, dt)

        dom = [('company_id', '=', company.id), ('date', '<=', end)]
        if start:
            dom.append(('date', '>=', start))
        moves = self.search(dom + [('state', '!=', 'cancelled')], order='date asc, id asc')

        initial = self._balance(company, until=start - timedelta(seconds=1)) if start else 0.0
        balance_now = self._balance(company)
        period_in = sum(m.amount for m in moves if m.entry_type == 'in' and m.state == 'posted')
        period_out = sum(m.amount for m in moves if m.entry_type == 'out' and m.state == 'posted')

        rows, running = [], initial
        for m in moves:
            if m.state == 'posted':
                running += m.amount if m.entry_type == 'in' else -m.amount
            rows.append({
                'id': m.id, 'name': m.name, 'type': m.entry_type, 'state': m.state,
                'date': som_format_date(fields.Datetime.context_timestamp(self, m.date), with_time=True),
                'concept': m.concept, 'category': m.category_id.name or '',
                'paid_to': m.paid_to or '', 'reference': m.reference or '',
                'amount': round(m.amount, 2), 'balance': round(running, 2) if m.state == 'posted' else None,
                'user': m.user_id.name or '', 'has_receipt': m.has_receipt,
            })
        rows.reverse()

        pending = self.search([('company_id', '=', company.id), ('state', '=', 'pending')], order='date asc')
        by_cat = {}
        for m in moves.filtered(lambda r: r.entry_type == 'out' and r.state == 'posted'):
            key = m.category_id.name or 'Sin categoría'
            by_cat[key] = by_cat.get(key, 0.0) + m.amount
        cats = sorted(({'name': k, 'amount': round(v, 2)} for k, v in by_cat.items()), key=lambda x: -x['amount'])

        return {
            'currency_symbol': cur.symbol or '$',
            'period_label': '%s – %s' % (som_format_date(df) if df else 'inicio', som_format_date(dt)),
            'kpis': {
                'balance': round(balance_now, 2),
                'initial': round(initial, 2),
                'period_in': round(period_in, 2),
                'period_out': round(period_out, 2),
                'count': len([r for r in rows if r['state'] == 'posted']),
                'pending_count': len(pending),
                'pending_amount': round(sum(pending.mapped('amount')), 2),
            },
            'rows': rows,
            'pending': [{
                'id': p.id, 'name': p.name, 'amount': round(p.amount, 2), 'concept': p.concept,
                'from': p.disbursement_id.user_id.name if p.disbursement_id else (p.paid_to or ''),
                'date': som_format_date(fields.Datetime.context_timestamp(self, p.date)),
            } for p in pending],
            'categories': [{'id': c.id, 'name': c.name} for c in self.env['petty.cash.category'].search([])],
            'by_category': cats,
            'is_manager': self._is_manager(),
        }

    @api.model
    def quick_create(self, vals):
        """Alta rápida desde el panel: valida lo mínimo y registra."""
        entry_type = vals.get('entry_type', 'out')
        amount = float(vals.get('amount') or 0)
        if amount <= 0:
            raise UserError(_('Captura un monto mayor a cero.'))
        concept = (vals.get('concept') or '').strip()
        if not concept:
            raise UserError(_('Captura el concepto.'))
        if entry_type == 'out' and not vals.get('category_id'):
            raise UserError(_('Elige la categoría del egreso.'))
        rec = self.create({
            'entry_type': entry_type,
            'amount': amount,
            'concept': concept,
            'category_id': int(vals['category_id']) if vals.get('category_id') else False,
            'paid_to': (vals.get('paid_to') or '').strip() or False,
            'reference': (vals.get('reference') or '').strip() or False,
            'notes': (vals.get('notes') or '').strip() or False,
            'state': 'posted',
        })
        return {'id': rec.id, 'name': rec.name}

    @api.model
    def action_print_period_report(self, period='month', date_from=False, date_to=False):
        df, dt = self._period_bounds(period, date_from, date_to)
        data = {'period': period, 'date_from': fields.Date.to_string(df) if df else False,
                'date_to': fields.Date.to_string(dt)}
        return self.env.ref('cash_receipt_voucher.action_report_petty_cash_period').report_action(self, data=data)


class PettyCashCancelWizard(models.TransientModel):
    _name = 'petty.cash.cancel.wizard'
    _description = 'Cancelar movimiento de caja chica'

    entry_ids = fields.Many2many('petty.cash.entry', string='Movimientos', required=True)
    reason = fields.Char(string='Motivo', required=True)

    def action_confirm(self):
        self.entry_ids.action_cancel(reason=self.reason)
        return {'type': 'ir.actions.act_window_close'}


class CashDisbursementPetty(models.Model):
    """Salida de la caja principal que ENTREGA fondo a Caja Chica."""
    _inherit = 'cash.disbursement'

    petty_cash_transfer = fields.Boolean(
        string='Entrega a Caja Chica', tracking=True,
        help='Marca esta salida como fondeo de Caja Chica Operaciones: se genera un ingreso '
             'pendiente que el operador de caja chica confirma al recibir el efectivo.')
    petty_entry_id = fields.Many2one('petty.cash.entry', string='Ingreso en Caja Chica', readonly=True, copy=False)
    petty_entry_state = fields.Selection(related='petty_entry_id.state', string='Recepción')

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.filtered('petty_cash_transfer')._petty_ensure_entry()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if vals.get('petty_cash_transfer'):
            self._petty_ensure_entry()
        return res

    def _petty_ensure_entry(self):
        Entry = self.env['petty.cash.entry'].sudo()
        for rec in self:
            if rec.petty_entry_id or rec.state != 'done':
                continue
            mxn = self.env.ref('base.MXN', raise_if_not_found=False)
            entry = Entry.create({
                'entry_type': 'in',
                'state': 'pending',
                'amount': rec.amount_mxn if (mxn and rec.currency_id != mxn) else rec.amount,
                'concept': _('Fondeo desde caja principal %s') % rec.name,
                'paid_to': rec.delivered_to,
                'date': rec.date,
                'user_id': rec.user_id.id,
                'company_id': rec.company_id.id,
                'disbursement_id': rec.id,
            })
            rec.petty_entry_id = entry
            rec.message_post(body=_('Entrega a Caja Chica registrada: ingreso %s pendiente de recibir.') % entry.name,
                             message_type='notification')
            for user in self.env['res.users'].sudo().search([('active', '=', True), ('share', '=', False)]).filtered(
                    lambda u: u.has_group(GROUP_USER)):
                entry.activity_schedule('mail.mail_activity_data_todo', user_id=user.id,
                                        summary=_('Recibir fondo de caja chica'),
                                        note=_('%s entregó %s. Confirma la recepción en Caja Chica.') % (
                                            rec.user_id.name, entry.amount))

    def action_cancel(self):
        for rec in self:
            entry = rec.petty_entry_id
            if entry and entry.state == 'posted':
                raise UserError(_(
                    'Caja Chica ya recibió este fondo (%s). Cancela primero el ingreso en Caja Chica.') % entry.name)
            if entry and entry.state == 'pending':
                entry.sudo().with_context(petty_cash_internal=True).write({
                    'state': 'cancelled', 'cancel_reason': _('Salida de caja cancelada'),
                    'cancelled_by': self.env.user.id, 'cancelled_date': fields.Datetime.now()})
        return super().action_cancel()


class ReportPettyCashPeriod(models.AbstractModel):
    _name = 'report.cash_receipt_voucher.report_petty_cash_period'
    _description = 'Reporte de Caja Chica por periodo'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        Entry = self.env['petty.cash.entry']
        df = fields.Date.to_date(data.get('date_from')) if data.get('date_from') else None
        dt = fields.Date.to_date(data.get('date_to')) if data.get('date_to') else fields.Date.context_today(self)
        start, end = Entry._dt_range(df, dt)
        company = self.env.company
        dom = [('company_id', '=', company.id), ('date', '<=', end), ('state', '=', 'posted')]
        if start:
            dom.append(('date', '>=', start))
        moves = Entry.search(dom, order='date asc, id asc')
        initial = Entry._balance(company, until=start - timedelta(seconds=1)) if start else 0.0
        running = initial
        lines = []
        for m in moves:
            running += m.amount if m.entry_type == 'in' else -m.amount
            lines.append({'m': m, 'balance': running,
                          'date': som_format_date(fields.Datetime.context_timestamp(self, m.date), with_time=True)})
        by_cat = {}
        for m in moves.filtered(lambda r: r.entry_type == 'out'):
            key = m.category_id.name or 'Sin categoría'
            by_cat[key] = by_cat.get(key, 0.0) + m.amount
        cancelled = Entry.search([('company_id', '=', company.id), ('state', '=', 'cancelled'),
                                  ('cancelled_date', '<=', end)] + ([('cancelled_date', '>=', start)] if start else []))
        return {
            'doc_ids': docids, 'doc_model': 'petty.cash.entry', 'data': data,
            'company': company,
            'currency': self.env.ref('base.MXN', raise_if_not_found=False) or company.currency_id,
            'period_label': '%s – %s' % (som_format_date(df) if df else 'inicio', som_format_date(dt)),
            'initial': initial, 'lines': lines, 'final': running,
            'total_in': sum(m.amount for m in moves if m.entry_type == 'in'),
            'total_out': sum(m.amount for m in moves if m.entry_type == 'out'),
            'by_category': sorted(by_cat.items(), key=lambda kv: -kv[1]),
            'cancelled': cancelled,
            'printed': som_format_date(fields.Datetime.context_timestamp(self, fields.Datetime.now()), with_time=True),
        }
