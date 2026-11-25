"""v4.89: pérdidas de v4.74 restauradas y tema por subtema con PKL.

- `fusionar_grupos_por_noticia` (Secretaría v1.47): la misma noticia
  (CuerpoEs igual o muy parecido) en un solo grupo ANTES de clasificar.
- `agrupar_noticias_filas`: el modo solo PKL agrupa con la misma evidencia
  del flujo con IA.
- `limpiar_caches_texto` al inicio de cada corrida.
- Regla del usuario: con PKL de tema, el mismo subtema lleva el mismo tema.
- Mismo molde en otra ciudad: el rótulo de un titular en MAYÚSCULAS
  recupera la mayúscula del lugar y ningún pase de rótulos une lugares
  distintos. Banco sin fusiones ni noticias partidas.
"""
import os
import sys
import types
from collections import defaultdict

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RAIZ)
sys.path.insert(0, os.path.join(_RAIZ, 'tools'))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
import analyzer_tono_tema as az  # noqa: E402
import perfil_cliente  # noqa: E402

P = perfil_cliente.cargar_perfil('secretaria_general_de_la_alcaldia_de_bogota')
B, A, V = P['brand'], P['aliases'], P['voceros']
KM = {'titulo': 'Título'}

ALERTA = ('La Alcaldía de Bogotá, a través de la Secretaría Distrital de Ambiente, declaró la Alerta '
          'Fase 1 por calidad del aire en las UPL de Kennedy, Arborizadora, Puente Aranda y Patio '
          'Bonito, en el suroccidente de la ciudad. La decisión se tomó porque durante 36 horas '
          'consecutivas las estaciones de monitoreo de Carvajal-Sevillana, Ciudad Bolívar, Puente '
          'Aranda y Kennedy registraron una calidad del aire regular por el aumento de partículas '
          'PM10. La Secretaría reforzará los controles a las industrias y hará operativos en vía.')
ALERTA_2 = ('Las estaciones de monitoreo de Carvajal-Sevillana, Ciudad Bolívar, Puente Aranda y '
            'Kennedy registraron durante 36 horas consecutivas una calidad del aire regular por '
            'partículas PM10, por lo que la Secretaría Distrital de Ambiente declaró la Alerta Fase 1 '
            'en Kennedy, Arborizadora, Puente Aranda y Patio Bonito. Se reforzarán los controles a '
            'las industrias del suroccidente y habrá operativos en vía a los vehículos.')
METRO = ('La Empresa Metro de Bogotá informó que el viaducto de la primera línea ya suma 20 '
         'kilómetros y 12 kilómetros de rieles instalados, con un avance general del 84 por ciento '
         'en las obras entre Bosa y la calle 72.')


def _fila(tit, cuerpo, medio):
    return {'Título': tit, 'CuerpoEs': cuerpo, 'Medio': medio, 'Contexto analizado': cuerpo[:300]}


def test_cuerpo_misma_noticia_reescrita_queda_en_un_grupo():
    rows = [_fila('Bogotá declara Alerta Fase 1 por calidad del aire en el suroccidente', ALERTA, 'A'),
            _fila('Alerta por aire en Kennedy y Puente Aranda: empresas paran equipos', ALERTA_2, 'B'),
            _fila('Metro de Bogotá llega a 20 kilómetros de viaducto', METRO, 'C')]
    grupos, mapa = az.construir_grupos(rows, KM, estricta=True,
                                       excluir_similitud=az.tokens_marca(B, A, V))
    assert mapa[0] != mapa[1]                       # la agrupación estricta las separaba
    grupos, mapa, n = az.fusionar_grupos_por_noticia(rows, grupos, mapa, KM, B, A, V)
    assert n == 1 and mapa[0] == mapa[1] != mapa[2]
    g = next(g for g in grupos if g['grupo'] == mapa[0])
    assert g['n'] == 2 and sorted(g['idxs']) == [0, 1]
    assert len(g['titulos_alt']) == 1
    assert sorted(mapa) == [0, 1, 2] and len(grupos) == 2


def test_cuerpo_noticias_distintas_con_el_mismo_pie_no_se_unen():
    pie = (' Pulzo.com se escribe con Z. El artículo fue curado por el equipo de Portal Bogotá y '
           'revisado por la mesa de edición antes de su publicación en la sección local.')
    rows = [_fila('Bogotá declara Alerta Fase 1 por calidad del aire en el suroccidente',
                  ALERTA + pie, 'Pulzo'),
            _fila('Metro de Bogotá llega a 20 kilómetros de viaducto', METRO + pie, 'Pulzo')]
    grupos, mapa = az.construir_grupos(rows, KM, estricta=True)
    grupos, mapa, n = az.fusionar_grupos_por_noticia(rows, grupos, mapa, KM, B, A, V)
    assert n == 0 and mapa[0] != mapa[1]


def test_cuerpo_sin_grupos_que_fusionar():
    rows = [_fila('Metro de Bogotá llega a 20 kilómetros de viaducto', METRO, 'C')]
    grupos, mapa = az.construir_grupos(rows, KM, estricta=True)
    g2, m2, n = az.fusionar_grupos_por_noticia(rows, grupos, mapa, KM, B, A, V)
    assert n == 0 and m2 == mapa



def test_agrupar_noticias_filas_modo_pkl():
    import banco_agrupacion as banco
    rows, verdad = banco.generar_dossier(3)
    cm = az.agrupar_noticias_filas(rows, {'titulo': 'Título'}, brand=banco.MARCA, aliases=banco.ALIAS)
    from collections import defaultdict
    h2c, c2h = defaultdict(set), defaultdict(set)
    for i, c in cm.items():
        h2c[verdad[i]].add(c)
        c2h[c].add(verdad[i])
    assert all(len(s) == 1 for s in h2c.values())
    assert all(len(s) == 1 for s in c2h.values())


def test_pkl_usa_el_agrupamiento_por_evidencia():
    import pkl_classifier
    src = open(pkl_classifier.__file__, encoding='utf-8').read()
    assert 'agrupar_noticias_filas' in src


def test_limpiar_caches_texto():
    az.nz('texto para la caché de prueba')
    az.words('texto para la caché de prueba')
    az.limpiar_caches_texto()
    assert az._nz_corto.cache_info().currsize == 0
    assert az._words_corto.cache_info().currsize == 0
    assert az.nz('Ávila  Ñ!') == 'avila n'


def test_mismo_subtema_mismo_tema_con_pkl():
    rows = [{'Subtema_IA': 'Programa de alimentación escolar en Soledad', 'Tema_IA': 'Educación'},
            {'Subtema_IA': 'Programa de alimentación escolar en Soledad', 'Tema_IA': 'Educación'},
            {'Subtema_IA': 'Programa de alimentación escolar en Soledad', 'Tema_IA': 'Salud'},
            {'Subtema_IA': 'Jornada de vacunación en Soledad', 'Tema_IA': 'Salud'},
            {'Subtema_IA': '-', 'Tema_IA': '-', 'is_duplicate': True}]
    assert az.un_tema_por_subtema_filas(rows) == 1
    assert {r['Tema_IA'] for r in rows[:3]} == {'Educación'}
    assert rows[3]['Tema_IA'] == 'Salud'
    assert rows[4]['Tema_IA'] == '-'


def test_empate_gana_el_de_la_primera_fila():
    rows = [{'Subtema_IA': 'Obras del metro', 'Tema_IA': 'Movilidad'},
            {'Subtema_IA': 'Obras del metro', 'Tema_IA': 'Infraestructura'}]
    az.un_tema_por_subtema_filas(rows)
    assert {r['Tema_IA'] for r in rows} == {'Movilidad'}


def test_titular_en_mayusculas_conserva_el_lugar():
    r = az._subtema_especifico(
        'FUNDACIÓN SANTA FE ABRE CONVOCATORIA DE BECAS DE ENFERMERÍA EN CHÍA',
        ['La Fundación Santa Fe abrió becas en Chía. En Chía habrá 40 cupos.'])
    assert r == 'Convocatoria de becas de enfermería en Chía'
    r = az._subtema_especifico(
        'FUNDACIÓN SANTA FE ABRE CONVOCATORIA DE BECAS DE ENFERMERÍA EN CARTAGENA', [])
    assert r.endswith('en Cartagena')


def test_lugar_distinto_no_es_el_mismo_hecho():
    assert not az._subtemas_mismo_hecho('Becas de enfermería en Medellín',
                                        'Becas para estudiantes de enfermería')
    assert not az._subtemas_mismo_hecho('Becas de enfermería en Medellín',
                                        'Becas de enfermería en Cartagena')
    assert az._subtemas_mismo_hecho('Fallecimiento de Navarro Wolff',
                                    'Fallecimiento de Antonio Navarro Wolff')
    assert az._subtemas_mismo_hecho('Inauguración del laboratorio en Cali',
                                    'Inauguración de laboratorio en Cali')


def test_banco_sin_fusiones_ni_partidas_10_semillas():
    import banco_agrupacion as banco
    for estricta in (False, True):
        for semilla in range(4, 11):
            r = banco.correr(semilla, estricta=estricta)
            assert r['sub']['partidas'] == 0, (estricta, semilla, r)
            assert r['sub']['fusiones'] == 0, (estricta, semilla, r)
            assert r['tono']['partidas'] == 0, (estricta, semilla, r)
