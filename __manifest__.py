{
    'name': 'Comprobante de Pago en Efectivo',
    'version': '19.0.3.1.2',
    'category': 'Sales',
    'summary': 'Genera recibos de pago en efectivo desde órdenes de venta',
    'description': """
        Módulo para generar comprobantes/recibos de recepción de pago en efectivo
        directamente desde las órdenes de venta. Permite:
        - Generar recibos para uno o varios pedidos
        - Monto manual y selección de divisa
        - Firma/sello digital
        - Seguimiento de estado (Borrador → Entregado → Pagado)
        - Invitación a registrar pago formal
        - Vinculación con pagos registrados en el sistema
    """,
    'author': 'Alphaqueb Consulting',
    'website': 'https://alphaqueb.com',
    'license': 'LGPL-3',
    'depends': [
        'sale',
        'account',
        # Motor unificado de avisos de "pago recibido" (Clara aplica / Lourdes
        # factura). El recibo de efectivo lo reutiliza para no duplicar lógica.
        'sale_payment_proof',
    ],
    'data': [
        'security/cash_receipt_security.xml',
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'views/cash_receipt_views.xml',
        'views/cash_internal_control_views.xml',
        'views/cash_dashboard_views.xml',
        'views/sale_order_views.xml',
        'wizard/cash_receipt_wizard_views.xml',
        'reports/cash_receipt_report.xml',
        'reports/cash_receipt_report_template.xml',
        'reports/cash_internal_control_report.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'cash_receipt_voucher/static/src/css/cash_receipt.scss',
            'cash_receipt_voucher/static/src/scss/cash_dashboard.scss',
            'cash_receipt_voucher/static/src/js/cash_dashboard/cash_dashboard.js',
            'cash_receipt_voucher/static/src/xml/cash_dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
