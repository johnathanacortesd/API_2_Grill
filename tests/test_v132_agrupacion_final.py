"""v1.32: casos del resultado real del 2026-11-21 (dossier de las 4:00 p.m.)."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
import analyzer_tono_tema as az
import motor_haiku as mh
import perfil_cliente

P = perfil_cliente.cargar_perfil('defensoria_del_pueblo')
B, A = P['brand'], P['aliases']
KM = {'titulo': 'Título', 'resumen': 'Resumen - Aclaracion'}
FIJOS = [f['subtema'] for f in P['subtemas_fijos']]


def _f(tit, sub, ctx='', res=''):
    return {'Título': tit, 'Subtema_IA': sub, 'Contexto analizado': ctx,
            'Resumen - Aclaracion': res, 'Tono_IA': 'Positivo'}


CTX_EVAC = ('La operación comenzó luego de una alerta de la Defensoría del Pueblo. El Ejército evacuó '
            'a seis civiles en riesgo y puso a salvo a cuatro menores reclutados en el Catatumbo, con '
            'la Defensoría, Migración Colombia y el ICBF.')


def test_variantes_catatumbo_se_unen():
    rows = [_f('Ejército evacúa a seis civiles y protege a cuatro menores en el Catatumbo',
               'Evacuación de civiles y menores en Catatumbo', CTX_EVAC) for _ in range(3)]
    rows.append(_f('Ejército evacuó a 14 personas en el Catatumbo; cuatro menores habrían sido '
                   'reclutados por la Estructura 33', 'Evacuación de civiles en Catatumbo',
                   'La operación contó con el acompañamiento de la Defensoría del Pueblo. Fueron '
                   'evacuados civiles y menores en el Catatumbo.'))
    assert az.unificar_variantes_final_filas(rows, KM, B, A, fijos=FIJOS) == 1
    assert {r['Subtema_IA'] for r in rows} == {'Evacuación de civiles y menores en Catatumbo'}


def test_variantes_de_hechos_distintos_no_se_unen():
    rows = [_f('Ataque con drones en Tibú deja un muerto', 'Ataque con drones en Tibú'),
            _f('Riesgo para comunidades de Sardinata por drones', 'Riesgo a población civil en Sardinata'),
            _f('Evacuación de civiles en el Catatumbo', 'Evacuación de civiles y menores en Catatumbo', CTX_EVAC)]
    assert az.unificar_variantes_final_filas(rows, KM, B, A, fijos=FIJOS) == 0


def test_alerta_temprana_en_texto_o_rotulo_va_al_fijo():
    rows = [
        # La variante que dejaron los pases finales («Alerta temprana sobre…»).
        _f('Disidencias aprietan a comunidades de Tolima: advierten riesgo en tres municipios',
           'Alerta temprana sobre Rovira, San Antonio y Roncesvalles',
           'La alerta comprende a Rovira, San Antonio y Roncesvalles.'),
        # Misma noticia (mismo titular) sin la frase: también.
        _f('Disidencias aprietan a comunidades de Tolima: advierten riesgo en tres municipios',
           'Riesgo en tres municipios del Tolima', 'La Defensoría advirtió riesgos.'),
        # La frase en el Resumen.
        _f('¿Por qué mataron a la lideresa Alcira Quintana?', 'Homicidio de lideresa Alcira Quintana',
           res='La Defensoría del Pueblo había emitido la Alerta Temprana 034/23 sobre Cantagallo.'),
        # Sin la frase: no se toca.
        _f('Tribunal ordena garantizar alimentación a detenidos en Cartagena',
           'Tutela por alimentación de detenidos en Cartagena', 'La Defensoría presentó la tutela.'),
    ]
    assert az.garantizar_subtemas_fijos_filas(rows, KM, P['subtemas_fijos']) == 3
    assert [r['Subtema_IA'] for r in rows] == ['Alertas Defensoría del Pueblo'] * 3 + [
        'Tutela por alimentación de detenidos en Cartagena']


def test_rotulo_de_entidad_se_rechaza():
    T = mh.terminos_cliente(B, A, P['voceros'])
    for s in ['Datos Defensoría del Pueblo', 'Gestión de la Defensoría',
              'Declaraciones de Iris Marín', 'Pronunciamiento de la Defensoría del Pueblo']:
        assert mh.es_rotulo_de_entidad(s, T), s
    for s in ['Alertas Defensoría del Pueblo', 'Falta de medicamentos en hospital de Arauca',
              'Balance de Colombia', 'Informe sobre líderes sociales asesinados']:
        assert not mh.es_rotulo_de_entidad(s, T), s
    mh._ESTADO['terminos'] = T
    try:
        pr = mh.problemas_subtema('Datos Defensoría del Pueblo', 'Arauca: Alertan por falta de tratamientos')
        assert pr and 'entidad' in pr[0]
        assert not any('entidad' in x for x in mh.problemas_subtema(
            'Falta de medicamentos en hospital de Arauca', 'Arauca: Alertan por falta de tratamientos'))
    finally:
        mh._ESTADO.pop('terminos', None)
