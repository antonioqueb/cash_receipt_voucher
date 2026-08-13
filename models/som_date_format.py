# -*- coding: utf-8 -*-
"""Formato de fecha único del sistema: "13 ago 2026".

Solo para ETIQUETAS que ve el usuario. Cualquier fecha que viaje como dato
(clave de agrupación, valor a ordenar, dominio, payload al backend, valor de
un <input type="date">) se queda en ISO: este formato NO se puede volver a
convertir a fecha.
"""

MESES_ES = ('ene', 'feb', 'mar', 'abr', 'may', 'jun',
            'jul', 'ago', 'sep', 'oct', 'nov', 'dic')


def som_format_date(value, empty='—', with_time=False):
    if not value:
        return empty
    try:
        out = '%02d %s %d' % (
            value.day, MESES_ES[value.month - 1], value.year)
    except (AttributeError, IndexError, TypeError):
        return empty
    if with_time and hasattr(value, 'hour'):
        out += ' %02d:%02d' % (value.hour, value.minute)
    return out
