{
    'name': 'Comprobante de Pago en Efectivo',
    'version': '19.0.1.0.0',
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
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'views/cash_receipt_views.xml',
        'views/sale_order_views.xml',
        'wizard/cash_receipt_wizard_views.xml',
        'reports/cash_receipt_report.xml',
        'reports/cash_receipt_report_template.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'cash_receipt_voucher/static/src/css/cash_receipt.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
