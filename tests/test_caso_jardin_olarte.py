# -*- coding: utf-8 -*-
"""Caso real 2026-11-09 (dossier de la Secretaría General revisado a mano).

- Nunca se agrupa porque sí: el pie fijo de un medio («Pulzo.com se escribe
  con Z») no es evidencia de «misma noticia».
- El nombre propio de un programa es un subtema válido y no se «repara».
- Notas de una figura ajena (perfil: tono_neutro_titulo) y titulares
  electorales son Neutro.
- Con TIPO/GÉNERO activos, una fila sin esos campos se rechaza.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyzer_tono_tema as az  # noqa: E402
import motor_haiku as mh  # noqa: E402

PIE = ('\n\n* Este artículo fue curado con apoyo de inteligencia artificial.\n\n'
       '* Pulzo.com se escribe con Z y es un medio digital de noticias de Colombia para todo público.')
JARDIN = ('La Alcaldía Mayor de Bogotá entregó la transformación de 1.812 metros cuadrados en el '
          'entorno del Jardín Infantil Olarte, en la localidad de Bosa, para mejorar la calidad del '
          'aire que respiran 157 niñas y niños menores de cinco años y cerca de 3.000 vecinos.')
INSPECTORES = ('La Secretaría Distrital de Gobierno realizó una jornada de capacitación para el '
               'personal de planta de las Inspecciones de Convivencia y Paz de Bogotá, con '
               'herramientas actualizadas para responder a los nuevos desafíos de la ciudad.')


def _rows():
    return [
        {'Título': 'Bogotá transforma entorno del Jardín Infantil Olarte en Bosa', 'Medio': 'Pulzo (Online)',
         'CuerpoEs': JARDIN + PIE, 'Contexto analizado': JARDIN},
        {'Título': 'Alcaldía de Bogotá refuerza capacitación para inspectoras e inspectores',
         'Medio': 'Pulzo (Online)', 'CuerpoEs': INSPECTORES + PIE, 'Contexto analizado': INSPECTORES},
        {'Título': 'Bogotá transforma el entorno del Jardín Infantil Olarte, en Bosa, por un aire más limpio',
         'Medio': 'Bogotá.gov (Online)', 'CuerpoEs': JARDIN + ' Las obras incluyeron siembra de árboles.',
         'Contexto analizado': JARDIN},
    ]


def test_pie_del_medio_no_une_noticias_distintas():
    rows = _rows()
    mapa = {0: 1, 1: 2, 2: 3}
    comps = az.noticias_por_evidencia(rows, mapa, {1: {}, 2: {}, 3: {}}, {'titulo': 'Título'},
                                      brand='Alcaldía de Bogotá')
    juntos = [sorted(c) for c in comps]
    assert [1] in juntos                                  # la de inspectores queda sola
    assert not any(1 in c and len(c) > 1 for c in juntos)


def test_unificar_por_noticia_no_impone_subtema_ajeno():
    rows = _rows()
    subs = ['Transformación del entorno del Jardín Olarte', 'Inspecciones de Convivencia y Paz',
            'Transformación del entorno del Jardín Olarte']
    for r, s in zip(rows, subs):
        r.update({'Subtema_IA': s, 'Tono_IA': 'Positivo'})
    mapa = {0: 1, 1: 2, 2: 3}
    etiquetas = {g: {'sub_tema': subs[g - 1], 'tono': 'Positivo'} for g in (1, 2, 3)}
    az.unificar_por_noticia(rows, mapa, etiquetas, {'titulo': 'Título'},
                            originales={g: subs[g - 1] for g in (1, 2, 3)},
                            brand='Alcaldía de Bogotá')
    assert rows[1]['Subtema_IA'] == 'Inspecciones de Convivencia y Paz'
    assert rows[0]['Subtema_IA'] == rows[2]['Subtema_IA'] == 'Transformación del entorno del Jardín Olarte'


def test_nombre_propio_de_programa_es_subtema_valido():
    for s, tit in (('Inspecciones de Convivencia y Paz',
                    'Alcaldía refuerza capacitación para inspectores de Inspecciones de Convivencia y Paz'),
                   ('Plan Motos y Vehículos en Engativá', 'Cinco motos y tres carros robados fueron recuperados'),
                   ('Operación Kratos', 'Capturan a Los Nocturnos'), ('Jóvenes a la E', 'Alcaldía anuncia becas')):
        assert mh.problemas_subtema(s, tit, '') == [], s
        assert 'subtema_sin_hecho' not in [x.split('(')[0] for x in az.validar(s, 'Neutro', [tit])], s
    # Lo demás sigue igual: verbo inicial y extracto se rechazan.
    assert mh.problemas_subtema('Anuncia nuevas medidas de seguridad', '', '')
    assert mh.problemas_subtema('capturan a los nocturnos en soacha', 'Capturan a Los Nocturnos en Soacha', '')


def test_notas_de_figura_ajena_y_electorales_son_neutro():
    if not hasattr(az, 'aplicar_regla_neutro_titulo'):
        return                                   # regla del perfil de Secretaría
    grupos = [{'grupo': 1, 'titulo': '¿Uribe ve a Petro candidato a la Alcaldía de Bogotá?'},
              {'grupo': 2, 'titulo': 'Petro critica al alcalde Galán por el metro'},
              {'grupo': 3, 'titulo': 'Alcaldía de Bogotá entrega colegio en Bosa'},
              {'grupo': 4, 'titulo': 'Estos son los candidatos a la Alcaldía de Bogotá'}]
    et = {1: {'tono': 'Positivo'}, 2: {'tono': 'Negativo'}, 3: {'tono': 'Positivo'}, 4: {'tono': 'Positivo'}}
    cambiados = az.aplicar_regla_neutro_titulo(grupos, et, ['Petro'],
                                               voceros=['Carlos Fernando Galán', 'Galán', 'el alcalde'],
                                               electoral=True)
    assert sorted(cambiados) == [1, 4]
    assert et[1]['tono'] == 'Neutro' and et[1]['flags']['neutro_pegajoso']
    assert et[2]['tono'] == 'Negativo'          # el titular nombra al alcalde: se respeta
    assert et[3]['tono'] == 'Positivo'
    # Sin la lista del perfil y sin el modo electoral no cambia nada.
    et2 = {1: {'tono': 'Positivo'}}
    assert az.aplicar_regla_neutro_titulo(grupos[:1], et2, [], electoral=False) == []


def test_fila_sin_tipo_o_genero_se_rechaza():
    if not hasattr(mh, 'TIPOS'):
        return                                   # app sin TIPO/GÉNERO
    its = [{'id': '1', 'titulo': 'Bogotá ofrece vacantes', 'contexto': 'La Alcaldía de Bogotá ofrece vacantes.'}]
    mh.preparar_estado(its, False, con_tipo_genero=True)
    fila = {'id': '1', 'subtema': 'Oferta semanal de vacantes distritales', 'tono': 'Positivo', 'rol': 'gestion'}
    r = asyncio.run(mh._guardar_clasificacion({'filas_json': json.dumps([fila])}))
    assert r.get('is_error') and 'falta tipo y genero' in r['content'][0]['text']
    assert '1' not in mh._ESTADO['res']
    fila.update(tipo='Programas y proyectos', genero='Noticia')
    r = asyncio.run(mh._guardar_clasificacion({'filas_json': json.dumps([fila])}))
    assert not r.get('is_error') and mh._ESTADO['res']['1']['tipo'] == 'Programas y proyectos'
    # Una corrección posterior sin tipo/genero conserva los guardados.
    r = asyncio.run(mh._guardar_clasificacion({'filas_json': json.dumps(
        [{'id': '1', 'subtema': 'Oferta semanal de vacantes del Distrito', 'tono': 'Positivo'}])}))
    assert not r.get('is_error') and mh._ESTADO['res']['1']['genero'] == 'Noticia'


def test_tipo_gestion_de_programa_no_es_institucional():
    if not hasattr(az, '_GESTION_PROGRAMA_PAT'):
        return                                   # app sin TIPO/GÉNERO
    B, A, V = 'Secretaría General de la Alcaldía de Bogotá', ['Alcaldía de Bogotá', 'Alcaldía Mayor de Bogotá'], ['Carlos Fernando Galán']
    grupos = [{'grupo': 1, 'titulo': 'Bogotá ofrece 2.946 vacantes laborales esta semana',
               'contexto': 'La Alcaldía Mayor de Bogotá ofrece 2.946 vacantes en tres ferias de empleo.'},
              {'grupo': 2, 'titulo': 'Galán habló de la militarización',
               'contexto': 'El alcalde Carlos Fernando Galán dijo que no comparte la propuesta.'}]
    et = {1: {'tipo': 'Información general'}, 2: {'tipo': 'Información general'}}
    az.aplicar_tipo_por_actor(grupos, et, B, A, voceros=V)
    assert et[1]['tipo'] == 'Proyectos y programas'
    assert et[2]['tipo'] == 'Institucional'
