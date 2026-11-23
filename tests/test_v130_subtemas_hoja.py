"""v1.30: subtemas ya publicados en la hoja de notas analizadas (TÍTULO,
RESUMEN, TEMA): la MISMA noticia del dossier retoma el TEMA."""
import os, sys, types
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
import memoria_cliente as mc
import analyzer_tono_tema as az

KM = {'titulo': 'Título', 'resumen': 'Resumen - Aclaracion'}
HOY = datetime.now().strftime('%d/%m/%Y')
CAB = ['ID', 'FECHA', 'MEDIO', 'TÍTULO', 'RESUMEN', 'TEMA', 'TONO']
RES_ALERTA = ('La Defensoría del Pueblo emitió una alerta temprana por el riesgo de reclutamiento '
              'forzado de menores en cinco municipios del Cauca, donde grupos armados se disputan '
              'el control territorial y las rutas del narcotráfico.')


def _fila(i, tit, res, tema, tono='Neutro', fecha=HOY):
    return [str(i), fecha, 'El Tiempo', tit, res, tema, tono]


def test_misma_noticia_toma_el_tema_de_la_hoja():
    refs = mc.referencias_de_tabla(CAB, [
        _fila(1, 'Defensoría alerta por reclutamiento de menores en el Cauca', RES_ALERTA,
              'Reclutamiento de menores en el Cauca')])
    rows = [
        # Mismo titular en otro medio.
        {'Título': 'Defensoría alerta por reclutamiento de menores en el Cauca',
         'Subtema_IA': 'Alertas Defensoría del Pueblo', 'Tono_IA': 'Positivo'},
        # Otro titular, mismo texto (RESUMEN).
        {'Título': 'Grupos armados reclutan niños, advierte la Defensoría',
         'Resumen - Aclaracion': RES_ALERTA, 'Subtema_IA': 'Riesgo para niños', 'Tono_IA': 'Positivo'},
        # Otra noticia: conserva su subtema.
        {'Título': 'Defensoría pide proteger a líderes sociales en el Catatumbo',
         'Resumen - Aclaracion': 'La Defensoría del Pueblo pidió medidas para proteger a los líderes sociales.',
         'Subtema_IA': 'Protección de líderes sociales', 'Tono_IA': 'Positivo'},
    ]
    n = az.anclar_subtemas_hoja(rows, KM, refs, mapa={0: 1, 1: 2, 2: 3})
    assert n == 2
    assert rows[0]['Subtema_IA'] == rows[1]['Subtema_IA'] == 'Reclutamiento de menores en el Cauca'
    assert rows[2]['Subtema_IA'] == 'Protección de líderes sociales'
    # El tono no se toca.
    assert [r['Tono_IA'] for r in rows] == ['Positivo'] * 3


def test_sin_contagio_por_subtema_automatico():
    refs = mc.referencias_de_tabla(CAB, [
        _fila(1, 'Defensoría alerta por reclutamiento de menores en el Cauca', '', 'Reclutamiento en el Cauca')])
    rows = [{'Título': 'Defensoría alerta por reclutamiento de menores en el Cauca', 'Subtema_IA': 'Alertas Defensoría del Pueblo'},
            {'Título': 'Alerta temprana por minería ilegal en el Chocó', 'Subtema_IA': 'Alertas Defensoría del Pueblo'}]
    # Distintas noticias (grupos 1 y 2) aunque compartan el subtema automático.
    assert az.anclar_subtemas_hoja(rows, KM, refs, mapa={0: 1, 1: 2}) == 1
    assert rows[1]['Subtema_IA'] == 'Alertas Defensoría del Pueblo'
    # Misma noticia en la corrida (mismo grupo): también la otra fila.
    rows[0]['Subtema_IA'] = rows[1]['Subtema_IA'] = 'x'
    assert az.anclar_subtemas_hoja(rows, KM, refs, mapa={0: 1, 1: 1}) == 2


def test_precision_titulos_genericos_cifras_y_descartes():
    hace_un_mes = (datetime.now() - timedelta(days=40)).strftime('%d/%m/%Y')
    refs = mc.referencias_de_tabla(CAB, [
        _fila(1, 'Mención Defensoría del Pueblo', '', 'Crisis carcelaria'),           # genérico
        _fila(2, 'Ataque en Jamundí deja 2 muertos y varios heridos', '', 'Ataque en Jamundí'),
        _fila(3, 'Nota descartada por el analista hoy', '', 'No aplica'),
        _fila(4, 'Nota duplicada del dossier de la tarde', '', 'x', tono='Duplicada'),
        _fila(5, 'Defensoría visita la cárcel La Modelo de Bogotá', '', 'Visita a La Modelo',
              fecha=hace_un_mes),                                                     # vieja
    ])
    assert {r['subtema'] for r in refs} == {'Crisis carcelaria', 'Ataque en Jamundí'}
    rows = [{'Título': 'Mención Defensoría del Pueblo', 'Subtema_IA': 'Defensa de migrantes'},
            {'Título': 'Ataque en Jamundí deja 5 muertos y varios heridos', 'Subtema_IA': 'Nuevo ataque en Jamundí'}]
    assert az.anclar_subtemas_hoja(rows, KM, refs, mapa={0: 1, 1: 2}) == 0


def test_mismo_titulo_con_dos_temas_el_mismo_dia_es_ambiguo():
    refs = mc.referencias_de_tabla(CAB, [
        _fila(1, 'Pronunciamiento de la Defensoría sobre derechos humanos', '', 'Crisis en el Catatumbo'),
        _fila(2, 'Pronunciamiento de la Defensoría sobre derechos humanos', '', 'Migrantes en Necoclí')])
    assert refs == []


def test_titular_en_mayusculas_en_la_hoja():
    refs = mc.referencias_de_tabla(['TÍTULO', 'TEMA'], [
        ['DEFENSORÍA ALERTA POR RECLUTAMIENTO DE MENORES EN EL CAUCA', 'Reclutamiento en el Cauca']])
    assert mc.buscar_referencia(refs, 'Defensoría alerta por reclutamiento de menores en el Cauca')


def test_lectura_de_la_hoja_csv(tmp_path):
    import pipeline
    p = tmp_path / 'hoja.csv'
    p.write_text('ID,FECHA,TÍTULO,RESUMEN,TEMA\n'
                 '61142010,%s,Defensoría alerta por reclutamiento de menores en el Cauca,%s,Reclutamiento en el Cauca\n'
                 '61142011.0,%s,Otra nota,,No aplica\n' % (HOY, RES_ALERTA.replace(',', ''), HOY),
                 encoding='utf-8')
    ids, refs = pipeline.cargar_hoja_analizadas(str(p))
    assert ids == {'61142010', '61142011'}
    assert [r['subtema'] for r in refs] == ['Reclutamiento en el Cauca']
    ids2, refs2 = pipeline.cargar_hoja_analizadas(str(p), con_referencias=False)
    assert ids2 == ids and refs2 == []


def test_fecha_informe_manda_sobre_fecha():
    # La hoja trae FECHA (de la nota) y FECHA INFORME (del boletín): manda
    # FECHA INFORME. Una nota vieja reportada hoy sí cuenta; una nota de hoy
    # reportada hace un mes no.
    viejo = (datetime.now() - timedelta(days=40)).strftime('%d/%m/%Y')
    cab = ['ID', 'FECHA', 'TÍTULO', 'RESUMEN', 'TEMA', 'FECHA INFORME']
    refs = mc.referencias_de_tabla(cab, [
        ['1', viejo, 'Defensoría alerta por reclutamiento de menores en el Cauca', '', 'Reclutamiento', HOY],
        ['2', HOY, 'Defensoría visita la cárcel La Modelo de Bogotá', '', 'Visita a La Modelo', viejo]])
    assert [r['subtema'] for r in refs] == ['Reclutamiento']
