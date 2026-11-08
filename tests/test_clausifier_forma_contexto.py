"""Clausifier 2026-11-07: forma del subtema en modo Haiku y Contexto >= 403."""
import asyncio
import json

import analyzer_tono_tema as a
import motor_haiku as mh


class TestVerboInicial:
    def test_rechaza_verbos(self):
        for s in ("querían volver a la guerra", "Anuncia nuevas medidas", "Realizando jornada",
                  "Denunció amenazas contra líderes", "Volver a la mesa de diálogo"):
            assert mh.empieza_con_verbo(s), s

    def test_acepta_nominales(self):
        for s in ("Riesgo de reactivación del conflicto armado",
                  "Bienestar de la población migrante", "Taller de liderazgo juvenil",
                  "Mujer víctima de violencia en Cali", "Poder judicial y reforma",
                  "Ejecución del presupuesto de salud"):
            assert not mh.empieza_con_verbo(s), s

    def test_problemas_palabras_y_verbo(self):
        assert mh.problemas_subtema("querían volver a la guerra")
        assert mh.problemas_subtema("Alerta")
        assert mh.problemas_subtema("uno dos tres cuatro cinco seis siete ocho")
        assert mh.problemas_subtema("Riesgo de reactivación del conflicto armado",
                                    "Disidencias dicen que querían volver a la guerra") == []


class TestAntiCopia:
    def test_casi_titulo(self):
        tit = "Defensoría alerta por reclutamiento de menores en el Cauca"
        assert mh.es_copia("Alerta por reclutamiento de menores en el Cauca", tit)

    def test_extracto_contexto(self):
        ctx = "Según la entidad, los excombatientes querían volver a la guerra en la región."
        assert mh.es_copia("Excombatientes querían volver a la guerra", "", ctx)

    def test_reformulacion_no_es_copia(self):
        assert mh.es_copia("Riesgo de reactivación del conflicto armado",
                           "Querían volver a la guerra, dice la Defensoría") is None


class TestGuardarRechaza:
    def test_rechazo_y_tope(self):
        mh.preparar_estado([{"id": "1", "titulo": "Querían volver a la guerra",
                             "contexto": "La Defensoría dijo que querían volver a la guerra."}],
                           incluir_tema=False)
        malo = json.dumps([{"id": "1", "subtema": "querían volver a la guerra", "tono": "Neutro"}])
        r1 = asyncio.run(mh._guardar_clasificacion({"filas_json": malo}))
        assert r1.get("is_error") and "RECHAZADOS" in r1["content"][0]["text"]
        assert "1" not in mh._ESTADO["res"]
        asyncio.run(mh._guardar_clasificacion({"filas_json": malo}))
        r3 = asyncio.run(mh._guardar_clasificacion({"filas_json": malo}))
        assert not r3.get("is_error")
        assert mh._ESTADO["res"]["1"]["subtema"] == "querían volver a la guerra"
        assert "1" in mh._ESTADO["marcados"]

    def test_bueno_se_guarda(self):
        mh.preparar_estado([{"id": "1", "titulo": "Querían volver a la guerra",
                             "contexto": "x"}], incluir_tema=False)
        bueno = json.dumps([{"id": "1", "subtema": "Riesgo de reactivación del conflicto armado",
                             "tono": "Neutro"}])
        r = asyncio.run(mh._guardar_clasificacion({"filas_json": bueno}))
        assert not r.get("is_error")
        assert mh._ESTADO["res"]["1"]["subtema"].startswith("Riesgo")


class TestGuardaFinal:
    def test_repara_verbo_y_copia_con_respaldo(self):
        rows = [{"Título": "Quieren volver a sembrar coca, advierten en Putumayo",
                 "Subtema_IA": "Quieren volver a sembrar coca"},
                {"Título": "Taller de liderazgo juvenil reunió a 200 jóvenes",
                 "Subtema_IA": "Taller de liderazgo juvenil en Quibdó"}]
        r = a.guarda_final_forma_subtema(rows, {}, {0: "Riesgo de retorno a cultivos de coca"})
        assert not mh.empieza_con_verbo(rows[0]["Subtema_IA"])
        assert rows[1]["Subtema_IA"] == "Taller de liderazgo juvenil en Quibdó"
        assert r["sobrevivientes"] == []


CUERPO = ("El municipio vivió una semana tensa por los combates en la zona rural. "
          "Las comunidades campesinas reportaron desplazamientos hacia la cabecera municipal. "
          "La Defensoría del Pueblo pidió garantías para la población civil. "
          "Los líderes sociales pidieron presencia del Estado en las veredas afectadas. "
          "La gobernación anunció un consejo de seguridad para el próximo lunes. "
          "El Ejército desplegó tropas adicionales en el corredor del río.")


class TestContexto403:
    def test_extiende_con_oraciones_literales(self):
        ctx = "La Defensoría del Pueblo pidió garantías para la población civil."
        out = a._contexto_con_titulo_y_extension(ctx, "Combates en zona rural", CUERPO,
                                                 "Defensoría del Pueblo", [], [])
        assert len(out) >= 403
        assert ctx in out
        assert "reportaron desplazamientos hacia la cabecera municipal." in out
        assert "Los líderes sociales pidieron presencia del Estado" in out
        # orden original del cuerpo
        assert out.index("Las comunidades") < out.index(ctx) < out.index("Los líderes")
        assert out.count(ctx) == 1

    def test_cuerpo_corto_da_cuerpo_entero(self):
        cuerpo = "La Defensoría del Pueblo visitó la zona. Hubo reuniones con líderes."
        out = a._contexto_con_titulo_y_extension("La Defensoría del Pueblo visitó la zona.",
                                                 "Visita", cuerpo, "Defensoría del Pueblo",
                                                 [], [])
        assert out == cuerpo

    def test_texto_es_contexto_sin_titulo(self):
        tit = "Defensoría del Pueblo pide garantías"
        out = a._contexto_con_titulo_y_extension(
            "La Defensoría del Pueblo pidió garantías para la población civil.", tit, CUERPO,
            "Defensoría del Pueblo", [], [])
        assert out.startswith(tit)
        texto = a._texto_desde_contexto(out, tit)
        assert texto == out[len(tit):].strip()

    def test_len_igual_largo_excel_con_tildes(self):
        s = "Acción, educación y niñez: ¿qué pasó?"
        assert len(s) == 37  # LARGO() cuenta caracteres, no bytes
