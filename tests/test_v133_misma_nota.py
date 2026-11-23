"""v1.33: la misma nota en impreso y web del mismo medio (caso real del
2026-11-22: Vanguardia / Vanguardia.com, IDs 62096722 y 62091032) lleva el
mismo subtema, aunque cambien el titular y el arranque del texto."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_s = types.ModuleType('openai'); _s.OpenAI = object; sys.modules.setdefault('openai', _s)
import analyzer_tono_tema as az

KM = {'titulo': 'Título', 'resumen': 'Resumen - Aclaracion', 'medio': 'Medio'}
PASAJE = ('En Colombia, organismos como la Defensoría del Pueblo advierten que alrededor de 2,5 '
          'millones de personas sufren problemas constantes asociados a la depresión. Lo más '
          'alarmante de la situación es que apenas 2 de cada 10 pacientes reciben atención y '
          'tratamiento adecuados.')


def _f(medio, tit, sub, ctx):
    return {'Medio': medio, 'Título': tit, 'Subtema_IA': sub, 'Contexto analizado': ctx}


def test_impreso_y_web_del_mismo_medio():
    rows = [_f('Vanguardia', 'Alerta por cifras de salud mental', 'Estadísticas nacionales de salud mental',
               'Sin embargo, el consumo de alcohol y la violencia doméstica inciden. ' + PASAJE),
            _f('Vanguardia.com', 'Así fortalece Santander los primeros auxilios ante complejo panorama de salud mental',
               'Cifras de salud mental en Colombia',
               'Publicidad ' + PASAJE + ' A través de Hablemos Santandereana-mente se canalizan casos.')]
    assert az.unificar_misma_nota_final(rows, KM) == 1
    assert rows[0]['Subtema_IA'] == rows[1]['Subtema_IA']


def test_familia_de_medio():
    assert az.familia_medio('Vanguardia') == az.familia_medio('Vanguardia.com') == \
        az.familia_medio('www.vanguardia.com')
    assert az.familia_medio('El Tiempo') == az.familia_medio('Eltiempo.com')
    assert az.familia_medio('Caracol Radio') != az.familia_medio('Blu Radio')


def test_frase_corta_compartida_no_une():
    # Una cifra citada igual en dos notas distintas de medios distintos no basta.
    frase = 'La Defensoría del Pueblo emitió 30 alertas tempranas este año en el país.'
    rows = [_f('El Tiempo', 'Crisis en el Catatumbo', 'Crisis humanitaria en el Catatumbo',
               'Desplazamiento masivo en Tibú. ' + frase),
            _f('Semana', 'Minería ilegal en el Chocó', 'Minería ilegal en el Chocó',
               'Comunidades del Atrato denuncian dragas. ' + frase)]
    assert az.unificar_misma_nota_final(rows, KM) == 0
    # Del mismo medio tampoco, si solo comparten esa frase corta.
    rows[1]['Medio'] = 'Eltiempo.com'
    assert az.unificar_misma_nota_final(rows, KM) == 0


def test_misma_nota_reproducida_por_otro_medio():
    rows = [_f('Vanguardia.com', 'Panorama de salud mental en Santander', 'Cifras de salud mental en Colombia',
               'Publicidad ' + PASAJE),
            _f('Co.headtopics.com', 'Santander registra 36 intentos de suicidio semanales',
               'Línea gratuita de atención en Santander',
               'Sin embargo, condiciones como los excesos en el uso de sustancias psicoactivas y el '
               'consumo de alcohol inciden en dichas estadísticas sobre intentos de suicidio. ' + PASAJE)]
    assert az.unificar_misma_nota_final(rows, KM) == 1


def test_rotulo_fijo_manda():
    rows = [_f('Vanguardia', 'Alerta por cifras', 'Alertas Defensoría del Pueblo', PASAJE),
            _f('Vanguardia.com', 'Cifras de salud mental', 'Cifras de salud mental en Colombia', PASAJE)]
    az.unificar_misma_nota_final(rows, KM, fijos=['Alertas Defensoría del Pueblo'])
    assert {r['Subtema_IA'] for r in rows} == {'Alertas Defensoría del Pueblo'}
