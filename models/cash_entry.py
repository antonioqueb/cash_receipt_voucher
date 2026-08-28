# -*- coding: utf-8 -*-
"""Entradas de Caja (control interno de efectivo, registro manual).

La caja es un libro INDEPENDIENTE: nada entra solo. Cada entrada se captura
a mano y puede (opcionalmente) referenciar pedidos de venta por folio y/o el
recibo de efectivo que la respalda. Los recibos siguen existiendo como
documento del cliente, pero ya NO alimentan el saldo por sí mismos.

Replica el doble control de los recibos: monto recibido (oficial) vs
depositado a cuenta; la diferencia es el efectivo que queda en caja.
Es una capa interna paralela: NO toca contabilidad ni pagos oficiales.
"""
from collections import OrderedDict
from datetime import timedelta, datetime, time

from dateutil.relativedelta import relativedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError, AccessError
from odoo.tools import float_compare, float_is_zero

from odoo.addons.cash_receipt_voucher.models.som_date_format import (
    MESES_ES, som_format_date,
)

CASH_INTERNAL_VIEW_GROUP = 'cash_receipt_voucher.group_cash_internal_control'
CASH_INTERNAL_EDIT_GROUP = 'cash_receipt_voucher.group_cash_internal_control_edit'


class CashEntry(models.Model):
    _name = 'cash.entry'
    _description = 'Entrada de Caja'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _rec_name = 'name'

    _name_company_uniq = models.Constraint(
        'unique(name, company_id)',
        'Ya existe una entrada de caja con ese folio en esta compañía.',
    )

    name = fields.Char(
        string='Folio', required=True, copy=False, readonly=True,
        default=lambda self: _('Nuevo'),
    )
    date = fields.Datetime(
        string='Fecha', required=True, default=fields.Datetime.now,
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Cliente / Contacto',
        help='Contacto relacionado, si aplica.',
    )
    received_from = fields.Char(
        string='Recibido de', tracking=True,
        help='Persona que entregó el efectivo (si no es un contacto del sistema).',
    )
    sale_order_ids = fields.Many2many(
        'sale.order',
        'cash_entry_sale_order_rel',
        'entry_id',
        'order_id',
        string='Pedidos Asociados',
        tracking=True,
        help='Pedidos de venta relacionados (búscalos por folio). Opcional.',
    )
    receipt_id = fields.Many2one(
        'cash.receipt', string='Recibo de Efectivo',
        tracking=True, copy=False,
        help='Recibo que respalda esta entrada, si existe. Opcional: la '
             'entrada puede capturarse sin recibo.',
    )
    concept = fields.Text(
        string='Concepto / Motivo interno',
        help='Origen del efectivo (cobro, anticipo, reposición, etc.).',
    )
    user_id = fields.Many2one(
        'res.users', string='Usuario responsable', required=True,
        default=lambda self: self.env.user, readonly=True,
        help='Quién registró la entrada de caja.',
    )
    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection([
        ('done', 'Registrada'),
        ('cancelled', 'Cancelada'),
    ], string='Estado', default='done', tracking=True)

    # ------------------------------------------------------------------
    # DOBLE CONTROL (misma estructura que el recibo)
    # ------------------------------------------------------------------
    amount = fields.Monetary(
        string='Monto Recibido', required=True, tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency', string='Divisa', required=True,
        default=lambda self: self.env.ref(
            'base.MXN', raise_if_not_found=False) or self.env.company.currency_id,
        tracking=True,
    )
    amount_internal = fields.Monetary(
        string='Depositado a Cuenta',
        currency_field='currency_id', copy=False, tracking=True,
        help='Monto que se registra/ingresa a la cuenta. Por defecto es igual '
             'al Monto Recibido. La diferencia es el efectivo que se queda '
             'físicamente en caja (caja chica).',
    )
    amount_internal_diff = fields.Monetary(
        string='Efectivo en Caja',
        compute='_compute_amount_internal_diff', store=True,
        currency_field='currency_id',
        help='Monto Recibido menos lo Depositado a Cuenta.',
    )
    has_internal_diff = fields.Boolean(
        string='Tiene Diferencia',
        compute='_compute_amount_internal_diff', store=True,
    )
    internal_diff_reason = fields.Char(
        string='Motivo del Ajuste Interno', copy=False, tracking=True,
    )
    internal_adjusted_by = fields.Many2one(
        'res.users', string='Ajuste Interno por', readonly=True, copy=False,
    )
    internal_adjusted_date = fields.Datetime(
        string='Fecha de Ajuste Interno', readonly=True, copy=False,
    )
    can_adjust_internal = fields.Boolean(
        compute='_compute_can_adjust_internal',
        help='Técnico: controla el solo-lectura de los campos internos.',
    )

    # Equivalente MXN con el tipo de cambio (DOF) de la fecha.
    mxn_currency_id = fields.Many2one(
        'res.currency', compute='_compute_ref_currencies',
    )
    is_usd = fields.Boolean(compute='_compute_is_usd', store=True)
    amount_mxn = fields.Monetary(
        string='Recibido (MXN)',
        compute='_compute_amounts_mxn', store=True,
        currency_field='mxn_currency_id',
    )
    amount_internal_mxn = fields.Monetary(
        string='A Cuenta (MXN)',
        compute='_compute_amounts_mxn', store=True,
        currency_field='mxn_currency_id',
    )

    # ------------------------------------------------------------------
    # COMPUTES
    # ------------------------------------------------------------------
    @api.depends('amount', 'amount_internal', 'currency_id')
    def _compute_amount_internal_diff(self):
        for rec in self:
            rounding = rec.currency_id.rounding or 0.01
            diff = (rec.amount or 0.0) - (rec.amount_internal or 0.0)
            rec.amount_internal_diff = diff
            rec.has_internal_diff = not float_is_zero(diff, precision_rounding=rounding)

    def _compute_ref_currencies(self):
        mxn = self.env.ref('base.MXN', raise_if_not_found=False)
        for rec in self:
            rec.mxn_currency_id = mxn

    @api.depends('currency_id')
    def _compute_is_usd(self):
        usd = self.env.ref('base.USD', raise_if_not_found=False)
        for rec in self:
            rec.is_usd = bool(usd and rec.currency_id and rec.currency_id == usd)

    @api.depends('amount', 'amount_internal', 'currency_id', 'date')
    def _compute_amounts_mxn(self):
        mxn = self.env.ref('base.MXN', raise_if_not_found=False)
        for rec in self:
            company = rec.company_id or self.env.company
            target = mxn or company.currency_id
            d = rec.date.date() if rec.date else fields.Date.context_today(rec)
            if rec.currency_id and target and rec.currency_id != target:
                rec.amount_mxn = rec.currency_id._convert(rec.amount or 0.0, target, company, d)
                rec.amount_internal_mxn = rec.currency_id._convert(rec.amount_internal or 0.0, target, company, d)
            else:
                rec.amount_mxn = rec.amount or 0.0
                rec.amount_internal_mxn = rec.amount_internal or 0.0

    # ------------------------------------------------------------------
    # DOBLE CONTROL: permisos y espejo (mismo criterio que cash.receipt)
    # ------------------------------------------------------------------
    def _can_adjust_internal(self):
        return self.env.user.has_group(CASH_INTERNAL_EDIT_GROUP)

    @api.depends_context('uid')
    def _compute_can_adjust_internal(self):
        can = self.env.user.has_group(CASH_INTERNAL_EDIT_GROUP)
        for rec in self:
            rec.can_adjust_internal = can

    @staticmethod
    def _amounts_differ(a, b, currency=None):
        rounding = (currency.rounding if currency else 0.0) or 0.01
        return float_compare(a or 0.0, b or 0.0, precision_rounding=rounding) != 0

    @api.onchange('amount')
    def _onchange_amount_mirror_internal(self):
        for rec in self:
            origin_amount = rec._origin.amount if rec._origin else 0.0
            if not rec.amount_internal or not rec._amounts_differ(
                    rec.amount_internal, origin_amount, rec.currency_id):
                rec.amount_internal = rec.amount

    @api.onchange('receipt_id')
    def _onchange_receipt_id(self):
        """Prellenar desde el recibo: la captura sigue siendo manual (el usuario
        revisa y guarda), pero no se transcribe dos veces."""
        for rec in self:
            r = rec.receipt_id
            if not r:
                continue
            rec.partner_id = r.partner_id
            rec.sale_order_ids = [(6, 0, r.sale_order_ids.ids)]
            rec.currency_id = r.currency_id
            rec.amount = r.amount
            rec.amount_internal = r.amount_internal
            if not rec.concept:
                rec.concept = r.notes or ''

    @api.onchange('sale_order_ids')
    def _onchange_sale_order_ids(self):
        for rec in self:
            if rec.sale_order_ids and not rec.partner_id:
                rec.partner_id = rec.sale_order_ids[0].partner_id
            if rec.sale_order_ids and not rec.amount:
                rec.amount = sum(rec.sale_order_ids.mapped('amount_total'))

    # ------------------------------------------------------------------
    # CONSTRAINS / CRUD
    # ------------------------------------------------------------------
    @api.constrains('amount')
    def _check_amount_positive(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_(
                    'El monto de la entrada de caja debe ser mayor a cero.'))

    @api.constrains('receipt_id', 'state')
    def _check_receipt_unique(self):
        """Un recibo respalda a lo más UNA entrada activa: dos entradas del
        mismo recibo duplicarían el efectivo en el saldo."""
        for rec in self:
            if not rec.receipt_id or rec.state == 'cancelled':
                continue
            dup = self.search([
                ('receipt_id', '=', rec.receipt_id.id),
                ('state', '!=', 'cancelled'),
                ('id', '!=', rec.id),
            ], limit=1)
            if dup:
                raise ValidationError(_(
                    'El recibo %(receipt)s ya está registrado en caja con la '
                    'entrada %(entry)s. Cancela esa entrada si necesitas '
                    'volver a capturarla.'
                ) % {'receipt': rec.receipt_id.name, 'entry': dup.name})

    @api.model_create_multi
    def create(self, vals_list):
        can_adjust = self._can_adjust_internal()
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'cash.entry') or _('Nuevo')
            if vals.get('amount_internal') in (None, False):
                vals['amount_internal'] = vals.get('amount', 0.0)
            elif not can_adjust:
                vals['amount_internal'] = vals.get('amount', 0.0)
                vals.pop('internal_diff_reason', None)
        records = super().create(vals_list)
        for rec in records:
            if can_adjust and rec._amounts_differ(rec.amount_internal, rec.amount, rec.currency_id):
                rec.internal_adjusted_by = self.env.user
                rec.internal_adjusted_date = fields.Datetime.now()
        return records

    def write(self, vals):
        adjusting = 'amount_internal' in vals or 'internal_diff_reason' in vals
        if adjusting and not self._can_adjust_internal():
            for rec in self:
                if 'amount_internal' in vals and rec._amounts_differ(
                        vals['amount_internal'], rec.amount_internal, rec.currency_id):
                    raise UserError(_(
                        'No tiene permisos para modificar el efectivo real '
                        '(control interno) de la entrada %(name)s.'
                    ) % {'name': rec.name})
                if 'internal_diff_reason' in vals and (vals.get('internal_diff_reason') or '') != (rec.internal_diff_reason or ''):
                    raise UserError(_(
                        'No tiene permisos para registrar el motivo del '
                        'ajuste interno.'))
        res = super().write(vals)
        if 'amount_internal' in vals and self._can_adjust_internal():
            stamp = {
                'internal_adjusted_by': self.env.user.id,
                'internal_adjusted_date': fields.Datetime.now(),
            }
            for rec in self:
                super(CashEntry, rec).write(stamp)
        return res

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def action_restore(self):
        self.write({'state': 'done'})
        return True

    # ==================================================================
    # DASHBOARD DE CONTROL INTERNO DE EFECTIVO (fuente: caja manual)
    # ==================================================================
    @api.model
    def _check_internal_access(self):
        if not self.env.user.has_group(CASH_INTERNAL_VIEW_GROUP):
            raise AccessError(_(
                'No tiene permisos para el Control Interno de Efectivo.'))

    @api.model
    def _resolve_period(self, period, date_from=False, date_to=False):
        """Devuelve (date_from, date_to) como objetos date según el periodo."""
        today = fields.Date.context_today(self)
        if period == 'custom' and date_from and date_to:
            return fields.Date.to_date(date_from), fields.Date.to_date(date_to)
        if period == 'today':
            return today, today
        if period == 'week':
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=6)
        if period == 'quarter':
            q_start_month = ((today.month - 1) // 3) * 3 + 1
            start = today.replace(month=q_start_month, day=1)
            return start, start + relativedelta(months=3, days=-1)
        if period == 'year':
            return today.replace(month=1, day=1), today.replace(month=12, day=31)
        # 'month' (por defecto)
        start = today.replace(day=1)
        return start, start + relativedelta(months=1, days=-1)

    @api.model
    def _period_domain(self, df, dt):
        domain = [('state', '!=', 'cancelled')]
        if df:
            domain.append(('date', '>=', fields.Datetime.to_string(
                datetime.combine(df, time.min))))
        if dt:
            domain.append(('date', '<=', fields.Datetime.to_string(
                datetime.combine(dt, time.max))))
        return domain

    @api.model
    def _resolve_currency_mode(self, currency_mode):
        usd = self.env.ref('base.USD', raise_if_not_found=False)
        mxn = self.env.ref('base.MXN', raise_if_not_found=False)
        company_cur = self.env.company.currency_id
        if currency_mode == 'usd' and usd:
            return 'usd', [('currency_id', '=', usd.id)], usd
        if currency_mode == 'mxn' and mxn:
            return 'mxn', [('currency_id', '=', mxn.id)], mxn
        return 'all_mxn', [], (mxn or company_cur)

    @api.model
    def get_dashboard_data(self, period='month', date_from=False, date_to=False, currency_mode='all_mxn'):
        """KPIs y series del dashboard de efectivo. La fuente son las ENTRADAS
        manuales de caja (no los recibos) menos las salidas."""
        self._check_internal_access()
        df, dt = self._resolve_period(period, date_from, date_to)
        currency_mode, cur_domain, disp_cur = self._resolve_currency_mode(currency_mode)
        company_cur = self.env.company.currency_id
        entries = self.search(self._period_domain(df, dt) + cur_domain, order='date asc')
        consolidated = currency_mode == 'all_mxn'

        disbursements = self.env['cash.disbursement'].search(
            self._period_domain(df, dt) + cur_domain)

        def _val(r):
            return r.amount_mxn if consolidated else r.amount
        def _valint(r):
            return r.amount_internal_mxn if consolidated else r.amount_internal
        def _en_caja(r):
            return _val(r) - _valint(r)

        def _val_out(o):
            return o.amount_mxn if consolidated else o.amount

        total_official = sum(_val(r) for r in entries)
        total_real = sum(_valint(r) for r in entries)
        total_diff = total_official - total_real
        total_out = sum(_val_out(o) for o in disbursements)
        # SALDO DE CAJA = ENTRADAS − SALIDAS. La caja es manual e
        # independiente: lo depositado a cuenta ya no participa del saldo.
        cash_on_hand = total_official - total_out
        with_diff = entries.filtered(lambda r: abs(_en_caja(r)) > 0.001)
        shortage = sum(_en_caja(r) for r in entries if _en_caja(r) > 0)
        overage = sum(-_en_caja(r) for r in entries if _en_caja(r) < 0)
        count = len(entries)

        # --- Serie temporal ---
        span_days = (dt - df).days if (df and dt) else 9999
        group = 'month' if span_days > 70 else 'day'
        keys = []
        if df and dt:
            if group == 'day':
                cur = df
                while cur <= dt:
                    keys.append((cur.strftime('%Y-%m-%d'),
                                 '%02d %s' % (cur.day, MESES_ES[cur.month - 1])))
                    cur += timedelta(days=1)
            else:
                cur = df.replace(day=1)
                while cur <= dt:
                    keys.append((cur.strftime('%Y-%m'),
                                 '%s %s' % (MESES_ES[cur.month - 1], str(cur.year)[2:])))
                    cur += relativedelta(months=1)
        buckets = OrderedDict((k, {'official': 0.0, 'real': 0.0, 'diff': 0.0, 'out': 0.0}) for k, _l in keys)
        labels = [l for _k, l in keys]
        for r in entries:
            if not r.date:
                continue
            local = fields.Datetime.context_timestamp(self, r.date)
            k = local.strftime('%Y-%m') if group == 'month' else local.strftime('%Y-%m-%d')
            b = buckets.get(k)
            if b is None:
                continue
            b['official'] += _val(r)
            b['real'] += _valint(r)
            b['diff'] += _en_caja(r)

        for o in disbursements:
            if not o.date:
                continue
            local = fields.Datetime.context_timestamp(self, o.date)
            k = local.strftime('%Y-%m') if group == 'month' else local.strftime('%Y-%m-%d')
            b = buckets.get(k)
            if b is not None:
                b['diff'] -= _val_out(o)
                b['out'] += _val_out(o)

        series = list(buckets.values())

        # --- Ranking por cliente (top 8 por efectivo RECIBIDO) ---
        # La clave se llama 'real' por compatibilidad con el front, pero
        # desde el rediseño de caja manual rankea por el monto recibido.
        by_partner = {}
        for r in entries:
            p = r.partner_id
            if not p:
                continue
            entry = by_partner.setdefault(p.id, {'name': p.display_name, 'real': 0.0, 'diff': 0.0})
            entry['real'] += _val(r)
            entry['diff'] += _en_caja(r)
        top_partners = sorted(by_partner.values(), key=lambda e: e['real'], reverse=True)[:8]
        retention_partners = sorted(
            [e for e in by_partner.values() if e['diff'] > 0],
            key=lambda e: e['diff'], reverse=True)[:6]

        # --- Entradas recientes (máx 12) ---
        recent = []
        for r in entries.sorted(key=lambda x: x.date or datetime.min, reverse=True)[:12]:
            recent.append({
                'id': r.id,
                'name': r.name,
                'date': som_format_date(
                    fields.Datetime.context_timestamp(self, r.date) if r.date else False,
                    empty='',
                ),
                'partner': r.partner_id.display_name or r.received_from or '',
                'orders': ', '.join(r.sale_order_ids.mapped('name')),
                'receipt': r.receipt_id.name or '',
                'official': _val(r),
                'real': _valint(r),
                'diff': _en_caja(r),
                'cur': r.currency_id.name or '',
                'state': r.state,
            })

        # --- Salidas recientes (máx 8) ---
        recent_out = []
        for o in disbursements.sorted(key=lambda x: x.date or datetime.min, reverse=True)[:8]:
            recent_out.append({
                'id': o.id,
                'name': o.name,
                'date': som_format_date(
                    fields.Datetime.context_timestamp(self, o.date) if o.date else False,
                    empty='',
                ),
                'delivered_to': o.delivered_to or '',
                'concept': (o.concept or '')[:60],
                'po': o.purchase_order_id.name or '',
                'user': o.user_id.name or '',
                'amount': _val_out(o),
                'cur': o.currency_id.name or '',
            })

        # --- Indicadores de dirección ---
        deposit_rate = (total_real / total_official * 100.0) if total_official else 0.0
        retention_rate = (total_diff / total_official * 100.0) if total_official else 0.0
        avg_retention = (total_diff / count) if count else 0.0
        avg_ticket = (total_official / count) if count else 0.0
        avg_out = (total_out / len(disbursements)) if disbursements else 0.0
        # La caja es MANUAL e independiente de pedidos y contabilidad: aquí no
        # se leen cuentas por cobrar ni saldos de clientes (decisión SOM).
        max_r = max(entries, key=lambda r: _val(r), default=None)
        max_receipt = {
            'name': max_r.name, 'value': _val(max_r),
            'partner': max_r.partner_id.display_name or max_r.received_from or '',
        } if max_r else {'name': '', 'value': 0.0, 'partner': ''}
        # Origen de las entradas: respaldadas por recibo vs captura suelta.
        linked = len(entries.filtered('receipt_id'))
        states = {'linked': linked, 'manual': count - linked}

        all_period = self.search(self._period_domain(df, dt))
        usd_count = len(all_period.filtered('is_usd'))
        mix = {'usd': usd_count, 'mxn': len(all_period) - usd_count}

        prev = {'official': 0.0, 'real': 0.0, 'diff': 0.0}
        if df and dt:
            length = (dt - df).days + 1
            prev_dt = df - timedelta(days=1)
            prev_df = prev_dt - timedelta(days=length - 1)
            prev_entries = self.search(self._period_domain(prev_df, prev_dt) + cur_domain)
            prev['official'] = sum(_val(r) for r in prev_entries)
            prev['real'] = sum(_valint(r) for r in prev_entries)
            prev['diff'] = prev['official'] - prev['real']

        def _delta(cur, pre):
            if pre:
                return (cur - pre) / pre * 100.0
            return 100.0 if cur else 0.0

        return {
            'currency': {'symbol': disp_cur.symbol or '$', 'position': disp_cur.position or 'before', 'label': disp_cur.name or ''},
            'currency_mode': currency_mode,
            'consolidated': consolidated,
            'mix': mix,
            'period': period,
            'date_from': df and fields.Date.to_string(df) or '',
            'date_to': dt and fields.Date.to_string(dt) or '',
            'kpis': {
                'total_official': total_official,
                'total_real': total_real,
                'total_diff': total_diff,
                'diff_pct': retention_rate,
                'deposit_rate': deposit_rate,
                'retention_rate': retention_rate,
                'avg_retention': avg_retention,
                'avg_out': avg_out,
                'avg_ticket': avg_ticket,
                'count': count,
                'partners_count': len(entries.mapped('partner_id')),
                'with_diff_count': len(with_diff),
                'shortage': shortage,
                'overage': overage,
                'total_out': total_out,
                'out_count': len(disbursements),
                'cash_on_hand': cash_on_hand,
            },
            'recent_out': recent_out,
            'max_receipt': max_receipt,
            'states': states,
            'prev': prev,
            'deltas': {
                'official': _delta(total_official, prev['official']),
                'real': _delta(total_real, prev['real']),
                'diff': _delta(total_diff, prev['diff']),
            },
            'series': series,
            'series_labels': labels,
            'series_group': group,
            'top_partners': top_partners,
            'retention_partners': retention_partners,
            'recent': recent,
        }

    @api.model
    def action_print_period_report(self, period='month', date_from=False, date_to=False, currency_mode='all_mxn'):
        """Acción de reporte PDF de las entradas del periodo y divisa."""
        self._check_internal_access()
        df, dt = self._resolve_period(period, date_from, date_to)
        currency_mode, cur_domain, disp_cur = self._resolve_currency_mode(currency_mode)
        entries = self.search(self._period_domain(df, dt) + cur_domain, order='date asc')
        if not entries:
            raise UserError(_('No hay entradas de caja en el periodo seleccionado para imprimir.'))
        return self.env.ref(
            'cash_receipt_voucher.action_report_cash_entry_control'
        ).report_action(entries.ids, data={
            'currency_mode': currency_mode,
            'docids': entries.ids,
        })


class ReportCashEntryControl(models.AbstractModel):
    """Parser del reporte de control de caja manual. Recupera las entradas y el
    modo de divisa desde 'data' (los docids no viajan en la URL al pasar data)."""
    _name = 'report.cash_receipt_voucher.report_cash_entry_control'
    _description = 'Reporte de Control de Caja (Entradas Manuales)'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        ids = docids or data.get('docids') or self.env.context.get('active_ids') or []
        docs = self.env['cash.entry'].browse(ids)
        return {
            'doc_ids': docs.ids,
            'doc_model': 'cash.entry',
            'docs': docs,
            'data': data,
            'currency_mode': data.get('currency_mode') or 'all_mxn',
        }
