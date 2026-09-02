# -*- coding: utf-8 -*-
"""La factura SIEMPRE representa la orden (regla del cliente, 2 sep 2026).

Flujo real: al registrar el primer pago se factura la orden completa. Si la
orden cambia después (líneas, cantidades, precios), la factura quedaba
desactualizada y el dinero del cliente ya no encontraba factura que
cubrir: quedaba "sin aplicar" y no comisionaba.

Como las facturas NO son fiscales, la alineación se hace con documentos de
diferencia, sin tocar nunca lo ya pagado:
* lo que falta por facturar → factura complementaria (publicada);
* lo facturado de más → nota de crédito (publicada), que Odoo aplica contra
  el saldo pendiente de la factura y, si ya estaba pagada, queda como saldo
  a favor del cliente.
La diferencia se calcula LÍNEA POR LÍNEA (importe sin IVA): cada línea de la
orden contra lo que ya tiene facturado (facturas menos notas de crédito).
La línea de ajuste se expresa como cantidad al precio vigente de la línea,
así la cantidad facturada nativa de Odoo sigue cuadrando.

Cuándo corre: al guardar cambios en una orden confirmada (una sola vez al
final de la transacción), al aplicar un pago desde la orden, con el botón
"Alinear facturas con la orden" y con un cron diario de respaldo. Si una
factura tiene líneas que no se pueden atribuir a la orden, NO se genera
nada: se avisa y se deja para revisión (jamás adivinar con dinero).
"""
import logging
from collections import defaultdict

from odoo import api, fields, models, Command, _

_logger = logging.getLogger(__name__)

SYNC_LINE_FIELDS = {'price_unit', 'product_uom_qty', 'discount', 'tax_id', 'tax_ids', 'product_id'}
SYNC_ORDER_FIELDS = {'order_line', 'pricelist_id', 'fiscal_position_id', 'partner_invoice_id'}


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    x_invoice_sync = fields.Boolean(
        string='Facturas alineadas con la orden', default=True, copy=False,
        help='Encendido: cada cambio en la orden confirmada genera automáticamente la factura '
             'complementaria o la nota de crédito por la diferencia, para que lo facturado sea '
             'siempre igual a la orden. Apagado: solo se alinea con el botón.')
    x_invoice_gap = fields.Monetary(
        string='Orden menos facturado', compute='_compute_x_invoice_gap',
        currency_field='currency_id',
        help='Positivo: falta facturar. Negativo: se facturó de más. Cero: alineado.')
    x_invoice_sync_note = fields.Char(
        string='Aviso de alineación', compute='_compute_x_invoice_gap',
        help='Motivo por el que no se puede alinear automáticamente.')

    # ------------------------------------------------------------------
    # Diferencia línea por línea
    # ------------------------------------------------------------------
    def _som_posted_customer_invoices(self):
        self.ensure_one()
        return self.sudo().invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type in ('out_invoice', 'out_refund'))

    def _som_invoice_deltas(self):
        """[(línea, diferencia_sin_iva)] en moneda de la orden, y un aviso si
        hay líneas facturadas que no se pueden atribuir (entonces NO se
        debe generar nada)."""
        self.ensure_one()
        so = self.sudo()
        cur = so.currency_id
        today = fields.Date.context_today(self)
        invoiced = defaultdict(float)
        for inv in so._som_posted_customer_invoices():
            sign = -1.0 if inv.move_type == 'out_refund' else 1.0
            for il in inv.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
                sls = il.sale_line_ids.filtered(lambda s: s.order_id == so)
                if not sls:
                    if il.sale_line_ids:
                        continue  # línea de OTRA orden (factura multi-orden): no es de esta
                    if inv.invoice_origin and inv.invoice_origin.strip() == so.name:
                        # Sin liga pero de esta orden: se atribuye por producto
                        # si es inequívoco; si no, se detiene la alineación.
                        cands = so.order_line.filtered(
                            lambda s: not s.display_type and s.product_id == il.product_id)
                        if len(cands) == 1:
                            sls = cands
                        else:
                            return [], _('La factura %s tiene la línea "%s" sin liga a una línea de '
                                         'la orden; revísala antes de alinear.') % (inv.name, il.name or il.product_id.display_name)
                    else:
                        continue
                amt = il.price_subtotal
                if inv.currency_id != cur:
                    amt = inv.currency_id._convert(amt, cur, so.company_id, inv.invoice_date or today)
                invoiced[sls[0].id] += amt * sign
        deltas = []
        for line in so.order_line.filtered(lambda l: not l.display_type):
            d = cur.round((line.price_subtotal or 0.0) - invoiced.get(line.id, 0.0))
            if not cur.is_zero(d):
                deltas.append((line, d))
        return deltas, ''

    @api.depends('order_line.price_subtotal', 'invoice_ids.state', 'invoice_ids.amount_untaxed')
    def _compute_x_invoice_gap(self):
        for so in self:
            gap, note = 0.0, ''
            if so.state == 'sale' and isinstance(so.id, int) and so._som_posted_customer_invoices():
                try:
                    deltas, note = so._som_invoice_deltas()
                    gap = sum(d for _l, d in deltas)
                except Exception as exc:  # noqa: BLE001
                    note = str(exc)[:200]
            so.x_invoice_gap = gap
            so.x_invoice_sync_note = note

    # ------------------------------------------------------------------
    # Documentos de diferencia
    # ------------------------------------------------------------------
    def _som_prepare_delta_line(self, line, amount):
        """Línea de ajuste: la diferencia (sin IVA, positiva) expresada como
        cantidad al precio vigente de la línea, con sus mismos impuestos."""
        vals = line._prepare_invoice_line()
        unit = (line.price_unit or 0.0) * (1.0 - (line.discount or 0.0) / 100.0)
        if unit:
            vals.update({'quantity': amount / unit, 'price_unit': line.price_unit,
                         'discount': line.discount or 0.0})
        else:
            vals.update({'quantity': 1.0, 'price_unit': amount, 'discount': 0.0})
        vals['name'] = '%s\n(Ajuste a la orden %s)' % (vals.get('name') or line.name or line.product_id.display_name, line.order_id.name)
        vals['sale_line_ids'] = [Command.link(line.id)]
        return vals

    def _som_sync_invoices(self, force=False):
        """Genera los documentos de diferencia de cada orden. Devuelve los
        asientos creados. Nunca toca facturas ni pagos existentes."""
        Move = self.env['account.move'].sudo()
        created = Move
        for so in self.sudo():
            if so.state != 'sale' or (not force and not so.x_invoice_sync):
                continue
            if not so._som_posted_customer_invoices():
                continue  # la primera factura la crea el flujo de pago (nativa)
            deltas, note = so._som_invoice_deltas()
            if note:
                so._som_sync_warn(note)
                continue
            if not deltas:
                continue
            docs = {'out_invoice': [(l, d) for l, d in deltas if d > 0],
                    'out_refund': [(l, -d) for l, d in deltas if d < 0]}
            for move_type, items in docs.items():
                if not items:
                    continue
                try:
                    with self.env.cr.savepoint():
                        vals = so._prepare_invoice()
                        vals.update({
                            'move_type': move_type,
                            'invoice_date': fields.Date.context_today(self),
                            'invoice_line_ids': [Command.create(so._som_prepare_delta_line(l, amt)) for l, amt in items],
                        })
                        move = Move.with_company(so.company_id).with_context(som_skip_invoice_sync=True).create(vals)
                        move.action_post()
                        created |= move
                        total = sum(amt for _l, amt in items)
                        so.message_post(
                            body=_('%(kind)s %(name)s publicada automáticamente por %(amount)s (sin IVA) para '
                                   'alinear lo facturado con la orden.') % {
                                'kind': 'Factura complementaria' if move_type == 'out_invoice' else 'Nota de crédito',
                                'name': move.name, 'amount': '%s %s' % (so.currency_id.symbol or '', '{:,.2f}'.format(total))},
                            message_type='notification')
                        _logger.info('[FACTURA=ORDEN] %s: %s %s por %.2f', so.name, move_type, move.name, total)
                except Exception as exc:  # noqa: BLE001 - jamás romper el guardado de la orden
                    _logger.exception('[FACTURA=ORDEN] %s: no se pudo generar %s', so.name, move_type)
                    so._som_sync_warn(_('No se pudo generar el documento de ajuste (%s): %s') % (move_type, str(exc)[:200]))
        return created

    def _som_sync_warn(self, note):
        self.ensure_one()
        try:
            self.sudo().message_post(body=_('⚠️ Facturas vs orden: %s') % note, message_type='notification')
        except Exception:  # noqa: BLE001
            pass
        _logger.warning('[FACTURA=ORDEN] %s: %s', self.name, note)

    def action_som_sync_invoices(self):
        """Botón: alinear ahora (aunque la alineación automática esté apagada)."""
        created = self._som_sync_invoices(force=True)
        self.invalidate_recordset(['x_invoice_gap', 'x_invoice_sync_note', 'invoice_ids'])
        if created:
            msg = _('%d documento(s) generados: %s') % (len(created), ', '.join(created.mapped('name')))
        elif self.x_invoice_sync_note:
            msg = self.x_invoice_sync_note
        else:
            msg = _('Lo facturado ya coincide con la orden.')
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _('Facturas vs orden'), 'message': msg,
                       'type': 'success' if created or not self.x_invoice_sync_note else 'warning', 'sticky': False,
                       'next': {'type': 'ir.actions.client', 'tag': 'reload'}},
        }

    # ------------------------------------------------------------------
    # Disparadores: una sola vez al final de la transacción
    # ------------------------------------------------------------------
    def _som_schedule_invoice_sync(self):
        if self.env.context.get('som_skip_invoice_sync'):
            return
        ids = {s.id for s in self if isinstance(s.id, int)}
        if not ids:
            return
        cr = self.env.cr
        data = cr.precommit.data.setdefault('som_invoice_sync', {'ids': set(), 'registered': False})
        data['ids'] |= ids
        if data['registered']:
            return
        data['registered'] = True
        env = self.env

        @cr.precommit.add
        def _som_run_invoice_sync():
            order_ids = list(data['ids'])
            data['ids'] = set()
            data['registered'] = False
            try:
                env['sale.order'].browse(order_ids).exists().sudo()._som_sync_invoices()
            except Exception:  # noqa: BLE001
                _logger.exception('[FACTURA=ORDEN] alineación diferida falló para %s', order_ids)

    def write(self, vals):
        res = super().write(vals)
        if SYNC_ORDER_FIELDS & set(vals):
            self.filtered(lambda s: s.state == 'sale')._som_schedule_invoice_sync()
        return res

    @api.model
    def _cron_som_invoice_sync(self):
        orders = self.sudo().search([('state', '=', 'sale'), ('x_invoice_sync', '=', True),
                                     ('invoice_ids.state', '=', 'posted')])
        created = orders._som_sync_invoices()
        if created:
            _logger.info('[FACTURA=ORDEN] cron: %d documento(s) de ajuste', len(created))
        return True


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def write(self, vals):
        res = super().write(vals)
        if SYNC_LINE_FIELDS & set(vals):
            self.mapped('order_id').filtered(lambda s: s.state == 'sale')._som_schedule_invoice_sync()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.mapped('order_id').filtered(lambda s: s.state == 'sale')._som_schedule_invoice_sync()
        return lines

    def unlink(self):
        orders = self.mapped('order_id').filtered(lambda s: s.state == 'sale')
        res = super().unlink()
        orders._som_schedule_invoice_sync()
        return res
