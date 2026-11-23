"""Portado de Secretaría v1.47: la misma noticia (CuerpoEs igual o muy parecido) en un
solo grupo ANTES de clasificar, también con agrupación estricta."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
import analyzer_tono_tema as az
import perfil_cliente

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


def test_misma_noticia_reescrita_queda_en_un_grupo():
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


def test_noticias_distintas_con_el_mismo_pie_no_se_unen():
    pie = (' Pulzo.com se escribe con Z. El artículo fue curado por el equipo de Portal Bogotá y '
           'revisado por la mesa de edición antes de su publicación en la sección local.')
    rows = [_fila('Bogotá declara Alerta Fase 1 por calidad del aire en el suroccidente',
                  ALERTA + pie, 'Pulzo'),
            _fila('Metro de Bogotá llega a 20 kilómetros de viaducto', METRO + pie, 'Pulzo')]
    grupos, mapa = az.construir_grupos(rows, KM, estricta=True)
    grupos, mapa, n = az.fusionar_grupos_por_noticia(rows, grupos, mapa, KM, B, A, V)
    assert n == 0 and mapa[0] != mapa[1]


def test_sin_grupos_que_fusionar():
    rows = [_fila('Metro de Bogotá llega a 20 kilómetros de viaducto', METRO, 'C')]
    grupos, mapa = az.construir_grupos(rows, KM, estricta=True)
    g2, m2, n = az.fusionar_grupos_por_noticia(rows, grupos, mapa, KM, B, A, V)
    assert n == 0 and m2 == mapa

