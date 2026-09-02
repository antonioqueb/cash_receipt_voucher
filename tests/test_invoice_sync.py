# -*- coding: utf-8 -*-
"""La factura siempre representa la orden: diferencias línea por línea."""
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install', 'invoice_sync')
class TestInvoiceSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_notrack=True,
                                       mail_create_nolog=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.tax16 = cls.env['account.tax'].create({
            'name': 'IVA 16 (prueba sync)', 'amount': 16.0, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'company_id': cls.company.id,
        })
        cls.goods = cls.env['product.product'].create({
            'name': 'Placa (prueba sync)', 'type': 'consu', 'list_price': 1000.0,
            'taxes_id': [(6, 0, cls.tax16.ids)],
        })
        cls.service = cls.env['product.product'].create({
            'name': 'Flete (prueba sync)', 'type': 'service', 'list_price': 100.0,
            'taxes_id': [(6, 0, cls.tax16.ids)],
        })
        cls.customer = cls.env['res.partner'].create({'name': 'Cliente sync (prueba)'})
        # Vendedor real: el superusuario no puede ser vendedor de una orden
        # (candado del módulo de comisiones).
        cls.seller_user = cls.env['res.users'].create({
            'name': 'Vendedor sync (prueba)', 'login': 'vendedor_sync_prueba',
            'group_ids': [(6, 0, [cls.env.ref('sales_team.group_sale_salesman').id,
                                  cls.env.ref('base.group_user').id])],
        })

    def _order(self, goods=100000.0):
        so = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'user_id': self.seller_user.id,
            'order_line': [(0, 0, {'product_id': self.goods.id, 'product_uom_qty': 10, 'price_unit': goods / 10.0,
                                   'tax_ids': [(6, 0, self.tax16.ids)]})],
        })
        so.action_confirm()
        inv = so._create_invoices(final=True)
        inv.invoice_date = fields.Date.today()
        inv.action_post()
        return so, inv

    def _invoiced(self, so):
        total = 0.0
        for inv in so._som_posted_customer_invoices():
            total += inv.amount_untaxed * (-1 if inv.move_type == 'out_refund' else 1)
        return total

    def test_01_price_change_creates_complementary_invoice(self):
        so, inv = self._order(100000.0)
        self.assertAlmostEqual(so.x_invoice_gap, 0.0, 2)
        # cambio SIN disparo automático, para probar el motor a solas
        so.order_line.with_context(som_skip_invoice_sync=True).write({'price_unit': 12000.0})  # sube a 120,000
        created = so._som_sync_invoices()
        self.assertEqual(len(created), 1)
        self.assertEqual(created.move_type, 'out_invoice')
        self.assertEqual(created.state, 'posted')
        self.assertAlmostEqual(created.amount_untaxed, 20000.0, delta=0.05)
        self.assertAlmostEqual(created.amount_total, 23200.0, delta=0.06, msg='lleva los mismos impuestos')
        self.assertEqual(created.invoice_line_ids.sale_line_ids, so.order_line, 'ligada a la línea de la orden')
        self.assertAlmostEqual(self._invoiced(so), 120000.0, delta=0.05)
        self.assertAlmostEqual(so.x_invoice_gap, 0.0, 2)
        # idempotente: nada más que generar
        self.assertFalse(so._som_sync_invoices())

    def test_02_new_line_and_qty_reduction(self):
        so, inv = self._order(100000.0)
        # nueva línea de servicio y la placa baja a 8 piezas
        so.with_context(som_skip_invoice_sync=True).write({'order_line': [
            (0, 0, {'product_id': self.service.id, 'product_uom_qty': 1, 'price_unit': 5000.0,
                    'tax_ids': [(6, 0, self.tax16.ids)]}),
            (1, so.order_line[0].id, {'product_uom_qty': 8}),
        ]})
        created = so._som_sync_invoices()
        self.assertEqual(len(created), 2, 'una complementaria (flete) y una nota de crédito (2 placas)')
        inv_c = created.filtered(lambda m: m.move_type == 'out_invoice')
        ref = created.filtered(lambda m: m.move_type == 'out_refund')
        self.assertAlmostEqual(inv_c.amount_untaxed, 5000.0, 2)
        self.assertAlmostEqual(ref.amount_untaxed, 20000.0, 2)
        self.assertAlmostEqual(ref.invoice_line_ids.quantity, 2.0, 3, 'la nota es por 2 piezas al precio vigente')
        self.assertAlmostEqual(self._invoiced(so), 85000.0, 2)
        self.assertAlmostEqual(so.amount_untaxed, 85000.0, 2)
        self.assertAlmostEqual(so.x_invoice_gap, 0.0, 2)

    def test_03_switch_off_and_button(self):
        so, inv = self._order(50000.0)
        so.x_invoice_sync = False
        so.order_line.price_unit = 6000.0
        self.assertFalse(so._som_sync_invoices(), 'apagado: no genera nada solo')
        self.assertAlmostEqual(so.x_invoice_gap, 10000.0, 2)
        created = so._som_sync_invoices(force=True)
        self.assertEqual(len(created), 1)
        self.assertAlmostEqual(so.x_invoice_gap, 0.0, 2)

    def test_05_auto_sync_on_save(self):
        """Guardar un cambio en la orden confirmada alinea solo, una sola vez."""
        so, inv = self._order(100000.0)
        so.order_line.price_unit = 12000.0
        docs = so._som_posted_customer_invoices()
        self.assertEqual(len(docs), 2, 'la original y UNA complementaria (sin duplicados)')
        self.assertAlmostEqual(self._invoiced(so), 120000.0, delta=0.05)
        self.assertAlmostEqual(so.x_invoice_gap, 0.0, 2)
        # otro guardado sin cambios reales no genera nada
        so.write({'order_line': [(1, so.order_line.id, {'price_unit': 12000.0})]})
        self.assertEqual(len(so._som_posted_customer_invoices()), 2)

    def test_04_unlinked_invoice_line_stops_sync(self):
        so, inv = self._order(50000.0)
        # una línea capturada a mano en la factura (sin liga a la orden), con un
        # producto que la orden NO tiene: no se adivina, se avisa.
        other = self.env['product.product'].create({'name': 'Otro (prueba sync)', 'type': 'consu', 'list_price': 1.0})
        inv.button_draft()
        inv.write({'invoice_line_ids': [(0, 0, {'product_id': other.id, 'quantity': 1, 'price_unit': 999.0,
                                                'tax_ids': [(6, 0, self.tax16.ids)]})]})
        inv.action_post()
        so.order_line.price_unit = 6000.0
        deltas, note = so._som_invoice_deltas()
        self.assertTrue(note)
        self.assertFalse(so._som_sync_invoices(force=True))
